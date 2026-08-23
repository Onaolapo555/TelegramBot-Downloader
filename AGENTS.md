# PROJECT: UniMedia Downloader Bot (Telegram)

## 1. Vision & Ultimate Goal

Build the **fastest, most reliable, and most user-friendly Telegram bot** in existence for downloading videos (and audio) from virtually any social media platform or YouTube.

Users simply paste a link → the bot returns the media in the highest practical quality, with options for video quality, audio-only, or both.  
No apps, no websites, no browser extensions, no Python installation required. Works perfectly on mobile.

**Primary success criteria:**
- Feels instant for normal videos (< 100 MB)
- Reliably handles 500 MB – 1 GB files (and up to ~2 GB when possible)
- Higher success rate and lower failure rate than any public competitor
- Extremely low latency from “link received” to “file delivered”
- Scales to hundreds/thousands of concurrent users without collapsing
- Clean, modern, delightful UX with progress updates and smart defaults

This is **not** a personal toy. It must be good enough that friends, family, and strangers actually prefer it over every other downloader bot or website.

---

## 2. Core Features (Must Have)

### 2.1 Download Capabilities
- Support all major platforms that yt-dlp supports:
  YouTube, TikTok, Instagram (Reels, Posts, Stories), Twitter/X, Facebook, Reddit, Vimeo, Twitch clips, LinkedIn, Pinterest, and many more.
- Automatic best quality selection with intelligent fallbacks.
- User-selectable options:
  - Best video (up to 1080p / 4K when file size allows)
  - 720p / 480p / 360p
  - Audio only (MP3 320kbps or best available, or M4A)
  - Video + separate audio track (when useful)
- Playlist support (optional, limited by default to prevent abuse — e.g. first 5–10 items or with confirmation).
- Subtitle download (optional, soft or hard-coded).
- Thumbnail + metadata (title, uploader, duration, view count) returned with the file.

### 2.2 Large File Support (Critical)
- Must support files from **500 MB up to 1 GB** reliably, and ideally up to Telegram’s practical maximum (~2 GB).
- Strategy:
  1. Prefer Telegram Local Bot API Server (self-hosted) → allows files up to 2 GB.
  2. Fallback: Upload finished file to temporary high-speed object storage (Cloudflare R2 / AWS S3 / Backblaze B2 / Wasabi) with short-lived signed URL (1–24 h expiry) and send the link + instructions.
  3. Always try to keep the final delivered file under the current Telegram limit when possible by choosing lower quality automatically if the user didn’t force high quality.

### 2.3 User Experience
- Super clean conversation flow.
- Immediate acknowledgement + live progress (percentage + speed + ETA when possible).
- Inline keyboards for quality / format choice after the bot detects the link.
- `/start`, `/help`, `/settings`, `/stats`, `/donate` (optional), language selection.
- Remember user preferences (default quality, audio preference, etc.).
- Multi-language support from day one (at least English + major languages of target users).
- Beautiful captions with title, duration, source, and short credit.

### 2.4 Reliability & Speed
- Highest possible success rate.
- Aggressive but smart retry logic.
- Format selection that prioritizes fast-downloadable progressive formats when possible, then falls back to best adaptive.
- Concurrent download of video + audio streams when merging is needed (yt-dlp already does this well).
- Pre-warming of yt-dlp extractors / cookies where useful.

---

## 3. High-Level Architecture



### Key Design Principles
- Fully asynchronous.
- Stateless bot processes as much as possible; state lives in Redis + DB.
- Horizontal scaling of workers.
- Fail-fast + graceful degradation.
- Zero long-term storage of user media (privacy + legal).

---

## 4. Detailed Component Breakdown

### 4.1 Bot Layer
- Library: `python-telegram-bot` v21+ (async) **or** `aiogram` 3 (both excellent; choose one and stick to it).
- Webhook mode in production (much better than polling for scale).
- Local Bot API Server running on the same machine or private network (critical for >50 MB files).

### 4.2 Link Processing Pipeline
1. Detect URL (robust regex + yt-dlp’s `extract_info(download=False)` for validation).
2. Quick metadata probe (title, duration, available formats, approximate size).
3. Present inline keyboard with smart defaults:
   - “Best Quality (auto)”
   - “720p”
   - “480p”
   - “Audio Only (MP3)”
   - “Cancel”
4. Once choice is made → enqueue job with full context (user_id, chat_id, url, format preference, message_id for progress editing).

### 4.3 Download Engine
- Use `yt-dlp` Python API (not just CLI) for fine control.
- Critical options:
  ```python
  {
      "format": "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[height<=1080]/best",
      "merge_output_format": "mp4",
      "outtmpl": "%(id)s.%(ext)s",
      "noplaylist": True,          # default
      "quiet": True,
      "no_warnings": True,
      "progress_hooks": [progress_hook],
      "postprocessors": [...],     # FFmpeg for audio extract / thumbnail
      "concurrent_fragment_downloads": 8–16,  # speed!
      "buffersize": 1024*1024,
      "http_chunk_size": 10*1024*1024,
      "retries": 10,
      "fragment_retries": 10,
      "socket_timeout": 30,
      # cookies / proxy if needed
  }