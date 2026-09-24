# Small browser-free image: docker build --target http -t douyin-resolver:http .
FROM python:3.12-slim AS http
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 DOUYIN_ENGINE=http
WORKDIR /app
COPY pyproject.toml LICENSE ./
COPY douyin_resolver ./douyin_resolver
RUN pip install --no-cache-dir .
USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:'+os.getenv('PORT','8000')+'/health', timeout=3)"
CMD ["sh", "-c", "exec uvicorn douyin_resolver.server:create_app --factory --host 0.0.0.0 --port ${PORT:-8000} --workers 1 --no-access-log"]

# Default: HTTP first + one reusable Chromium process when needed.
# Keep this image version aligned with the browser extra in pyproject.toml.
FROM mcr.microsoft.com/playwright/python:v1.58.0-noble AS auto
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 DOUYIN_ENGINE=auto DOUYIN_BROWSER_CHANNEL=chromium
WORKDIR /app
COPY pyproject.toml LICENSE ./
COPY douyin_resolver ./douyin_resolver
RUN pip install --no-cache-dir '.[browser]'
USER pwuser
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:'+os.getenv('PORT','8000')+'/health', timeout=3)"
CMD ["sh", "-c", "exec uvicorn douyin_resolver.server:create_app --factory --host 0.0.0.0 --port ${PORT:-8000} --workers 1 --no-access-log"]
