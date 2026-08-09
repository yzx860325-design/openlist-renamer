#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OpenList 影视资源智能重命名 — 核心逻辑模块
===========================================
供 GUI / CLI / Web 三端复用：
  - 集数解析（第X集 / SxxExx / EPx / Episode / 纯数字）
  - 文件名杂质清理
  - TMDB 搜索匹配
  - OpenList API（Alist 兼容）
"""

import json
import re
import urllib.request
import urllib.parse
import urllib.error

# ============ 配置 ============
VIDEO_EXTS = {'.mp4', '.mkv', '.avi', '.ts', '.m2ts', '.mov', '.wmv', '.rmvb', '.flv', '.webm', '.m4v'}
TMDB_BASE = 'https://api.themoviedb.org/3'
TMDB_KEY = 'fe717bbe0351637ab4a8cd6f7c754686'  # 默认 Key，可用环境变量 TMDB_KEY 覆盖

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
    r'(?i)\b(国语|粤语|中字|中文字幕|双语|简繁|繁中|简中)\b',
]

EPISODE_PATTERNS = [
    (r'[Ss](\d{1,2})[Ee](\d{1,3})', 'sxxexx'),
    (r'第(\d{1,2})季[^\d]{0,4}第(\d{1,3})[集話话]', 'cn_ss'),
    (r'第(\d{1,3})[集話话]', 'cn_ep'),
    (r'[Ee][Pp]?\.?(\d{1,3})', 'ep'),
    (r'(?i)episode\s*\.?\s*(\d{1,3})', 'episode'),
    (r'^\s*(\d{1,3})\s*\.(?:mp4|mkv|avi|ts|m2ts|mov|m4v|webm|flv|rmvb|wmv)$', 'pure_num'),
    (r'(?<![0-9])[-\s_\.](\d{1,2})(?![0-9])(?:[-\s_\.]|$)', 'num'),
]


# ============ 集数解析 ============
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


def is_video(name):
    """是否视频文件"""
    if '.' not in name:
        return False
    return name.rsplit('.', 1)[-1].lower() in {e.lstrip('.') for e in VIDEO_EXTS}


# ============ TMDB ============
class TMDB:
    def __init__(self, api_key=None):
        self.api_key = api_key or TMDB_KEY
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

    def build_plan(self, item, media, include_year=True):
        """
        根据 TMDB 匹配结果和目录项，生成改名方案。
        item: {name, is_dir}
        media: {name, original_name, year, media_type}
        返回 (new_name, note)；无法处理返回 (None, note)
        """
        name = item.get('name', '')
        is_dir = item.get('is_dir', False)

        if not is_dir and not is_video(name):
            return None, '非视频文件'

        is_tv = media.get('media_type') == 'tv'
        title = media.get('name') or media.get('original_name') or ''
        year = media.get('year') or ''
        ytxt = ' (%s)' % year if (include_year and year) else ''

        season, episode = extract_episode(name)
        ext = name.rsplit('.', 1)[-1] if '.' in name else ''

        if is_tv:
            if season is None or episode is None:
                return None, '剧集但无法解析集数'
            if is_dir:
                return '%s%s' % (title, ytxt), '剧集文件夹'
            return '%s%s S%02dE%02d.%s' % (title, ytxt, season, episode, ext), '剧集 %d-%d' % (season, episode)
        else:
            if is_dir:
                return '%s%s' % (title, ytxt), '电影文件夹'
            return '%s%s.%s' % (title, ytxt, ext), '电影'


# ============ OpenList（Alist 兼容 API）============
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

    def batch_rename(self, items):
        """items: [(full_path, new_name), ...] → (ok_count, results)"""
        ok = 0
        results = []
        for path, new_name in items:
            try:
                if self.rename(path, new_name):
                    ok += 1
                    results.append((path, new_name, True, ''))
                else:
                    results.append((path, new_name, False, 'API 返回失败'))
            except Exception as e:
                results.append((path, new_name, False, str(e)))
        return ok, results


# ============ 本地文件系统（NAS 挂载卷 / 电脑本地）============
import os as _os
import shutil as _shutil

IMG_BASE = 'https://image.tmdb.org/t/p/'


class LocalFS:
    """
    本地/挂载卷文件系统适配器。
    安全设计：所有路径必须位于 root 下（防目录穿越）。
    """

    def __init__(self, root='/media'):
        self.root = _os.path.abspath(root)

    def _resolve(self, path):
        """把逻辑路径（/xxx）解析为物理路径，校验在 root 内"""
        if not path:
            path = '/'
        # 去掉前导斜杠
        rel = path.lstrip('/')
        full = _os.path.join(self.root, rel)
        full = _os.path.abspath(full)
        if not (full == self.root or full.startswith(self.root + _os.sep)):
            raise Exception('路径越界: %s' % path)
        return full

    def _to_logic(self, full):
        """物理路径转逻辑路径"""
        full = _os.path.abspath(full)
        rel = full[len(self.root):].lstrip(_os.sep).replace(_os.sep, '/')
        return '/' + rel if rel else '/'

    def list_dir(self, path):
        full = self._resolve(path)
        if not _os.path.isdir(full):
            raise Exception('不是目录: %s' % path)
        items = []
        try:
            entries = sorted(_os.listdir(full), key=str.lower)
        except PermissionError:
            raise Exception('无权限读取: %s' % path)
        for name in entries:
            p = _os.path.join(full, name)
            try:
                st = _os.stat(p)
                items.append({'name': name,
                              'is_dir': _os.path.isdir(p),
                              'size': st.st_size if not _os.path.isdir(p) else 0})
            except Exception:
                continue
        return items

    def rename(self, path, name):
        """重命名文件/目录；name 不含路径分隔符"""
        if '/' in name or '\\' in name:
            raise Exception('新名称不能包含路径分隔符')
        full = self._resolve(path)
        parent = _os.path.dirname(full)
        new_full = _os.path.join(parent, name)
        if not _os.path.exists(full):
            raise Exception('源不存在: %s' % path)
        if _os.path.exists(new_full) and _os.path.abspath(new_full) != full:
            raise Exception('目标已存在: %s' % name)
        _os.rename(full, new_full)
        return True

    def batch_rename(self, items):
        ok = 0
        results = []
        for path, new_name in items:
            try:
                if self.rename(path, new_name):
                    ok += 1
                    results.append((path, new_name, True, ''))
                else:
                    results.append((path, new_name, False, '重命名失败'))
            except Exception as e:
                results.append((path, new_name, False, str(e)))
        return ok, results

    def write_file(self, path, data, binary=False):
        """在挂载卷内写文件（如 NFO / 海报）"""
        full = self._resolve(path)
        parent = _os.path.dirname(full)
        _os.makedirs(parent, exist_ok=True)
        mode = 'wb' if binary else 'w'
        kwargs = {} if binary else {'encoding': 'utf-8'}
        with open(full, mode, **kwargs) as f:
            f.write(data)
        return full


# ============ 刮削引擎（NFO + 海报）============
def _xml_escape(s):
    if not s:
        return ''
    return (str(s).replace('&', '&amp;').replace('<', '&lt;')
            .replace('>', '&gt;').replace('"', '&quot;').replace("'", '&apos;'))


def download_image(tmdb, poster_path, size='w500', timeout=30):
    """下载 TMDB 图片，返回 bytes"""
    if not poster_path:
        return None
    url = IMG_BASE + size + poster_path
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 OpenListRenamer/2.0'})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()
    except Exception:
        return None


def build_nfo(media, detail):
    """
    生成 Jellyfin/Kodi 标准的 NFO 内容。
    media: {name, original_name, year, media_type, id}
    detail: TMDB 详情 dict
    返回 XML 字符串
    """
    is_tv = media.get('media_type') == 'tv'
    title = media.get('name') or media.get('original_name') or ''
    otitle = media.get('original_name') or title
    year = media.get('year') or ''
    plot = detail.get('overview') or ''
    genres = [g.get('name', '') for g in (detail.get('genres') or [])]
    genre_str = ''.join('<genre>%s</genre>' % _xml_escape(g) for g in genres if g)

    lines = []
    lines.append('<?xml version="1.0" encoding="utf-8" standalone="yes"?>')
    lines.append('<%s>' % ('tvshow' if is_tv else 'movie'))
    lines.append('  <title>%s</title>' % _xml_escape(title))
    lines.append('  <originaltitle>%s</originaltitle>' % _xml_escape(otitle))
    if year:
        lines.append('  <year>%s</year>' % _xml_escape(year))
        lines.append('  <premiered>%s-01-01</premiered>' % _xml_escape(year))
    if plot:
        lines.append('  <plot>%s</plot>' % _xml_escape(plot))
        lines.append('  <outline>%s</outline>' % _xml_escape(plot))
    if genre_str:
        lines.append(genre_str)
    lines.append('  <poster>poster.jpg</poster>')
    lines.append('  <fanart>fanart.jpg</fanart>')
    if is_tv:
        lines.append('  <episodeguideurl></episodeguideurl>')
    lines.append('</%s>' % ('tvshow' if is_tv else 'movie'))
    return '\n'.join(lines)


def scrape_folder(fs, folder_path, media, tmdb):
    """
    对重命名后的影视文件夹执行刮削：
      1. 拉 TMDB 详情
      2. 生成 tvshow.nfo / movie.nfo
      3. 下载 poster.jpg + fanart.jpg
    返回结果 dict。
    """
    # 详情
    is_tv = media.get('media_type') == 'tv'
    tmdb_id = media.get('id')
    if not tmdb_id:
        return {'ok': False, 'msg': '缺少 TMDB ID'}
    detail = tmdb._req(f'/{("tv" if is_tv else "movie")}/{tmdb_id}',
                       {'append_to_response': 'images'})
    if not detail or 'id' not in detail:
        return {'ok': False, 'msg': 'TMDB 详情拉取失败'}

    nfo_name = 'tvshow.nfo' if is_tv else 'movie.nfo'
    nfo_content = build_nfo(media, detail)
    title = media.get('name') or media.get('original_name') or ''
    year = media.get('year') or ''

    written = []
    # NFO
    try:
        fs.write_file(folder_path.rstrip('/') + '/' + nfo_name, nfo_content)
        written.append(nfo_name)
    except Exception as e:
        return {'ok': False, 'msg': 'NFO 写入失败: %s' % e}

    # 海报 / 背景
    images = (detail.get('images') or {})
    poster_path = (images.get('posters') or [{}])[0].get('file_path') or detail.get('poster_path')
    fanart_path = (images.get('backdrops') or [{}])[0].get('file_path') or detail.get('backdrop_path')

    if poster_path:
        data = download_image(tmdb, poster_path)
        if data:
            try:
                fs.write_file(folder_path.rstrip('/') + '/poster.jpg', data, binary=True)
                written.append('poster.jpg')
            except Exception:
                pass
    if fanart_path:
        data = download_image(tmdb, fanart_path, size='w1280')
        if data:
            try:
                fs.write_file(folder_path.rstrip('/') + '/fanart.jpg', data, binary=True)
                written.append('fanart.jpg')
            except Exception:
                pass

    return {'ok': True, 'files': written, 'title': title, 'year': year}


if __name__ == '__main__':
    # 自测
    for n in ['狂飙 第01集.mp4', '01.mp4', '庆余年.S02E08.mkv', '三体.EP10.mkv', '流浪地球2.mp4', '教父2.mp4']:
        print(n, '→', extract_episode(n))
