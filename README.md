# OpenList 影视资源智能重命名 + 刮削

把**混乱命名的电影/剧集**整理成**芝杜播放器能识别**的标准结构，一次搞定，不再手动改文件名。

```
狂飙.第12集.mp4           →  狂飙 (2023)/狂飙 (2023) S01E12.mp4
漫长的季节.03.mkv         →  漫长的季节 (2023)/漫长的季节 (2023) S01E03.mkv
The.Godfather.1972.mp4   →  教父 (1972)/教父 (1972).mkv
[电影天堂]沙丘2.mkv      →  沙丘2 (2024)/沙丘2 (2024).mkv
```

## 🚀 v3 新特性（多来源 + 自动刮削）

| 能力 | 说明 |
|---|---|
| **三端来源切换** | OpenList 网盘 / NAS 挂载卷 / 电脑本地，任意地方都能改 |
| **自动刮削** | 重命名后自动生成 `tvshow.nfo`/`movie.nfo` + `poster.jpg` + `fanart.jpg`（Jellyfin/Kodi/芝杜通用标准） |
| **完整影视库整理** | 改名 → 建文件夹 → 拉 TMDB 简介/海报/背景图，一步到位 |

刮削产物示例（重命名一个文件夹后自动生成）：
```
狂飙 (2023)/
├── tvshow.nfo        ← 剧集元数据（标题/年份/简介/类型）
├── poster.jpg        ← 海报
├── fanart.jpg        ← 背景图
└── 狂飙 (2023) S01E01.mp4
```

## ✨ 三种使用方式

| 方式 | 适合场景 | 说明 |
|---|---|---|
| **🌐 Web 版（Docker）** | NAS/服务器常驻 | 浏览器操作，手机电脑都能用 |
| **🖥️ GUI 版（exe）** | Windows 单机 | 双击即用，连 OpenList 改网盘资源名 |
| **⌨️ 命令行版** | 本地/NAS 文件整理 | `media_renamer.py` 扫描目录批量改 |

---

## 🌐 方式一：Web 版（Docker 部署，推荐）

### 快速启动

```bash
# 方法 A：docker compose（改好 docker-compose.yml 里的 OpenList 地址/账号）
docker compose up -d

# 方法 B：docker run
docker run -d --name openlist-renamer -p 24568:24568 \
  -e BASE_URL=http://192.168.x.x:5244 \
  -e OL_USER=admin -e OL_PASS=admin \
  ghcr.io/yzx860325-design/openlist-renamer:latest
```

打开 `http://NAS地址:24568` 即可使用。

### 环境变量

| 变量 | 说明 | 默认 |
|---|---|---|
| `BASE_URL` | OpenList 地址 | `http://10.10.10.1:5445` |
| `OL_USER` / `OL_PASS` | OpenList 账号密码 | `admin` / `admin` |
| `TMDB_KEY` | TMDB API Key | 内置（建议换自己的） |
| `PORT` | 监听端口 | `24568` |
| `SECRET_KEY` | 会话密钥 | 内置 |
| `MEDIA_ROOT` | NAS/本地影视根目录 | `/media` |

### NAS 挂载（整理本地/NAS 文件时必配）

`docker-compose.yml` 的 volumes 段，把影视目录挂到 `/media`：
```yaml
volumes:
  - /volume1/影视:/media   # 群晖示例
  # - /share/CACHEDEV1_DATA/影视:/media   # 威联通示例
```

### 使用流程

1. **选择来源**：OpenList 网盘 / NAS 本地（Tab 切换）
2. **进入**某部影视所在的文件夹
3. **输入真实影视名**（你记录的名字，如"狂飙"）→ **匹配 TMDB**
4. **选择**正确结果（同名影视用年份区分）
5. **生成改名方案** → 勾选 → **执行重命名**
6. **自动刮削**：勾选"重命名后自动刮削"→ 自动生成 NFO + 海报/背景图

---

## 🖥️ 方式二：GUI 版（Windows exe）

1. 双击 `OpenList影视重命名.exe`
2. 填 OpenList 地址 + 账号密码 → 连接
3. 进入影视目录 → 输入真实剧名 → 匹配 TMDB → 选结果
4. 生成方案 → 勾选 → 执行

> 打包命令：`pyinstaller -F -w -n "OpenList影视重命名" openlist_renamer_gui.py`

---

## ⌨️ 方式三：命令行版（本地/NAS 文件）

### 1. 获取 TMDB API Key（一次性）
- 注册 https://www.themoviedb.org/signup → https://www.themoviedb.org/settings/api
- Create → Developer → 复制 **API Key (v3 auth)**

### 2. 运行
```bash
# 预览（不改文件，先看效果）
python media_renamer.py --key YOUR_KEY --scan "D:\电影"

# 执行
python media_renamer.py --key YOUR_KEY --scan "D:\电影" --apply

# 电影合集（如 教父1/2/3，有数字也按电影）
python media_renamer.py --key YOUR_KEY --scan "D:\电影\教父" --movie

# 指定年份辅助匹配
python media_renamer.py --key YOUR_KEY --scan "D:\电影" --year 2024
```

---

## 🔧 能识别什么

| 文件名模式 | 例子 | 识别结果 |
|---|---|---|
| 中文集数 | 狂飙.第12集 | S01E12 |
| 标准季集 | 庆余年.S02E08 | S02E08 |
| 中英季集 | 第2季第12集 | S02E12 |
| EP 格式 | 三体.EP10 | S01E10 |
| Episode | 繁花 Episode 3 | S01E03 |
| 纯数字 | 01.mp4 / 漫长的季节.03 | S01E01 / S01E03 |

自动清除：`[电影天堂]` 广告、`1080p/x264/BluRay` 编码、`国语中字` 等杂质。

## 📁 项目结构

```
├── app.py                    # Web 版（Flask，Docker 入口）
├── core.py                   # 核心逻辑：集数解析/TMDB/OpenList/本地FS/刮削引擎
├── templates/index.html      # Web 前端（来源切换 + 刮削）
├── openlist_renamer_gui.py   # GUI 版（tkinter）
├── media_renamer.py          # 命令行版
├── Dockerfile                # Docker 镜像
├── docker-compose.yml        # 一键部署（含 NAS 挂载示例）
└── requirements.txt          # Python 依赖（仅 flask）
```

## ⚠️ 注意事项

- **先预览再执行**；执行会直接改网盘/文件系统里的名字，不可撤销
- 剧集自动解析集数；无法解析的（花絮等）自动跳过
- 依赖网络访问 TMDB（匹配片名/年份）
- TMDB Key 建议注册自己的（免费），避免公共 Key 被限流
