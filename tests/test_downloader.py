from app.core.downloader import build_ydl_opts, FORMAT_MAP

def test_format_map():
    assert "best" in FORMAT_MAP
    assert "720" in FORMAT_MAP
    assert "audio_mp3" in FORMAT_MAP

def test_build_opts_best():
    opts = build_ydl_opts("best", "/tmp/%(id)s.%(ext)s")
    assert opts["format"] == FORMAT_MAP["best"]
    assert opts["quiet"] is True

def test_build_opts_audio():
    opts = build_ydl_opts("audio_mp3", "/tmp/%(id)s.%(ext)s")
    assert any(p["key"] == "FFmpegExtractAudio" for p in opts["postprocessors"])
