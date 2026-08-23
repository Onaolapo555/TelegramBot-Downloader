from app.utils.url import extract_urls, is_known_platform
from app.utils.format import human_bytes, human_duration

def test_extract_urls():
    assert extract_urls("check https://youtu.be/dQw4w9WgXcQ wow") == ["https://youtu.be/dQw4w9WgXcQ"]
    assert extract_urls("www.youtube.com/watch?v=abc") == ["https://www.youtube.com/watch?v=abc"]
    assert extract_urls("no link here") == []

def test_human_bytes():
    assert human_bytes(0) == "0 B"
    assert human_bytes(1024) == "1.0 KB"
    assert human_bytes(1024*1024) == "1.0 MB"

def test_human_duration():
    assert human_duration(61) == "1m 1s"
    assert human_duration(3600) == "1h 0m"
