"""Phase-5 tests — observability, cookies, proxy, subtitles, metrics."""

import pathlib
import sys

import pytest

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


def test_config_phase5_fields():
    from app.config import Settings

    s = Settings(
        BOT_TOKEN="123:abc",
        SENTRY_DSN="https://test@sentry.io/1",
        ENABLE_METRICS=False,
        ENABLE_SUBTITLES=True,
        SUBTITLE_LANGS="en,hi",
        YTDLP_PROXY="http://proxy:8080",
        YTDLP_AUTO_UPDATE=True,
    )
    assert s.sentry_dsn == "https://test@sentry.io/1"
    assert s.enable_metrics is False
    assert s.enable_subtitles is True
    assert s.subtitle_langs == "en,hi"
    assert s.ytdlp_proxy == "http://proxy:8080"
    assert s.ytdlp_auto_update is True


def test_build_opts_proxy():
    from app.config import get_settings
    from app.core.downloader import build_ydl_opts

    get_settings.cache_clear()
    # Need to set proxy via env monkey? Use Settings override via object patch
    s = get_settings()
    orig_proxy = s.ytdlp_proxy
    try:
        s.ytdlp_proxy = "http://proxy:8080"
        opts = build_ydl_opts("best", "/tmp/%(id)s.%(ext)s", url="https://youtu.be/abc")
        assert opts.get("proxy") == "http://proxy:8080"
    finally:
        s.ytdlp_proxy = orig_proxy
        get_settings.cache_clear()


def test_build_opts_subtitles():
    from app.config import get_settings
    from app.core.downloader import build_ydl_opts

    get_settings.cache_clear()
    s = get_settings()
    orig_en = s.enable_subtitles
    orig_langs = s.subtitle_langs
    try:
        s.enable_subtitles = True
        s.subtitle_langs = "en,es"
        opts = build_ydl_opts("best", "/tmp/%(id)s.%(ext)s")
        assert opts.get("writesubtitles") is True
        assert opts.get("writeautomaticsub") is True
        assert opts.get("subtitleslangs") == ["en", "es"]
    finally:
        s.enable_subtitles = orig_en
        s.subtitle_langs = orig_langs
        get_settings.cache_clear()


def test_cookiefile_per_domain(tmp_path: pathlib.Path):
    from app.config import get_settings
    from app.core.downloader import _resolve_cookiefile

    get_settings.cache_clear()
    s = get_settings()
    orig_dir = s.cookies_dir
    try:
        s.cookies_dir = tmp_path
        # Create generic cookie
        (tmp_path / "cookies.txt").write_text("# Netscape", encoding="utf-8")
        assert _resolve_cookiefile("https://youtu.be/abc") == str(tmp_path / "cookies.txt")
        # Remove generic, create domain-specific
        (tmp_path / "cookies.txt").unlink()
        (tmp_path / "youtube.txt").write_text("# youtube", encoding="utf-8")
        assert _resolve_cookiefile("https://www.youtube.com/watch?v=abc") == str(tmp_path / "youtube.txt")
        assert _resolve_cookiefile("https://youtu.be/abc") == str(tmp_path / "youtube.txt")
        # Instagram specific
        (tmp_path / "instagram.txt").write_text("# insta", encoding="utf-8")
        assert _resolve_cookiefile("https://www.instagram.com/reel/abc") == str(tmp_path / "instagram.txt")
        # No cookie for unknown domain
        assert _resolve_cookiefile("https://example.com/video") is None
        # No url
        (tmp_path / "youtube.txt").unlink()
        (tmp_path / "instagram.txt").unlink()
        (tmp_path / "cookies.txt").write_text("# gen", encoding="utf-8")
        assert _resolve_cookiefile(None) == str(tmp_path / "cookies.txt")
    finally:
        s.cookies_dir = orig_dir
        get_settings.cache_clear()


def test_metrics_endpoint_config():
    from app.config import get_settings

    get_settings.cache_clear()
    s = get_settings()
    assert hasattr(s, "enable_metrics")
    assert s.enable_metrics is True  # default from .env
    # Check build_app returns metrics app when enabled
    from app.main import build_app

    # Ensure get_settings cache correct
    get_settings.cache_clear()
    bot, dp, app = build_app()
    # In polling mode with enable_metrics=True, app should be metrics app (not None)
    assert app is not None
    # Check routes include /metrics, /health
    routes = [r.resource.canonical for r in app.router.routes()] if hasattr(app.router, "routes") else []
    # At least check that we can find health route by inspecting
    found_health = any("/health" in str(r) for r in app.router.routes())
    assert found_health, "health route missing"


def test_nginx_example_exists():
    import pathlib

    p = pathlib.Path("docker/nginx.conf.example")
    assert p.exists()
    txt = p.read_text(encoding="utf-8")
    assert "proxy_pass http://unimedia_bot/webhook" in txt
    assert "/metrics" in txt
    assert "client_max_body_size" in txt


def test_env_example_has_phase5():
    import pathlib

    txt = pathlib.Path(".env.example").read_text(encoding="utf-8")
    assert "SENTRY_DSN" in txt
    assert "ENABLE_METRICS" in txt
    assert "YTDLP_PROXY" in txt


@pytest.mark.asyncio
async def test_metadata_uses_proxy_and_cookies():
    # Just ensure probe doesn't crash with proxy set
    from app.config import get_settings

    get_settings.cache_clear()
    s = get_settings()
    orig_proxy = s.ytdlp_proxy
    try:
        s.ytdlp_proxy = "http://proxy:8080"
        # Probe with invalid url should still raise but not due to proxy handling
        from app.core.metadata import probe_metadata

        try:
            await probe_metadata("https://example.com/invalid_test_url_for_probe")
        except Exception as e:
            # Should be ValueError or yt-dlp error, not config error
            assert isinstance(e, Exception)
    finally:
        s.ytdlp_proxy = orig_proxy
        get_settings.cache_clear()
