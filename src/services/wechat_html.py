"""Convert Horizon markdown summaries to WeChat-compatible HTML with inline styles.

WeChat Official Account strips <style> blocks and external CSS, so all styling
must be inline. This module converts markdown to HTML then applies inline styles
via BeautifulSoup tree-walking.
"""

from bs4 import BeautifulSoup, NavigableString, Tag
import markdown

# Primary accent color and palette
_ACCENT = "#1e6fff"
_ACCENT_LIGHT = "#e8f0fe"
_BG_CARD = "#f8f9fc"
_TEXT_PRIMARY = "#1a1a2e"
_TEXT_BODY = "#3d3d5c"
_TEXT_MUTED = "#8888a0"
_BORDER = "#e8e8f0"

STYLES = {
    "h1": (
        "font-size:22px;font-weight:bold;color:#ffffff;"
        "background:linear-gradient(135deg,#1e6fff,#5e4ae3);"
        "padding:24px 16px;margin:0 0 20px 0;text-align:center;"
        "border-radius:8px;letter-spacing:1px;"
    ),
    "h2": (
        "font-size:17px;font-weight:bold;color:#1a1a2e;"
        "margin:28px 0 10px 0;padding:12px 14px;"
        "background-color:#e8f0fe;border-left:4px solid #1e6fff;"
        "border-radius:0 6px 6px 0;"
    ),
    "h3": (
        "font-size:15px;font-weight:bold;color:#1e6fff;"
        "margin:18px 0 8px 0;"
    ),
    "p": (
        "font-size:15px;color:#3d3d5c;line-height:1.85;"
        "margin:0 0 14px 0;word-wrap:break-word;"
        "letter-spacing:0.3px;"
    ),
    "a": "color:#1e6fff;text-decoration:none;border-bottom:1px solid rgba(30,111,255,0.3);",
    "ul": "padding-left:22px;margin:0 0 14px 0;",
    "ol": "padding-left:22px;margin:0 0 14px 0;",
    "li": "margin-bottom:6px;font-size:15px;color:#3d3d5c;line-height:1.75;",
    "blockquote": (
        "border-left:3px solid #1e6fff;padding:10px 16px;"
        "color:#666680;margin:14px 0;font-size:14px;"
        "background-color:#f0f3fa;border-radius:0 6px 6px 0;"
        "line-height:1.7;"
    ),
    "pre": (
        "background-color:#1e1e2e;padding:14px 16px;"
        "border-radius:8px;overflow-x:auto;"
        "font-size:13px;line-height:1.6;color:#e0e0e0;"
        "margin:14px 0;"
    ),
    "code": (
        "font-family:Menlo,Monaco,monospace;"
        "background-color:rgba(30,111,255,0.08);"
        "padding:2px 8px;border-radius:4px;font-size:13px;"
        "color:#1e6fff;"
    ),
    "hr": "border:none;border-top:1px solid #e8e8f0;margin:28px 0;",
    "img": (
        "max-width:100%;height:auto;display:block;"
        "margin:16px auto;border-radius:8px;"
        "box-shadow:0 2px 12px rgba(0,0,0,0.08);"
    ),
    "strong": "font-weight:bold;color:#1a1a2e;",
    "em": "font-style:italic;",
    "table": (
        "border-collapse:collapse;width:100%;margin:14px 0;"
        "font-size:14px;border-radius:6px;overflow:hidden;"
    ),
    "th": (
        "background-color:#1e6fff;color:#ffffff;"
        "padding:10px 14px;text-align:left;font-weight:bold;"
    ),
    "td": (
        "padding:10px 14px;border-bottom:1px solid #e8e8f0;"
        "color:#3d3d5c;"
    ),
}

# Tags that get default paragraph styling when they contain block-level content
_DEFAULT_BLOCK_STYLE = STYLES["p"]


def markdown_to_wechat_html(markdown_text: str) -> str:
    """Convert a markdown string to WeChat-compatible HTML with inline styles.

    Returns an HTML fragment (no <html>/<body> wrapper) suitable for the
    WeChat draft/add API `content` field.
    """
    md = markdown.Markdown(extensions=["extra", "fenced_code"])
    raw_html = md.convert(markdown_text)

    soup = BeautifulSoup(raw_html, "html.parser")

    _sanitize(soup)
    _apply_styles(soup)

    return "".join(str(child) for child in soup.children)


def _sanitize(soup: BeautifulSoup) -> None:
    """Remove elements/attributes that WeChat rejects."""
    # Unwrap internal anchor links (href="#...") — WeChat rejects these
    for tag in soup.find_all("a", href=True):
        href = tag.get("href", "")
        if isinstance(href, str) and href.startswith("#"):
            tag.unwrap()

    for tag in soup.find_all(True):
        # Strip class and id — WeChat strips them anyway
        for attr in ("class", "id"):
            if attr in tag.attrs:
                del tag.attrs[attr]

        # Remove empty anchor tags left over after unwrapping
        if tag.name == "a" and not tag.get_text(strip=True) and not tag.get("href"):
            tag.decompose()
            continue

        # Unwrap div wrappers
        if tag.name == "div":
            tag.unwrap()


def _apply_styles(soup: BeautifulSoup | Tag) -> None:
    """Walk the HTML tree recursively and apply inline styles."""
    for child in list(soup.children):
        if isinstance(child, NavigableString):
            continue

        tag = child.name.lower() if hasattr(child, "name") else None
        if tag is None:
            continue

        # Apply base style for this tag
        style = STYLES.get(tag)
        if style:
            existing = child.get("style", "")
            child["style"] = f"{style} {existing}".strip()

        # Special cases

        # code inside pre — skip styling (pre handles it)
        if tag == "pre":
            for code_child in child.find_all("code"):
                if "style" in code_child.attrs:
                    del code_child.attrs["style"]

        # strong/em inside headings — inherit from parent, don't double-style
        if tag in ("strong", "b", "em", "i"):
            parent = child.parent
            if parent and hasattr(parent, "name") and parent.name in ("h1", "h2", "h3"):
                if "style" in child.attrs:
                    del child.attrs["style"]

        # Recurse
        _apply_styles(child)
