#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Media Renamer - 影视文件智能重命名工具（芝杜刮削友好版）
========================================================
功能：
  1. 扫描目录（支持 NAS 网络路径 \\\\NAS\\xxx 或本地盘符）中的视频文件
  2. 解析混乱文件名：提取剧名线索 + 集数（第X集 / SxxExx / EPx / 纯数字等）
  3. TMDB API 搜索匹配 → 补齐官方片名、年份、季数
  4. 按芝杜标准结构重命名归档：
       电影:  电影名 (1994)/电影名 (1994).mkv
       剧集:  剧名 (2023)/Season 01/剧名 S01E01.mkv
  5. 无法识别的文件输出到 unresolved.txt，人工确认

用法：
  python media_renamer.py --key YOUR_TMDB_KEY --scan "D:\\电影"          # 预览（不执行）
  python media_renamer.py --key YOUR_TMDB_KEY --scan "\\\\NAS\\影视" --apply  # 执行重命名
  python media_renamer.py --key YOUR_TMDB_KEY --scan "D:\\电影" --movie  # 强制按电影处理

TMDB API Key 注册：https://www.themoviedb.org/settings/api （免费，2分钟）
"""

import os
import re
import sys
import json
import shutil
import difflib
import argparse
import urllib.request
import urllib.parse
import urllib.error
from pathlib import Path

# ============ 配置 ============
VIDEO_EXTS = {'.mp4', '.mkv', '.avi', '.ts', '.m2ts', '.mov', '.wmv', '.rmvb', '.flv', '.webm', '.m4v'}
SUBTITLE_EXTS = {'.srt', '.ass', '.ssa', '.sub', '.idx', '.sup'}
TMDB_BASE = 'https://api.themoviedb.org/3'

# 文件名杂质（发布组/编码/分辨率/广告等）——正则片段，按顺序清理
IMPURITY_PATTERNS = [
    r'\[[^\]]{0,30}字幕[^\]]*\]',          # [简英字幕] 等
    r'\[[^\]]*字幕[^\]]*\]',
    r'www\.[a-z0-9\-\.]+\.[a-z]{2,}',       # www.xxx.com 广告
    r'https?://\S+',
    r'@[a-zA-Z0-9_]{2,}',                   # @发布组
    r'(?i)\b(2160p|1080p|720p|480p|4k|8k|uhd|hd|hdr10|hdr|sdr|dovi|dv)\b',
    r'(?i)\b(x264|x265|h\.264|h\.265|hevc|avc|av1|mpeg4|divx|vp9)\b',
    r'(?i)\b(bluray|web-?dl|web-?rip|hdrip|bdrip|dvdrip|remux|bd|dvd|web)\b',
    r'(?i)\b(dts|ac3|aac|flac|truehd|dd5\.1|5\.1|7\.1|2audio|dual)\b',
    r'(?i)\b(atmos|vision|10bit|8bit|h265|h264)\b',
    r'(?i)\b(season|s\d{1,2}e\d{1,2}|e\d{1,3}|第\d{1,3}[集話话]|第\d{1,3}季)\b',  # 季集标记（不删，单独提取）
    r'[-_\.\s]+$',                          # 尾部杂质
    r'^[-_\.\s]+',                          # 头部杂质
]

# 集数提取正则（优先级从高到低）
EPISODE_PATTERNS = [
    (r'[Ss](\d{1,2})[Ee](\d{1,3})', 'sxxexx'),      # S01E12 / s1e3
    (r'第(\d{1,2})季[^\d]{0,4}第(\d{1,3})[集話话]', 'cn_ss'),  # 第2季第12集
    (r'第(\d{1,3})[集話话]', 'cn_ep'),               # 第12集
    (r'[Ee][Pp]?\.?(\d{1,3})', 'ep'),               # EP12 / E12 / ep.3
    (r'(?i)episode\s*\.?\s*(\d{1,3})', 'episode'),  # Episode 3
    (r'(?<![0-9])[-\s_\.](\d{1,2})(?![0-9])(?:[-\s_\.]|$)', 'num'),  # .03 结尾（排除年份）
]

# 剧名黑名单词（解析剧名时要剔除的通用词）
TITLE_STOPWORDS = {
    '高清', '国语', '粤语', '中字', '中文字幕', '双语', '合集', '全集',
    '完整版', '无水印', '抢先版', '院线', '剧场版', '超清', '蓝光',
    '免费', '在线观看', '资源', '下载', '修复版', '加长版',
    '未删减', '导演剪辑版', '4k', '1080p', '720p',
}


# ============ 1. 扫描 ============
def scan_files(root):
    """扫描目录下所有视频文件（含子目录），返回 [{path, name, ext}]"""
    files = []
    root = Path(root)
    for dirpath, dirnames, filenames in os.walk(root):
        for fn in filenames:
            ext = Path(fn).suffix.lower()
            if ext in VIDEO_EXTS:
                files.append({
                    'path': Path(dirpath) / fn,
                    'name': Path(fn).stem,
                    'ext': ext,
                    'folder': Path(dirpath).name,
                })
    return files


# ============ 2. 解析 ============
def extract_episode(name):
    """从文件名提取 (season, episode)，没有则 (None, None)"""
    for pat, kind in EPISODE_PATTERNS:
        m = re.search(pat, name)
        if not m:
            continue
        if kind == 'sxxexx':
            return int(m.group(1)), int(m.group(2))
        elif kind == 'cn_ss':
            return int(m.group(1)), int(m.group(2))
        elif kind in ('cn_ep', 'ep', 'episode', 'num'):
            return 1, int(m.group(1))
    return None, None


def strip_impurities(name, season=None, episode=None):
    """去掉文件名中的杂质，返回干净的候选剧名"""
    s = name
    # 扩展名
    s = re.sub(r'\.(mp4|mkv|avi|ts|m2ts|mov|wmv|rmvb|flv|webm|m4v)$', ' ', s, flags=re.I)
    # 方括号整体（广告/来源标记）
    s = re.sub(r'\[[^\]]*\]', ' ', s)
    for pat in IMPURITY_PATTERNS:
        s = re.sub(pat, ' ', s)
    # 去掉季集标记（含空格变体）
    s = re.sub(r'[Ss]\d{1,2}[Ee]\d{1,3}', ' ', s)
    s = re.sub(r'第\d{1,3}[集話话]', ' ', s)
    s = re.sub(r'第\d{1,2}季', ' ', s)
    s = re.sub(r'(?i)episode\s*\.?\s*\d{1,3}', ' ', s)
    s = re.sub(r'(?i)[Ee][Pp]?\.?\s*\d{1,3}', ' ', s)
    # 去掉已提取的集数数字（如 "漫长的季节.03" 的 03）
    if episode is not None:
        s = re.sub(r'(?<![A-Za-z])0*%d(?![A-Za-z0-9])' % episode, ' ', s)
    # 组合词：BD1080p / WEB1080p 等
    s = re.sub(r'(?i)(bd|web|dvd|bluray)\s*(\d{3,4}p)', ' ', s)
    # 停用词（高清/国语/中字/合集 等）
    for w in TITLE_STOPWORDS:
        s = re.sub(w, ' ', s)
    # 清理多余分隔符
    s = re.sub(r'[\[\]【】()（）《》【】]', ' ', s)
    s = re.sub(r'[_\-\.]+', ' ', s)
    s = re.sub(r'\s+', ' ', s).strip()
    return s


def extract_title(name, folder_name=''):
    """提取剧名候选 + 年份提示。返回 (候选列表, year_hint)"""
    candidates = []
    season, episode = extract_episode(name)

    # 从文件名剥离年份（4位数字），作为匹配提示
    year_hint = None
    ym = re.search(r'(?<!\d)((?:19|20)\d{2})(?!\d)', name)
    if ym:
        year_hint = int(ym.group(1))

    clean = strip_impurities(name, season, episode)
    if clean:
        # 候选名中去掉年份（避免干扰搜索）
        clean_no_year = re.sub(r'(?<!\d)((?:19|20)\d{2})(?!\d)', ' ', clean)
        clean_no_year = re.sub(r'\s+', ' ', clean_no_year).strip()
        if clean_no_year:
            candidates.append(clean_no_year)
        else:
            candidates.append(clean)

    # 文件夹名作为候选（可能文件夹就是剧名）
    if folder_name:
        fclean = strip_impurities(folder_name)
        if fclean:
            fclean_no_year = re.sub(r'(?<!\d)((?:19|20)\d{2})(?!\d)', ' ', fclean)
            fclean_no_year = re.sub(r'\s+', ' ', fclean_no_year).strip()
            if fclean_no_year:
                candidates.append(fclean_no_year)
            else:
                candidates.append(fclean)

    # 去重保序
    seen = set()
    result = []
    for c in candidates:
        if c and c.lower() not in seen:
            seen.add(c.lower())
            result.append(c)
    return result, year_hint


# ============ 3. TMDB 搜索 ============
class TMDB:
    def __init__(self, api_key):
        self.api_key = api_key
        self.cache = {}

    def _req(self, path, params):
        params['api_key'] = self.api_key
        params['language'] = params.get('language', 'zh-CN')
        url = TMDB_BASE + path + '?' + urllib.parse.urlencode(params)
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 MediaRenamer/1.0'})
            with urllib.request.urlopen(req, timeout=15) as r:
                return json.loads(r.read().decode('utf-8'))
        except urllib.error.HTTPError as e:
            if e.code == 401:
                print('  [ERROR] TMDB API Key 无效，请检查 --key 参数')
                sys.exit(1)
            print(f'  [ERROR] TMDB HTTP {e.code}')
            return None
        except Exception as e:
            print(f'  [ERROR] TMDB 请求失败: {e}')
            return None

    def search(self, query, media_type='tv'):
        """搜索剧集/电影，返回结果列表"""
        key = (query, media_type)
        if key in self.cache:
            return self.cache[key]
        data = self._req(f'/search/{media_type}', {'query': query})
        results = (data or {}).get('results', [])
        self.cache[key] = results
        return results

    def match(self, query, year_hint=None, media_type='tv'):
        """多轮搜索 + 相似度匹配，返回最佳 {id, name, original_name, year, seasons}"""
        candidates = self.search(query, media_type)
        if not candidates:
            return None

        # 相似度评分
        best = None
        best_score = 0
        q = query.lower()
        for c in candidates:
            name = (c.get('name') or c.get('title') or '').lower()
            oname = (c.get('original_name') or c.get('original_title') or '').lower()
            score = max(
                difflib.SequenceMatcher(None, q, name).ratio(),
                difflib.SequenceMatcher(None, q, oname).ratio(),
            )
            # 年份加分
            year_field = 'first_air_date' if media_type == 'tv' else 'release_date'
            year = (c.get(year_field) or '')[:4]
            if year_hint and year == str(year_hint):
                score += 0.3
            if score > best_score:
                best_score = score
                best = c

        if best_score < 0.3:
            return None

        year_field = 'first_air_date' if media_type == 'tv' else 'release_date'
        return {
            'id': best['id'],
            'name': best.get('name') or best.get('title') or '',
            'original_name': best.get('original_name') or best.get('original_title') or '',
            'year': (best.get(year_field) or '')[:4],
            'seasons': best.get('number_of_seasons'),
        }


# ============ 4. 命名 ============
def build_movie_path(media, ext):
    """电影：电影名 (年份)/电影名 (年份).ext"""
    name = media['name'] or media['original_name']
    year = media.get('year') or '0000'
    folder = f'{name} ({year})'
    fname = f'{name} ({year}){ext}'
    return folder, fname


def build_tv_path(media, season, episode, ext):
    """剧集：剧名 (年份)/Season 01/剧名 S01E01.ext"""
    name = media['name'] or media['original_name']
    year = media.get('year') or '0000'
    folder = f'{name} ({year})'
    sub = f'Season {season:02d}'
    fname = f'{name} S{season:02d}E{episode:02d}{ext}'
    return folder, sub, fname


def sanitize(name):
    """Windows 非法字符清理"""
    return re.sub(r'[<>:"/\\|?*]', ' ', name).strip()


# ============ 5. 主流程 ============
def process(args):
    api = TMDB(args.key)
    files = scan_files(args.scan)
    if not files:
        print('未找到视频文件')
        return

    print(f'扫描到 {len(files)} 个视频文件\n')

    # 先按目录分组（同一个文件夹视为同一个剧/电影）
    plans = []   # 可执行的计划
    unresolved = []  # 无法识别的

    for f in files:
        rel = str(f['path'])
        name = f['name']
        folder = f['folder']
        season, episode = extract_episode(name)

        # 候选剧名 + 年份提示
        titles, year_hint = extract_title(name, folder)
        if not titles:
            unresolved.append((rel, '无法提取剧名'))
            continue

        # 判定类型：文件在 "Season X" 目录下 或 文件名含 SxxExx → 剧集
        is_tv = False
        if re.search(r'(?i)season\s*\d+', str(f['path'].parent.name)):
            is_tv = True
        if season is not None and episode is not None:
            # 有集数但可能是电影合集（如 教父1/2/3）——由用户 --movie 强制
            if not args.movie:
                is_tv = True

        # TMDB 搜索（用剥离年份的纯剧名 + 年份提示辅助匹配）
        media = None
        for t in titles:
            media = api.match(t, year_hint=year_hint or args.year, media_type='tv' if is_tv else 'movie')
            if media:
                used_title = t
                break

        if not media:
            unresolved.append((rel, f'TMDB 未匹配（候选: {titles[0]}）'))
            continue

        # 构建目标
        if is_tv:
            if season is None or episode is None:
                # 有剧名没集数 → 无法确定集号，需人工
                unresolved.append((rel, f'剧集但无法提取集数（匹配: {media["name"]}）'))
                continue
            folder_name, sub, fname = build_tv_path(media, season, episode, f['ext'])
            dest = Path(args.scan) / sanitize(folder_name) / sub / sanitize(fname)
        else:
            folder_name, fname = build_movie_path(media, f['ext'])
            dest = Path(args.scan) / sanitize(folder_name) / sanitize(fname)

        # 防止覆盖
        if dest.exists() and dest != f['path']:
            base = dest.stem
            dest = dest.with_name(f'{base}_dup{dest.suffix}')

        plans.append((f['path'], dest, media, season, episode, is_tv))

    # ===== 输出计划 =====
    print('=' * 70)
    print(f'识别成功 {len(plans)} 个，未识别 {len(unresolved)} 个\n')
    for src, dst, media, season, episode, is_tv in plans:
        kind = '剧集' if is_tv else '电影'
        print(f'[{kind}] {src.name}')
        print(f'   -> {os.path.relpath(dst, args.scan)}')

    if unresolved:
        print('\n----- 未识别的文件 -----')
        for rel, reason in unresolved:
            print(f'  ⚠ {os.path.basename(rel)}  ({reason})')

    # 未识别清单落盘
    if unresolved:
        with open(os.path.join(args.scan, 'unresolved.txt'), 'w', encoding='utf-8') as fp:
            for rel, reason in unresolved:
                fp.write(f'{reason}\t{rel}\n')
        print(f'\n未识别清单已写入: {os.path.join(args.scan, "unresolved.txt")}')

    if not args.apply:
        print('\n[预览模式] 未执行任何操作。加 --apply 执行重命名。')
        return

    # ===== 执行 =====
    print('\n' + '=' * 70)
    print('开始执行...')
    moved = 0
    for src, dst, media, season, episode, is_tv in plans:
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            if dst == src:
                print(f'  = {src.name}（已符合规范）')
                continue
            shutil.move(str(src), str(dst))
            print(f'  ✓ {src.name} -> {os.path.relpath(dst, args.scan)}')
            moved += 1
        except Exception as e:
            print(f'  ✗ 移动失败 {src.name}: {e}')
    print(f'\n完成：移动 {moved} 个文件')


def main():
    parser = argparse.ArgumentParser(description='影视文件智能重命名工具（芝杜刮削友好）')
    parser.add_argument('--key', help='TMDB API Key', required=True)
    parser.add_argument('--scan', help='要扫描的目录（支持 \\\\NAS\\影视 或 D:\\影视）', required=True)
    parser.add_argument('--apply', action='store_true', help='执行重命名（默认只预览）')
    parser.add_argument('--movie', action='store_true', help='强制按电影处理（有集数也当电影）')
    parser.add_argument('--year', type=int, help='年份提示，辅助匹配（可选）')
    args = parser.parse_args()
    process(args)


if __name__ == '__main__':
    main()
