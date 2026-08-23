FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# ffmpeg + deps for yt-dlp
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# copy deps first for cache
COPY pyproject.toml README.md ./
COPY app ./app

RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -e . && \
    yt-dlp --version

# non-root
RUN useradd -m bot && chown -R bot:bot /app
USER bot

CMD ["python", "-m", "app.main", "--polling"]
