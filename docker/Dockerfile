FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# ffmpeg + aria2c + deps for yt-dlp insanely fast chunked downloads
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    aria2 \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# copy deps first for cache
COPY pyproject.toml README.md ./
COPY app ./app

RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -e . && \
    yt-dlp --version

# non-root + allow bot to auto-update yt-dlp inside container
RUN useradd -m bot && chown -R bot:bot /app && chmod -R a+w /usr/local/lib/python3.11/site-packages || true
USER bot

CMD ["python", "-m", "app.main", "--polling"]
