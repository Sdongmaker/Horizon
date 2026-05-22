"""Convert Horizon markdown summaries to WeChat-compatible HTML with inline styles.

WeChat Official Account strips <style> blocks and external CSS, so all styling
must be inline. This module converts markdown to HTML then applies inline styles
via BeautifulSoup tree-walking.
"""

from bs4 import BeautifulSoup, NavigableString, Tag
import markdown

STYLES = {
    "h1": "font-size:22px;font-weight:bold;color:#1a1a1a;margin:24px 0 16px 0;text-align:center;",
    "h2": "font-size:18px;font-weight:bold;color:#1a1a1a;margin:20px 0 12px 0;"
    "border-bottom:1px solid #e0e0e0;padding-bottom:8px;",
    "h3": "font-size:16px;font-weight:bold;color:#1a1a1a;margin:16px 0 8px 0;",
    "p": "font-size:15px;color:#3f3f3f;line-height:1.75;margin:0 0 16px 0;word-wrap:break-word;",
    "a": "color:#576b95;text-decoration:none;",
    "ul": "padding-left:24px;margin:0 0 16px 0;",
    "ol": "padding-left:24px;margin:0 0 16px 0;",
    "li": "margin-bottom:8px;font-size:15px;color:#3f3f3f;line-height:1.75;",
    "blockquote": "border-left:4px solid #ddd;padding-left:16px;color:#888;margin:16px 0;font-size:14px;",
    "pre": "background-color:#f5f5f5;padding:12px 16px;border-radius:5px;overflow-x:auto;font-size:13px;line-height:1.5;",
    "code": "font-family:monospace;background-color:#f5f5f5;padding:2px 6px;border-radius:3px;font-size:14px;",
    "hr": "border:none;border-top:1px solid #e0e0e0;margin:24px 0;",
    "img": "max-width:100%;height:auto;display:block;margin:16px auto;border-radius:4px;",
    "strong": "font-weight:bold;",
    "em": "font-style:italic;",
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
