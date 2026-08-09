#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OpenList 影视资源智能重命名 — Web 版 v3
========================================
Docker 部署后浏览器访问，支持：
  - 三端来源：OpenList（Alist API）/ NAS 挂载卷 / 电脑本地
  - 目录浏览（三端统一）
  - TMDB 匹配（输入真实影视名 → 选结果）
  - 批量改名方案 + 执行
  - 自动刮削：重命名后生成 NFO + 海报/背景图

环境变量：
  TMDB_KEY    TMDB API Key（可选，默认内置）
  PORT        监听端口（默认 24568）
  BASE_URL    默认 OpenList 地址（可选）
  OL_USER     默认 OpenList 账号（可选）
  OL_PASS     默认 OpenList 密码（可选）
  MEDIA_ROOT  本地/NAS 影视根目录（默认 /media，Docker 挂载点）
"""

import os
import re
import threading

from flask import Flask, render_template, request, jsonify, session

from core import TMDB, OpenList, LocalFS, scrape_folder, extract_movie_query, extract_episode, is_video

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'openlist-renamer-secret')
app.config['JSON_AS_ASCII'] = False

DEFAULT_OL = os.environ.get('BASE_URL', 'http://10.10.10.1:5445')
DEFAULT_USER = os.environ.get('OL_USER', 'admin')
DEFAULT_PASS = os.environ.get('OL_PASS', 'admin')
MEDIA_ROOT = os.environ.get('MEDIA_ROOT', '/media')

# 会话级客户端
_clients = {}
_lock = threading.Lock()


def get_client():
    cid = session.get('ol_id')
    if cid and cid in _clients:
        return _clients[cid]
    return None


def make_client(base, user, pwd):
    ol = OpenList(base, user, pwd)
    if not ol.login():
        return None
    import uuid
    cid = str(uuid.uuid4())
    _clients[cid] = ol
    session['ol_id'] = cid
    session['ol_base'] = base
    return ol


def get_fs():
    """返回当前会话的 LocalFS（root 固定为 MEDIA_ROOT）"""
    return LocalFS(MEDIA_ROOT)


def _source_driver(src):
    """根据来源名返回驱动器实例"""
    if src == 'fs':
        return get_fs()
    ol = get_client()
    if not ol:
        raise Exception('OpenList 未连接，请先登录')
    return ol


def _fmt_items(items):
    return [{'name': it.get('name', ''),
             'is_dir': it.get('is_dir', False),
             'size': it.get('size', 0)} for it in items]


@app.route('/')
def index():
    return render_template('index.html',
                           default_ol=DEFAULT_OL,
                           default_user=DEFAULT_USER,
                           default_pass=DEFAULT_PASS,
                           media_root=MEDIA_ROOT)


# ============ API ============
@app.route('/api/login', methods=['POST'])
def api_login():
    data = request.get_json(silent=True) or {}
    base = (data.get('base') or '').strip() or DEFAULT_OL
    user = (data.get('username') or '').strip() or DEFAULT_USER
    pwd = data.get('password') or DEFAULT_PASS
    try:
        ol = make_client(base, user, pwd)
        if not ol:
            return jsonify({'ok': False, 'msg': '登录失败：账号或密码错误，或地址不可达'})
        return jsonify({'ok': True, 'base': base})
    except Exception as e:
        return jsonify({'ok': False, 'msg': str(e)})


@app.route('/api/fs/roots', methods=['GET'])
def api_fs_roots():
    """本地文件系统根信息"""
    try:
        exists = os.path.isdir(MEDIA_ROOT)
        return jsonify({'ok': True, 'root': MEDIA_ROOT, 'exists': exists})
    except Exception as e:
        return jsonify({'ok': False, 'msg': str(e)})


@app.route('/api/list', methods=['GET'])
def api_list():
    src = request.args.get('src', 'openlist')
    path = request.args.get('path', '/')
    try:
        driver = _source_driver(src)
        items = driver.list_dir(path)
        return jsonify({'ok': True, 'path': path, 'src': src, 'items': _fmt_items(items)})
    except Exception as e:
        return jsonify({'ok': False, 'msg': str(e)})


@app.route('/api/search', methods=['GET'])
def api_search():
    q = request.args.get('q', '').strip()
    if not q:
        return jsonify({'ok': False, 'msg': '请输入影视名'})
    tmdb = TMDB()
    try:
        results = tmdb.search_all(q)
        data = []
        for r in results:
            name = r.get('name') or r.get('title') or ''
            oname = r.get('original_name') or r.get('original_title') or ''
            yf = 'first_air_date' if r.get('_media_type') == 'tv' else 'release_date'
            year = (r.get(yf) or '')[:4]
            mt = 'tv' if r.get('_media_type') == 'tv' else 'movie'
            data.append({
                'id': r['id'],
                'name': name,
                'original_name': oname,
                'year': year,
                'media_type': mt,
                'label': '%s (%s) [%s]' % (name, year, '剧集' if mt == 'tv' else '电影'),
            })
        return jsonify({'ok': True, 'results': data})
    except Exception as e:
        return jsonify({'ok': False, 'msg': str(e)})


@app.route('/api/plan', methods=['POST'])
def api_plan():
    data = request.get_json(silent=True) or {}
    src = data.get('src', 'openlist')
    path = data.get('path', '/')
    media = data.get('media')
    include_year = data.get('include_year', True)
    if not media:
        return jsonify({'ok': False, 'msg': '请先选择影视'})

    tmdb = TMDB()
    try:
        driver = _source_driver(src)
        items = driver.list_dir(path)
        plans = []
        is_tv = media.get('media_type') == 'tv'
        # 季子目录名正则
        SEASON_RE = re.compile(r'^(?:Season\s*|第\s*)(\d+)(?:\s*季|\s*Season)?$|^S0*(\d+)$', re.I)

        for it in items:
            name = it.get('name', '')
            is_dir = it.get('is_dir', False)
            src_path = path.rstrip('/') + '/' + name

            # 剧集场景：检测到 Season 子目录 → 递归处理内部视频
            if is_tv and is_dir and SEASON_RE.match(name):
                # 进入 Season 子目录，列出内部视频
                try:
                    inner_items = driver.list_dir(src_path)
                    season_plans = tmdb.build_season_plans(inner_items, media, include_year)
                    for old_name, new_name, note in season_plans:
                        if new_name.lower() != old_name.lower():
                            plans.append({
                                'src_name': old_name,
                                'new_name': new_name,
                                'note': note + f'（在{name}/）',
                                'src_path': src_path + '/' + old_name,
                                'is_season_inner': True,
                                'media': media,
                            })
                except Exception as e:
                    pass
                # 季文件夹本身也列出（标记可跳过或保留）
                plans.append({
                    'src_name': name,
                    'new_name': name,
                    'note': '季文件夹（保留）',
                    'src_path': src_path,
                    'is_season_dir': True,
                    'media': media,
                })
                continue

            new_name, note = tmdb.build_plan(it, media, include_year)
            if new_name is not None and new_name.lower() != it.get('name', '').lower():
                plans.append({
                    'src_name': it.get('name', ''),
                    'new_name': new_name,
                    'note': note,
                    'src_path': src_path,
                    'media': media,
                })
        return jsonify({'ok': True, 'plans': plans, 'total': len(items)})
    except Exception as e:
        return jsonify({'ok': False, 'msg': str(e)})


@app.route('/api/batch-analyze', methods=['POST'])
def api_batch_analyze():
    """
    批量智能识别：扫描分类目录（如 /动画剧集）下所有影视文件夹，
    自动提取片名 → TMDB 匹配 → 识别结构 → 生成改名方案。
    返回带置信度的结果，供用户确认。
    """
    data = request.get_json(silent=True) or {}
    src = data.get('src', 'fs')
    path = data.get('path', '/')
    include_year = data.get('include_year', True)

    try:
        driver = _source_driver(src)
        tmdb = TMDB()
        items = driver.list_dir(path)
        results = []
        SEASON_RE = re.compile(r'^(?:Season\s*|第\s*)(\d+)(?:\s*季|\s*Season)?$|^S0*(\d+)$', re.I)

        for it in items:
            name = it.get('name', '')
            is_dir = it.get('is_dir', False)
            if not is_dir:
                continue  # 只处理文件夹（影视条目）
            src_path = path.rstrip('/') + '/' + name

            # 1. 自动提取查询词
            query, hint = extract_movie_query(name)

            # 2. TMDB 匹配
            match = None
            media_type = None
            if query:
                try:
                    all_results = tmdb.search_all(query)
                    if all_results:
                        # 优先 hint 类型
                        if hint in ('tv', 'movie'):
                            match = next((r for r in all_results if r.get('_media_type') == hint), None)
                        if not match:
                            match = all_results[0]
                        media_type = match.get('_media_type')
                except Exception:
                    pass

            # 3. 识别结构：子目录（季）+ 散视频
            inner_items = driver.list_dir(src_path)
            season_dirs = []
            top_videos = []
            for inner in inner_items:
                if inner.get('is_dir'):
                    season_dirs.append(inner['name'])
                elif is_video(inner.get('name', '')):
                    top_videos.append(inner['name'])

            # 4. 生成该条目的改名方案
            media = None
            if match:
                m_year = (match.get('first_air_date') or match.get('release_date') or '')[:4]
                media = {
                    'id': match['id'],
                    'name': match.get('name') or match.get('title'),
                    'original_name': match.get('original_name') or match.get('original_title'),
                    'year': m_year,
                    'media_type': media_type,
                }

            # 置信度
            year_in_name = re.search(r'(19|20)\d{2}', name)
            conf = 'low'
            if match:
                conf = 'mid'
                if media_type == hint:
                    conf = 'hi'
                if year_in_name and media and media.get('year') == year_in_name.group(0):
                    conf = 'hi'

            results.append({
                'name': name,
                'src_path': src_path,
                'query': query,
                'matched': bool(match),
                'confidence': conf,
                'media': media,
                'structure': {
                    'season_dirs': season_dirs,
                    'top_videos': top_videos[:20],
                    'top_video_count': len(top_videos),
                },
            })

        return jsonify({'ok': True, 'results': results, 'total': len(results)})
    except Exception as e:
        return jsonify({'ok': False, 'msg': str(e)})


@app.route('/api/rename', methods=['POST'])
def api_rename():
    data = request.get_json(silent=True) or {}
    src = data.get('src', 'openlist')
    targets = data.get('items', [])
    scrape = data.get('scrape', True)  # 是否刮削
    if not targets:
        return jsonify({'ok': False, 'msg': '没有选择任何项目'})
    try:
        driver = _source_driver(src)
        ok, results = driver.batch_rename([(t['src_path'], t['new_name']) for t in targets])

        # 刮削：对重命名成功的【文件夹】执行（剧集/电影整库文件夹）
        scrape_results = []
        tmdb = TMDB()
        # ⚠️ OpenList v3 上传 API 有兼容性问题（夸克/阿里等网盘不支持直接 API 上传文件）
        # 所以 OpenList 来源默认不执行刮削（重命名后下载到本地再刮削）
        scrape_enabled = scrape and src == 'fs'
        if scrape_enabled:
            for t in targets:
                parent_path = t['src_path'].rsplit('/', 1)[0]
                new_full = parent_path + '/' + t['new_name']
                # 只刮削"顶层影视文件夹"：非季内文件、非季文件夹、且带 media 的目录项
                if t.get('is_season_inner') or t.get('is_season_dir'):
                    continue
                if not t.get('media') or not t['media'].get('id'):
                    continue
                try:
                    # 顶层目录方案（重命名目标即文件夹）→ 刮削
                    r = scrape_folder(driver, new_full, t.get('media') or {}, tmdb)
                    if r.get('ok'):
                        scrape_results.append({'path': new_full, 'files': r.get('files', []), 'ok': True})
                    else:
                        scrape_results.append({'path': new_full, 'ok': False, 'msg': r.get('msg')})
                except Exception as e:
                    scrape_results.append({'path': new_full, 'ok': False, 'msg': str(e)})

        return jsonify({'ok': True, 'success': ok, 'total': len(targets),
                        'scrape': scrape_results,
                        'results': [{'path': p, 'new_name': n, 'ok': s, 'msg': m} for p, n, s, m in results]})
    except Exception as e:
        return jsonify({'ok': False, 'msg': str(e)})


@app.route('/api/scrape', methods=['POST'])
def api_scrape():
    """对指定文件夹执行刮削（已重命名的文件夹）"""
    data = request.get_json(silent=True) or {}
    src = data.get('src', 'openlist')
    path = data.get('path', '')
    media = data.get('media') or {}
    if not path:
        return jsonify({'ok': False, 'msg': '缺少路径'})
    if not media.get('id'):
        return jsonify({'ok': False, 'msg': '缺少 TMDB 匹配'})
    try:
        driver = _source_driver(src)
        tmdb = TMDB()
        r = scrape_folder(driver, path, media, tmdb)
        return jsonify(r)
    except Exception as e:
        return jsonify({'ok': False, 'msg': str(e)})


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 24568))
    app.run(host='0.0.0.0', port=port, debug=False, threaded=True)
