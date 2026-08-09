#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OpenList 影视资源智能重命名 — Web 版
====================================
Docker 部署后浏览器访问，支持：
  - 连接 OpenList（Alist 兼容 API）
  - 目录浏览
  - TMDB 匹配（输入真实影视名 → 选结果）
  - 批量改名方案 + 执行

环境变量：
  TMDB_KEY   TMDB API Key（可选，默认内置）
  PORT       监听端口（默认 8080）
  BASE_URL   默认 OpenList 地址（可选，如 http://192.168.1.100:5244）
  OL_USER    默认 OpenList 账号（可选）
  OL_PASS    默认 OpenList 密码（可选）
"""

import os
import threading

from flask import Flask, render_template, request, jsonify, session

from core import TMDB, OpenList, extract_episode

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'openlist-renamer-secret')
app.config['JSON_AS_ASCII'] = False

DEFAULT_OL = os.environ.get('BASE_URL', 'http://10.10.10.1:5445')
DEFAULT_USER = os.environ.get('OL_USER', 'admin')
DEFAULT_PASS = os.environ.get('OL_PASS', 'admin')

# 会话级 OpenList 客户端（每个浏览器会话独立）
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


@app.route('/')
def index():
    return render_template('index.html',
                           default_ol=DEFAULT_OL,
                           default_user=DEFAULT_USER,
                           default_pass=DEFAULT_PASS)


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


@app.route('/api/list', methods=['GET'])
def api_list():
    ol = get_client()
    if not ol:
        return jsonify({'ok': False, 'msg': '未连接，请先登录'})
    path = request.args.get('path', '/')
    try:
        items = ol.list_dir(path)
        data = [{'name': it.get('name', ''),
                 'is_dir': it.get('is_dir', False),
                 'size': it.get('size', 0)} for it in items]
        return jsonify({'ok': True, 'path': path, 'items': data})
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
    ol = get_client()
    if not ol:
        return jsonify({'ok': False, 'msg': '未连接，请先登录'})
    data = request.get_json(silent=True) or {}
    path = data.get('path', '/')
    media = data.get('media')
    include_year = data.get('include_year', True)
    if not media:
        return jsonify({'ok': False, 'msg': '请先选择影视'})

    tmdb = TMDB()
    try:
        items = ol.list_dir(path)
        plans = []
        for it in items:
            new_name, note = tmdb.build_plan(it, media, include_year)
            if new_name is not None and new_name.lower() != it.get('name', '').lower():
                plans.append({
                    'src_name': it.get('name', ''),
                    'new_name': new_name,
                    'note': note,
                    'src_path': path.rstrip('/') + '/' + it.get('name', ''),
                })
        return jsonify({'ok': True, 'plans': plans, 'total': len(items)})
    except Exception as e:
        return jsonify({'ok': False, 'msg': str(e)})


@app.route('/api/rename', methods=['POST'])
def api_rename():
    ol = get_client()
    if not ol:
        return jsonify({'ok': False, 'msg': '未连接，请先登录'})
    data = request.get_json(silent=True) or {}
    targets = data.get('items', [])
    if not targets:
        return jsonify({'ok': False, 'msg': '没有选择任何项目'})

    ok, results = ol.batch_rename([(t['src_path'], t['new_name']) for t in targets])
    return jsonify({'ok': True, 'success': ok, 'total': len(targets),
                    'results': [{'path': p, 'new_name': n, 'ok': s, 'msg': m} for p, n, s, m in results]})


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port, debug=False, threaded=True)
