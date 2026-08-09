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


if __name__ == '__main__':
    # 自测
    for n in ['狂飙 第01集.mp4', '01.mp4', '庆余年.S02E08.mkv', '三体.EP10.mkv', '流浪地球2.mp4', '教父2.mp4']:
        print(n, '→', extract_episode(n))
