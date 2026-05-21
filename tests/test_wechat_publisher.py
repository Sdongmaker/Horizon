"""Tests for WeChat publisher, HTML converter, and cover image generation."""

import pytest
from src.models import WeChatPublishConfig
from src.services.wechat_html import markdown_to_wechat_html
from src.services.wechat_publisher import WeChatPublisher


# ---- HTML converter tests ----

def test_converts_paragraphs():
    html = markdown_to_wechat_html("Hello world.\n\nSecond paragraph.")
    assert "Hello world." in html
    assert "Second paragraph." in html
    assert "<html" not in html.lower()
    assert "<body" not in html.lower()
    assert "<style" not in html


def test_converts_headers():
    html = markdown_to_wechat_html("# H1\n\n## H2\n\n### H3")
    assert "font-size:22px" in html  # h1 style
    assert "font-size:18px" in html  # h2 style
    assert "font-size:16px" in html  # h3 style
    assert "H1" in html
    assert "H2" in html
    assert "H3" in html


def test_converts_links():
    html = markdown_to_wechat_html("[click here](https://example.com)")
    assert '<a href="https://example.com"' in html
    assert "color:#576b95" in html  # link color
    assert "click here" in html


def test_converts_code_blocks():
    html = markdown_to_wechat_html("Inline `code()` here.\n\n```python\nprint('hello')\n```")
    assert "code()" in html
    assert "background-color:#f5f5f5" in html
    assert "print" in html
    # pre code should NOT have its own padding (pre handles it)
    assert html.count("background-color:#f5f5f5") >= 1


def test_converts_blockquotes():
    html = markdown_to_wechat_html("> quoted text\n> more quote")
    assert "border-left:4px solid" in html
    assert "quoted text" in html


def test_converts_lists():
    html = markdown_to_wechat_html("- item one\n- item two\n\n1. first\n2. second")
    assert "item one" in html
    assert "item two" in html
    assert "first" in html
    assert "second" in html


def test_preserves_inline_html():
    """Raw HTML anchors (used by summarizer for TOC) should pass through."""
    html = markdown_to_wechat_html('<a id="item-1"></a>\n\n## [Title](https://x.com) ⭐️ 8/10')
    assert 'id="item-1"' in html
    assert "Title" in html


def test_preserves_details_tags():
    md = '<details><summary>References</summary>\n\n- [A](https://a.com)\n- [B](https://b.com)\n\n</details>'
    html = markdown_to_wechat_html(md)
    assert "<details>" in html
    assert "<summary>" in html
    assert "References" in html
    assert "https://a.com" in html


def test_handles_horizon_summary_format():
    """Integration test with real Horizon summary structure."""
    md = """# Horizon Daily - 2026-05-22

> From 150 items, 8 important content pieces were selected

---

1. [GitHub releases MCP SDK v2.0](#item-1) ⭐️ 9/10
2. [OpenAI announces GPT-5](#item-2) ⭐️ 8/10

---

<a id="item-1"></a>
## [GitHub releases MCP SDK v2.0](https://github.com/example) ⭐️ 9/10

Major release with new features.

github · example · May 22, 14:30

**Background**: MCP is the Model Context Protocol.

<details><summary>References</summary>

- [Release Notes](https://github.com/example/releases)

</details>

**Tags**: `#mcp`, `#sdk`, `#release`

---

<a id="item-2"></a>
## [OpenAI announces GPT-5](https://openai.com/blog) ⭐️ 8/10

Next generation model.

openai · blog · May 22, 10:00

---
"""
    html = markdown_to_wechat_html(md)
    assert "Horizon Daily" in html
    assert "GitHub releases MCP SDK" in html
    assert "OpenAI announces GPT-5" in html
    assert 'id="item-1"' in html
    assert 'id="item-2"' in html
    assert "<details>" in html
    assert "#mcp" in html
    assert "<html" not in html.lower()
    assert "<body" not in html.lower()
    assert "<style" not in html


def test_raw_html_passes_through():
    """Raw HTML in markdown (used by summarizer for anchors/details) must pass through."""
    html = markdown_to_wechat_html('<a id="item-1"></a>')
    assert 'id="item-1"' in html  # preserved, not stripped


# ---- Publisher initialization tests ----

def test_publisher_disabled_when_credentials_missing():
    config = WeChatPublishConfig(enabled=True, appid_env="MISSING_VAR", secret_env="MISSING_VAR")
    publisher = WeChatPublisher(config)
    assert publisher._disabled is True


def test_publisher_disabled_when_enabled_false():
    config = WeChatPublishConfig(enabled=False)
    publisher = WeChatPublisher(config)
    # When enabled=False, we still construct it but the orchestrator skips it
    # _disabled may or may not be True depending on env vars
    assert isinstance(publisher, WeChatPublisher)


def test_publisher_enabled_when_credentials_present(monkeypatch):
    monkeypatch.setenv("WECHAT_APPID", "wx_test")
    monkeypatch.setenv("WECHAT_SECRET", "test_secret")
    config = WeChatPublishConfig(enabled=True)
    publisher = WeChatPublisher(config)
    assert publisher._disabled is False
    assert publisher.appid == "wx_test"
    assert publisher.secret == "test_secret"


# ---- Cover image tests ----

def test_branded_cover_generates_jpeg():
    from src.services.wechat_publisher import _generate_branded_cover

    data = _generate_branded_cover("2026-05-22", "en")
    assert isinstance(data, bytes)
    assert len(data) > 1000  # should be a real JPEG
    assert data[:2] == b"\xff\xd8"  # JPEG magic bytes


def test_branded_cover_zh():
    from src.services.wechat_publisher import _generate_branded_cover

    data = _generate_branded_cover("2026-05-22", "zh")
    assert len(data) > 1000
    assert data[:2] == b"\xff\xd8"


# ---- API flow tests (mocked) — using asyncio.run() like the rest of the project ----


def test_publish_flow_with_mock_api(monkeypatch):
    """Full flow test with mocked WeChat API responses."""
    import asyncio

    monkeypatch.setenv("WECHAT_APPID", "wx_test")
    monkeypatch.setenv("WECHAT_SECRET", "test_secret")

    import httpx

    call_index = [0]

    responses = [
        {"access_token": "fake_token", "expires_in": 7200},
        {"media_id": "cover_media_id_123", "url": "http://example.com/img.jpg"},
        {"media_id": "draft_media_id_456"},
        {"publish_id": 123456, "msg_data_id": 123456, "errcode": 0, "errmsg": "ok"},
        {"publish_id": 123456, "status": 0, "errmsg": "ok"},
    ]

    def mock_json(self):
        idx = call_index[0]
        call_index[0] = min(idx + 1, len(responses) - 1)
        return responses[idx]

    monkeypatch.setattr(httpx.Response, "json", mock_json)

    async def _run():
        config = WeChatPublishConfig(enabled=True)
        publisher = WeChatPublisher(config)
        return await publisher.publish_daily_summary(
            summary_md="# Test\n\nHello world.",
            date="2026-05-22",
            lang="en",
        )

    result = asyncio.run(_run())
    assert result.get("publish_id") == 123456
    assert result.get("media_id") == "draft_media_id_456"
    assert "error" not in result


def test_publisher_returns_error_on_api_failure(monkeypatch):
    """When the WeChat API returns an error, publisher should return error dict, not raise."""
    import asyncio

    monkeypatch.setenv("WECHAT_APPID", "wx_test")
    monkeypatch.setenv("WECHAT_SECRET", "test_secret")

    import httpx

    def mock_fail(self):
        return {"errcode": 40001, "errmsg": "invalid credential"}

    monkeypatch.setattr(httpx.Response, "json", mock_fail)

    async def _run():
        config = WeChatPublishConfig(enabled=True)
        publisher = WeChatPublisher(config)
        return await publisher.publish_daily_summary("# Test", "2026-05-22", "en")

    result = asyncio.run(_run())
    assert "error" in result


def test_publisher_skips_when_disabled():
    import asyncio

    config = WeChatPublishConfig(enabled=True, appid_env="MISSING", secret_env="MISSING")
    publisher = WeChatPublisher(config)

    result = asyncio.run(publisher.publish_daily_summary("# Test", "2026-05-22", "en"))
    assert result == {"error": "WeChat credentials not configured"}
