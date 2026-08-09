# OpenList 影视资源智能重命名 — Web 版
FROM python:3.11-slim

WORKDIR /app

# 依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 代码
COPY core.py app.py ./
COPY templates ./templates

# 默认配置（可用环境变量覆盖）
ENV PORT=8080
ENV TMDB_KEY=fe717bbe0351637ab4a8cd6f7c754686
ENV BASE_URL=http://10.10.10.1:5445
ENV OL_USER=admin
ENV OL_PASS=admin
ENV SECRET_KEY=openlist-renamer-secret

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/', timeout=3)" || exit 1

CMD ["python", "app.py"]
