#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OpenList 影视资源智能重命名工具（GUI 版 v2）
============================================
流程改进：你输入真实影视名 → TMDB 匹配官方名+年份 → 工具解析文件名里的集数 → 批量重命名。

工作流程：
  1. 连接 OpenList
  2. 进入某部影视所在目录（该目录下是这部剧的各个集文件或文件夹）
  3. 输入框填真实影视名（如"狂飙"）→ 点"匹配" → 下拉选择正确的结果（含年份区分同名作品）
  4. 点"分析并生成改名方案" → 列表显示：原名 → 新名（剧名 SxxExx）
  5. 勾选 → 执行重命名

打包：pyinstaller -F -w -n "OpenList影视重命名" openlist_renamer_gui.py
"""

import json
import re
import sys
import difflib
import threading
import urllib.request
import urllib.parse
import urllib.error
import tkinter as tk
from tkinter import ttk, messagebox

# ============ 配置 ============
VIDEO_EXTS = {'.mp4', '.mkv', '.avi', '.ts', '.m2ts', '.mov', '.wmv', '.rmvb', '.flv', '.webm', '.m4v'}
TMDB_BASE = 'https://api.themoviedb.org/3'
TMDB_KEY = 'fe717bbe0351637ab4a8cd6f7c754686'  # 用户 Key（可改）

IMPURITY_PATTERNS = [
    r'\[[^\]]*字幕[^\]]*\]',
    r'www\.[a-z0-9\-\.]+\.[a-z]{2,}',
    r'https?://\S+',
    r'@[a-zA-Z0-9_]{2,}',
    r'(?i)\b(2160p|1080p|720p|480p|4k|8k|uhd|hd|hdr10|hdr|sdr|dovi|dv)\b',
    r'(?i)\b(x264|x265|h\.264|h\.265|hevc|avc|av1|mpeg4|divx|vp9)\b',
    r'(?i)\b(bluray|web-?dl|web-?rip|hdrip|bdrip|dvdrip|remux|bd|dvd|web)\b',
    r'(?i)\b(dts|ac3|aac|flac|truehd|dd5\.1|5\.1|7\.1|2audio|dual)\b',
    r'(?i)\b(atmos|vision|10bit|8bit|h265|h264)\b',
]

EPISODE_PATTERNS = [
    (r'[Ss](\d{1,2})[Ee](\d{1,3})', 'sxxexx'),
    (r'第(\d{1,2})季[^\d]{0,4}第(\d{1,3})[集話话]', 'cn_ss'),
    (r'第(\d{1,3})[集話话]', 'cn_ep'),
    (r'[Ee][Pp]?\.?(\d{1,3})', 'ep'),
    (r'(?i)episode\s*\.?\s*(\d{1,3})', 'episode'),
    # 纯数字开头 + 扩展名（如 "01.mp4"、"02.mkv"）— 优先匹配
    (r'^\s*(\d{1,3})\s*\.(?:mp4|mkv|avi|ts|m2ts|mov|m4v|webm|flv|rmvb|wmv)$', 'pure_num'),
    # 前面有分隔符的数字（"狂飙 01.mp4"、"狂飙.01"）— 排除年份/纯片名数字误判
    (r'(?<![0-9])[-\s_\.](\d{1,2})(?![0-9])(?:[-\s_\.]|$)', 'num'),
]


# ============ 集数解析（复用已验证逻辑）============
def extract_episode(name):
    """返回 (season, episode)，没有则 (None, None)"""
    for pat, kind in EPISODE_PATTERNS:
        m = re.search(pat, name)
        if not m:
            continue
        if kind in ('sxxexx', 'cn_ss'):
            return int(m.group(1)), int(m.group(2))
        return 1, int(m.group(1))
    return None, None


def clean_episode_part(name):
    """去掉集数标记后的剩余（用于识别无集数的文件）"""
    s = re.sub(r'[Ss]\d{1,2}[Ee]\d{1,3}', '', name)
    s = re.sub(r'第\d{1,3}[集話话]', '', s)
    s = re.sub(r'第\d{1,2}季', '', s)
    s = re.sub(r'(?i)episode\s*\.?\s*\d{1,3}', '', s)
    s = re.sub(r'(?i)[Ee][Pp]?\.?\s*\d{1,3}', '', s)
    s = re.sub(r'[-\s_\.](\d{1,2})(?:[-\s_\.]|$)', '', s)
    return s


class TMDB:
    def __init__(self, api_key):
        self.api_key = api_key
        self.cache = {}

    def _req(self, path, params):
        params['api_key'] = self.api_key
        params['language'] = params.get('language', 'zh-CN')
        url = TMDB_BASE + path + '?' + urllib.parse.urlencode(params)
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 OpenListRenamer/2.0'})
            with urllib.request.urlopen(req, timeout=15) as r:
                return json.loads(r.read().decode('utf-8'))
        except Exception:
            return None

    def search_all(self, query):
        """同时搜 tv 和 movie，合并结果"""
        results = []
        seen = set()
        for mt in ('tv', 'movie'):
            data = self._req(f'/search/{mt}', {'query': query})
            for c in (data or {}).get('results', []):
                c['_media_type'] = mt
                key = c['id']
                if key not in seen:
                    seen.add(key)
                    results.append(c)
        return results


class OpenList:
    def __init__(self, base, username, password):
        self.base = base.rstrip('/')
        self.username = username
        self.password = password
        self.token = None

    def _req(self, path, data=None):
        headers = {'User-Agent': 'Mozilla/5.0 OpenListRenamer/2.0', 'Content-Type': 'application/json'}
        if self.token:
            headers['Authorization'] = self.token
        body = json.dumps(data).encode() if data is not None else None
        req = urllib.request.Request(self.base + path, data=body, headers=headers,
                                     method='POST' if data is not None else 'GET')
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read().decode('utf-8', errors='replace'))

    def login(self):
        d = self._req('/api/auth/login', {'username': self.username, 'password': self.password})
        if d.get('code') == 200 and d.get('data', {}).get('token'):
            self.token = d['data']['token']
            return True
        return False

    def list_dir(self, path):
        d = self._req('/api/fs/list', {'path': path, 'page': 1, 'per_page': 500, 'refresh': False})
        if d.get('code') == 200:
            return d['data'].get('content', [])
        raise Exception(d.get('message', 'list failed'))

    def rename(self, path, name):
        d = self._req('/api/fs/rename', {'path': path, 'name': name})
        return d.get('code') == 200


class App:
    def __init__(self, root):
        self.root = root
        self.root.title('OpenList 影视资源智能重命名')
        self.root.geometry('980x720')
        self.ol = None
        self.tmdb = TMDB(TMDB_KEY)
        self.current_path = '/'
        self.current_items = []
        self.plans = []
        self.search_results = []
        self.selected_media = None  # 用户选定的 TMDB 结果

        style = ttk.Style()
        try:
            style.theme_use('vista')
        except Exception:
            pass

        self._build_ui()

    def _build_ui(self):
        # ---- 连接区 ----
        conn = ttk.LabelFrame(self.root, text='OpenList 连接', padding=8)
        conn.pack(fill='x', padx=8, pady=6)

        ttk.Label(conn, text='地址:').grid(row=0, column=0, sticky='e')
        self.var_base = tk.StringVar(value='http://10.10.10.1:5445')
        ttk.Entry(conn, textvariable=self.var_base, width=32).grid(row=0, column=1, padx=4)

        ttk.Label(conn, text='账号:').grid(row=0, column=2, sticky='e')
        self.var_user = tk.StringVar(value='admin')
        ttk.Entry(conn, textvariable=self.var_user, width=10).grid(row=0, column=3, padx=4)

        ttk.Label(conn, text='密码:').grid(row=0, column=4, sticky='e')
        self.var_pass = tk.StringVar(value='admin')
        ttk.Entry(conn, textvariable=self.var_pass, width=10, show='*').grid(row=0, column=5, padx=4)

        ttk.Button(conn, text='连接', command=self.do_login).grid(row=0, column=6, padx=6)
        ttk.Button(conn, text='刷新', command=self.do_refresh).grid(row=0, column=7)

        # ---- 目录导航 ----
        nav = ttk.LabelFrame(self.root, text='目录（进入某部影视所在的文件夹）', padding=8)
        nav.pack(fill='x', padx=8, pady=4)
        self.var_path = tk.StringVar(value='/')
        ttk.Entry(nav, textvariable=self.var_path).pack(side='left', fill='x', expand=True, padx=4)
        ttk.Button(nav, text='进入', command=self.do_enter).pack(side='left')
        ttk.Button(nav, text='上一级', command=self.do_up).pack(side='left', padx=4)
        ttk.Button(nav, text='根目录', command=self.do_root).pack(side='left')

        # ---- 影视名匹配区（核心新功能）----
        match = ttk.LabelFrame(self.root, text='真实影视名匹配（输入你记录的剧名/片名）', padding=8)
        match.pack(fill='x', padx=8, pady=4)

        ttk.Label(match, text='影视名:').grid(row=0, column=0, sticky='e')
        self.var_title = tk.StringVar()
        ttk.Entry(match, textvariable=self.var_title, width=28).grid(row=0, column=1, padx=4)
        ttk.Button(match, text='匹配 TMDB', command=self.do_search).grid(row=0, column=2, padx=4)

        ttk.Label(match, text='选择结果:').grid(row=0, column=3, sticky='e')
        self.var_result = tk.StringVar()
        self.cmb_result = ttk.Combobox(match, textvariable=self.var_result, width=44, state='readonly')
        self.cmb_result.grid(row=0, column=4, padx=4)
        self.cmb_result.bind('<<ComboboxSelected>>', self.on_result_selected)

        ttk.Button(match, text='生成改名方案', command=self.do_plan).grid(row=0, column=5, padx=6)

        # ---- 列表 ----
        list_frame = ttk.LabelFrame(self.root, text='改名方案（勾选要执行的）', padding=8)
        list_frame.pack(fill='both', expand=True, padx=8, pady=4)

        cols = ('old', 'new', 'info')
        self.tree = ttk.Treeview(list_frame, columns=cols, show='headings', selectmode='extended')
        self.tree.heading('old', text='原名')
        self.tree.heading('new', text='新名')
        self.tree.heading('info', text='说明')
        self.tree.column('old', width=300)
        self.tree.column('new', width=340)
        self.tree.column('info', width=200)
        self.tree.pack(fill='both', expand=True, side='left')

        scroll = ttk.Scrollbar(list_frame, orient='vertical', command=self.tree.yview)
        scroll.pack(side='right', fill='y')
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.bind('<Double-1>', self.on_double_click)

        # ---- 操作区 ----
        ops = ttk.Frame(self.root, padding=8)
        ops.pack(fill='x', padx=8)

        self.btn_apply = ttk.Button(ops, text='执行重命名（勾选项）', command=self.do_apply)
        self.btn_apply.pack(side='left', padx=4)
        ttk.Button(ops, text='全选', command=lambda: self.tree.selection_set(self.tree.get_children())).pack(side='left', padx=2)
        ttk.Button(ops, text='清空', command=lambda: self.tree.selection_remove(self.tree.get_children())).pack(side='left', padx=2)
        # 年份选项
        self.var_include_year = tk.BooleanVar(value=True)
        ttk.Checkbutton(ops, text='文件名含年份', variable=self.var_include_year).pack(side='left', padx=10)

        # ---- 日志 ----
        log_frame = ttk.LabelFrame(self.root, text='日志', padding=4)
        log_frame.pack(fill='x', padx=8, pady=4)
        self.log_text = tk.Text(log_frame, height=7, state='disabled')
        self.log_text.pack(fill='x')

    def log(self, msg):
        self.log_text.configure(state='normal')
        self.log_text.insert('end', msg + '\n')
        self.log_text.see('end')
        self.log_text.configure(state='disabled')
        self.root.update_idletasks()

    # ============ OpenList ============
    def do_login(self):
        base = self.var_base.get().strip()
        user = self.var_user.get().strip()
        pwd = self.var_pass.get()
        if not base:
            messagebox.showerror('错误', '请填写 OpenList 地址')
            return

        def work():
            try:
                self.log('连接 %s ...' % base)
                ol = OpenList(base, user, pwd)
                if not ol.login():
                    self.log('登录失败：账号或密码错误')
                    messagebox.showerror('登录失败', '账号或密码错误，或地址不可达')
                    return
                self.ol = ol
                self.log('登录成功 ✓')
                self.root.after(0, self.do_refresh)
            except Exception as e:
                self.log('连接失败: %s' % e)
                messagebox.showerror('连接失败', str(e))

        threading.Thread(target=work, daemon=True).start()

    def do_refresh(self):
        if not self.ol:
            messagebox.showwarning('提示', '请先连接')
            return
        self.load_path(self.var_path.get().strip() or '/')

    def do_enter(self):
        self.load_path(self.var_path.get().strip() or '/')

    def do_up(self):
        p = self.var_path.get().strip() or '/'
        if p == '/':
            return
        self.load_path(p.rsplit('/', 1)[0] or '/')

    def do_root(self):
        self.load_path('/')

    def load_path(self, path):
        def work():
            try:
                items = self.ol.list_dir(path)
                self.current_items = items
                self.current_path = path
                self.root.after(0, self.render_items, items)
                self.log('目录: %s （%d 项）' % (path, len(items)))
            except Exception as e:
                self.log('读取失败: %s' % e)

        threading.Thread(target=work, daemon=True).start()

    def render_items(self, items):
        self.var_path.set(self.current_path)
        self.tree.delete(*self.tree.get_children())
        self.plans = []
        for it in items:
            name = it.get('name', '')
            is_dir = it.get('is_dir', False)
            tag = '(目录)' if is_dir else '文件'
            self.tree.insert('', 'end', iid=name, values=(name, '', tag))

    def on_double_click(self, event):
        sel = self.tree.selection()
        if len(sel) != 1:
            return
        name = sel[0]
        for it in self.current_items:
            if it.get('name') == name and it.get('is_dir'):
                self.load_path(self.current_path.rstrip('/') + '/' + name)
                return

    # ============ TMDB 搜索 ============
    def do_search(self):
        query = self.var_title.get().strip()
        if not query:
            messagebox.showwarning('提示', '请输入真实影视名')
            return

        def work():
            self.log('搜索 TMDB: %s ...' % query)
            results = self.tmdb.search_all(query)
            self.search_results = results
            if not results:
                self.log('未找到匹配，换关键词试试')
                self.root.after(0, lambda: messagebox.showinfo('无结果', 'TMDB 未找到该影视，试试英文名或更精确的名字'))
                return
            labels = []
            for r in results:
                name = r.get('name') or r.get('title') or ''
                oname = r.get('original_name') or r.get('original_title') or ''
                yf = 'first_air_date' if r.get('_media_type') == 'tv' else 'release_date'
                year = (r.get(yf) or '')[:4]
                mt = '剧集' if r.get('_media_type') == 'tv' else '电影'
                labels.append('%s (%s) [%s]' % (name, year, mt))
            self.root.after(0, lambda: self.cmb_result.configure(values=labels))
            self.log('找到 %d 个结果，请选择正确的' % len(results))

        threading.Thread(target=work, daemon=True).start()

    def on_result_selected(self, event=None):
        idx = self.cmb_result.current()
        if 0 <= idx < len(self.search_results):
            r = self.search_results[idx]
            self.selected_media = {
                'id': r['id'],
                'name': r.get('name') or r.get('title') or '',
                'original_name': r.get('original_name') or r.get('original_title') or '',
                'year': (r.get('first_air_date') or r.get('release_date') or '')[:4],
                'media_type': r.get('_media_type'),
            }
            self.log('已选择: %s (%s) [%s]' % (self.selected_media['name'],
                                                self.selected_media['year'],
                                                '剧集' if self.selected_media['media_type'] == 'tv' else '电影'))

    # ============ 生成改名方案 ============
    def do_plan(self):
        if not self.ol:
            messagebox.showwarning('提示', '请先连接')
            return
        if not self.selected_media:
            messagebox.showwarning('提示', '请先在"选择结果"里选定正确的影视')
            return
        if not self.current_items:
            self.log('当前目录为空')
            return

        media = self.selected_media
        is_tv = media['media_type'] == 'tv'
        title = media['name'] or media['original_name']
        year = media.get('year') or ''
        include_year = self.var_include_year.get()

        def work():
            self.log('生成改名方案（%s %s）...' % (title, year))
            results = []
            for it in self.current_items:
                name = it.get('name', '')
                is_dir = it.get('is_dir', False)
                if not is_dir and '.' in name:
                    ext = '.' + name.rsplit('.', 1)[-1].lower()
                    if ext not in VIDEO_EXTS:
                        continue

                # 解析集数
                season, episode = extract_episode(name)
                new_name = None
                note = ''
                ytxt = ' (%s)' % year if (include_year and year) else ''
                if is_tv:
                    if season is not None and episode is not None:
                        if is_dir:
                            new_name = '%s%s' % (title, ytxt)
                        else:
                            ext = name.rsplit('.', 1)[-1] if '.' in name else ''
                            new_name = '%s%s S%02dE%02d.%s' % (title, ytxt, season, episode, ext)
                        note = '剧集 %d-%d' % (season, episode)
                    else:
                        note = '⚠ 无法解析集数'
                else:
                    if is_dir:
                        new_name = '%s%s' % (title, ytxt)
                    else:
                        ext = name.rsplit('.', 1)[-1] if '.' in name else ''
                        new_name = '%s%s.%s' % (title, ytxt, ext)
                    note = '电影'

                if new_name and new_name.lower() != name.lower():
                    results.append((name, new_name, note))
                else:
                    results.append((name, new_name or name, note or '无需修改'))

            self.root.after(0, self.render_plan, results)

        threading.Thread(target=work, daemon=True).start()

    def render_plan(self, results):
        self.tree.delete(*self.tree.get_children())
        self.plans = []
        for name, new_name, note in results:
            if new_name != name and '⚠' not in note:
                self.tree.insert('', 'end', iid=name, values=(name, new_name, note))
                self.tree.selection_add(name)
                self.plans.append({
                    'src_dir': self.current_path,
                    'src_name': name,
                    'new_name': new_name,
                })
            else:
                self.tree.insert('', 'end', iid=name, values=(name, '', note))
        self.log('生成完成：%d 项可重命名' % len(self.plans))

    # ============ 执行 ============
    def do_apply(self):
        if not self.ol:
            messagebox.showwarning('提示', '请先连接')
            return
        selected = self.tree.selection()
        if not selected:
            messagebox.showinfo('提示', '没有勾选任何项目')
            return
        renames = [p for p in self.plans if p['src_name'] in selected]
        if not renames:
            messagebox.showinfo('提示', '勾选的项目没有可重命名的')
            return

        if not messagebox.askyesno('确认', '确定重命名 %d 个资源吗？\n\n该操作会直接修改网盘里的文件名！' % len(renames)):
            return

        def work():
            ok_count = 0
            for p in renames:
                full = p['src_dir'].rstrip('/') + '/' + p['src_name']
                try:
                    if self.ol.rename(full, p['new_name']):
                        ok_count += 1
                        self.log('✓ %s → %s' % (p['src_name'], p['new_name']))
                    else:
                        self.log('✗ 失败: %s' % p['src_name'])
                except Exception as e:
                    self.log('✗ %s: %s' % (p['src_name'], e))
            self.log('完成：成功 %d/%d' % (ok_count, len(renames)))
            if ok_count:
                self.root.after(0, self.do_refresh)

        threading.Thread(target=work, daemon=True).start()


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == '__main__':
    main()
