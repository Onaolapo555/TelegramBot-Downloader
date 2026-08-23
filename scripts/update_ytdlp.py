#!/usr/bin/env python
"""Update yt-dlp to latest and print version."""
import subprocess, sys

try:
    subprocess.check_call([sys.executable, "-m", "pip", "install", "-U", "yt-dlp"])
    import yt_dlp
    print(f"yt-dlp version: {yt_dlp.version.__version__}")
except Exception as e:
    print(f"Failed: {e}", file=sys.stderr)
    sys.exit(1)
