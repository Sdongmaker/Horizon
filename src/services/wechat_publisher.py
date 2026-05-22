"""WeChat Official Account publishing service for Horizon daily summaries.

Wraps WeChatClient to provide a high-level publish_daily_summary() method
that handles the full flow: cover image → HTML conversion → draft → publish.
"""

import logging
import os
from io import BytesIO
from typing import Optional

from ..models import WeChatPublishConfig
from .wechat import WeChatClient
from .wechat_html import markdown_to_wechat_html

logger = logging.getLogger(__name__)

_COVER_WIDTH = 900
_COVER_HEIGHT = 500


class WeChatPublisher:
    """Publishes Horizon daily summaries to WeChat Official Account.

    Follows the EmailManager / WebhookNotifier pattern: config-driven
    construction, lazy API client init, soft-fail error handling.
    """

    def __init__(self, config: WeChatPublishConfig, console=None):
        self.config = config
        if console is None:
            try:
                from rich.console import Console

                self.console = Console()
            except ImportError:

                class DummyConsole:
                    def print(self, *args, **kwargs):
                        print(*args, **kwargs)

                self.console = DummyConsole()
        else:
            self.console = console

        self.appid = os.getenv(config.appid_env)
        self.secret = os.getenv(config.secret_env)
        self._client: Optional[WeChatClient] = None
        self._disabled: bool = False

        if not self.appid or not self.secret:
            logger.warning(
                f"WeChat credentials not set ({config.appid_env}, {config.secret_env}). "
                "WeChat publishing disabled."
            )
            self.console.print(
                f"[yellow]Warning: {config.appid_env} or {config.secret_env} "
                "not set. WeChat publishing disabled.[/yellow]"
            )
            self._disabled = True

    async def _get_client(self) -> WeChatClient:
        if self._client is not None:
            return self._client
        self._client = WeChatClient(self.appid, self.secret)
        ok = await self._client.test_connection()
        if not ok:
            raise RuntimeError("WeChat access_token retrieval failed — check AppID/Secret and IP whitelist")
        return self._client

    async def run_self_test(self) -> dict:
        """Run a full startup self-test and return results.

        Tests: access_token → image upload → draft creation → draft cleanup.
        Prints each step to console so failures are visible in logs.
        """
        if self._disabled:
            self.console.print("[yellow]⚠️  WeChat self-test skipped: credentials not configured[/yellow]")
            return {"status": "skipped", "reason": "credentials not configured"}

        self.console.print("[bold]📱 WeChat self-test starting...[/bold]")
        results = {}

        try:
            # 1. Connection / token
            self.console.print("  [1/3] Testing connection (access_token)...")
            client = await self._get_client()
            token = await client._ensure_token()
            self.console.print(f"  [green]✓[/green] Token obtained → {token[:16]}...")
            results["token"] = "ok"
        except Exception as e:
            self.console.print(f"  [red]✗[/red] Token failed: {e}")
            results["token"] = f"failed: {e}"
            logger.error(f"WeChat self-test: token failed - {e}")
            return {"status": "failed", "results": results}

        # 2. Upload test image
        try:
            self.console.print("  [2/3] Uploading test cover image...")
            cover = WeChatClient.generate_cover_image()
            mat = await client.upload_permanent_image("test_cover.jpg", cover)
            thumb_media_id = mat.get("media_id")
            if not thumb_media_id:
                errcode = mat.get("errcode", "unknown")
                errmsg = mat.get("errmsg", str(mat))
                self.console.print(f"  [red]✗[/red] Image upload failed: errcode={errcode} errmsg={errmsg}")
                results["image_upload"] = f"failed: {errmsg}"
                logger.error(f"WeChat self-test: image upload failed - errcode={errcode} errmsg={errmsg}")
                return {"status": "failed", "results": results}
            self.console.print(f"  [green]✓[/green] Test image uploaded → {thumb_media_id[:20]}...")
            results["image_upload"] = "ok"
            results["test_thumb_media_id"] = thumb_media_id
        except Exception as e:
            self.console.print(f"  [red]✗[/red] Image upload failed: {e}")
            results["image_upload"] = f"failed: {e}"
            logger.error(f"WeChat self-test: image upload failed - {e}")
            return {"status": "failed", "results": results}

        # 3. Create a test draft, then clean up both draft and cover image
        try:
            self.console.print("  [3/5] Creating test draft...")
            test_html = markdown_to_wechat_html(
                "**Horizon 启动自测**\n\n"
                "这是一条由 Horizon 系统自动生成的测试草稿，用于验证微信公众号 API 连通性。\n\n"
                "所有功能模块运行正常。"
            )
            draft = await client.create_draft(
                title="Horizon 启动自测",
                content=test_html,
                thumb_media_id=thumb_media_id,
                author=self.config.author,
                digest="Horizon 系统启动自测草稿",
                need_open_comment=False,
            )
            media_id = draft.get("media_id")
            if not media_id:
                errcode = draft.get("errcode", "unknown")
                errmsg = draft.get("errmsg", str(draft))
                self.console.print(f"  [red]✗[/red] Draft creation failed: errcode={errcode} errmsg={errmsg}")
                results["draft"] = f"failed: {errmsg}"
                logger.error(f"WeChat self-test: draft creation failed - errcode={errcode} errmsg={errmsg}")
                return {"status": "failed", "results": results}
            self.console.print(f"  [green]✓[/green] Test draft created → {media_id}")

            # 4. Delete test draft
            self.console.print("  [4/5] Deleting test draft...")
            del_result = await client.delete_draft(media_id)
            del_errcode = del_result.get("errcode")
            if del_errcode and del_errcode != 0:
                errmsg = del_result.get("errmsg", str(del_result))
                self.console.print(f"  [yellow]⚠[/yellow] Draft deletion failed: errcode={del_errcode} errmsg={errmsg}")
                results["draft_delete"] = f"failed: {errmsg}"
            else:
                self.console.print(f"  [green]✓[/green] Test draft deleted")
                results["draft_delete"] = "ok"

            # 5. Delete test cover image from permanent materials
            self.console.print("  [5/5] Deleting test cover image...")
            del_mat = await client.delete_permanent_material(thumb_media_id)
            mat_errcode = del_mat.get("errcode")
            if mat_errcode and mat_errcode != 0:
                errmsg = del_mat.get("errmsg", str(del_mat))
                self.console.print(f"  [yellow]⚠[/yellow] Cover image deletion failed: errcode={mat_errcode} errmsg={errmsg}")
                results["image_delete"] = f"failed: {errmsg}"
            else:
                self.console.print(f"  [green]✓[/green] Test cover image deleted")
                results["image_delete"] = "ok"

            results["draft"] = "ok"
        except Exception as e:
            self.console.print(f"  [red]✗[/red] Draft test failed: {e}")
            results["draft"] = f"failed: {e}"
            logger.error(f"WeChat self-test: draft test failed - {e}")
            return {"status": "failed", "results": results}

        self.console.print(f"[bold green]✅ WeChat self-test passed![/bold green]\n")
        logger.info(f"WeChat self-test passed: {results}")
        return {"status": "ok", "results": results}

    async def publish_daily_summary(
        self,
        summary_md: str,
        date: str,
        lang: str,
        headline: str = "",
    ) -> dict:
        """Full flow: cover upload → HTML conversion → draft → (publish).

        Returns a dict with keys: media_id (always), publish_id on publish mode,
        or error on failure.
        """
        if self._disabled:
            return {"error": "WeChat credentials not configured"}

        try:
            client = await self._get_client()
        except Exception as e:
            logger.warning(f"WeChat client init failed: {e}")
            self.console.print(f"[yellow]⚠️  WeChat client init failed: {e}[/yellow]")
            return {"error": str(e)}

        lang_label = lang.upper()
        icon = "📱"

        try:
            # 1. Upload cover image
            self.console.print(f"{icon} WeChat ({lang_label}): uploading cover image...")
            cover_bytes = self._make_cover(date, lang)
            mat = await client.upload_permanent_image("cover.jpg", cover_bytes)
            thumb_media_id = mat.get("media_id")
            if not thumb_media_id:
                return {"error": f"Cover upload failed: {mat}"}
            self.console.print(f"{icon} WeChat ({lang_label}): cover uploaded → {thumb_media_id[:20]}...")

            # 2. Convert markdown to WeChat HTML
            self.console.print(f"{icon} WeChat ({lang_label}): converting markdown to HTML...")
            content_html = markdown_to_wechat_html(summary_md)

            # 3. Create draft
            self.console.print(f"{icon} WeChat ({lang_label}): creating draft...")
            labels = _get_labels(lang)
            title = headline or f"{labels['header']} - {date}"
            digest_prefix = labels.get("digest_prefix", "Daily tech brief")
            digest = f"{digest_prefix} — {date}"

            draft = await client.create_draft(
                title=title,
                content=content_html,
                thumb_media_id=thumb_media_id,
                author=self.config.author,
                digest=digest,
                need_open_comment=self.config.need_open_comment,
            )
            media_id = draft.get("media_id")
            if not media_id:
                errcode = draft.get("errcode", "unknown")
                errmsg = draft.get("errmsg", str(draft))
                logger.error(f"WeChat draft creation failed: errcode={errcode} errmsg={errmsg}")
                self.console.print(
                    f"[red]❌ WeChat ({lang_label}) draft creation failed: "
                    f"errcode={errcode} errmsg={errmsg}[/red]\n"
                )
                return {"error": f"Draft creation failed: {draft}"}
            self.console.print(f"{icon} WeChat ({lang_label}): draft created → {media_id[:20]}...")

            # 4. Draft-only mode: stop here
            if self.config.publish_mode == "draft":
                self.console.print(
                    f"[green]📝 WeChat draft ({lang_label}) saved — "
                    f"media_id: {media_id}[/green]\n"
                )
                return {"mode": "draft", "media_id": media_id}

            # 5. Publish
            self.console.print(f"{icon} WeChat ({lang_label}): submitting for publish...")
            pub = await client.publish_draft(media_id)
            publish_id = pub.get("publish_id")
            if not publish_id:
                errcode = pub.get("errcode", "unknown")
                errmsg = pub.get("errmsg", str(pub))
                logger.error(f"WeChat publish failed: errcode={errcode} errmsg={errmsg}")
                self.console.print(
                    f"[red]❌ WeChat ({lang_label}) publish failed: "
                    f"errcode={errcode} errmsg={errmsg}[/red]\n"
                )
                return {"error": f"Publish failed: {pub}"}

            # 6. Check status
            status = await client.check_publish_status(str(publish_id))
            self.console.print(
                f"[green]✅ WeChat article ({lang_label}) published! "
                f"publish_id: {publish_id}[/green]\n"
            )
            return {"mode": "publish", "publish_id": publish_id, "media_id": media_id, "status": status}

        except Exception as e:
            logger.warning(f"WeChat publish ({lang_label}) failed: {e}")
            self.console.print(f"[yellow]⚠️  WeChat publish ({lang_label}) failed: {e}[/yellow]\n")
            return {"error": str(e)}

    def _make_cover(self, date: str, lang: str) -> bytes:
        """Generate a branded cover image, or fall back to solid-color placeholder."""
        if not self.config.generate_cover:
            return WeChatClient.generate_cover_image()
        try:
            return _generate_branded_cover(date, lang)
        except Exception:
            return WeChatClient.generate_cover_image()


def _generate_branded_cover(
    date: str,
    lang: str,
    width: int = _COVER_WIDTH,
    height: int = _COVER_HEIGHT,
) -> bytes:
    """Generate a branded cover with gradient, geometric accents, and modern type."""
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", (width, height))
    # Draw a gradient: deep indigo → vibrant blue-purple
    for y in range(height):
        ratio = y / height
        r = int(15 + (55 - 15) * ratio)
        g = int(20 + (60 - 20) * ratio)
        b = int(70 + (140 - 70) * ratio)
        for x in range(width):
            img.putpixel((x, y), (r, g, b))

    draw = ImageDraw.Draw(img)

    # Decorative geometric circles (subtle, low opacity)
    for cx, cy, cr, co in [
        (width - 80, 80, 120, 20),
        (100, height - 100, 90, 15),
        (width - 180, height - 60, 60, 12),
    ]:
        draw.ellipse(
            [cx - cr, cy - cr, cx + cr, cy + cr],
            outline=(255, 255, 255, co),
            width=2,
        )

    # Horizontal accent line
    accent_y = height // 2 + 50
    draw.line(
        [(width // 2 - 60, accent_y), (width // 2 + 60, accent_y)],
        fill=(255, 255, 255, 60),
        width=1,
    )

    # Title
    title = "Horizon Daily" if lang == "en" else "Horizon 每日速递"

    try:
        # Try system fonts first, then common Linux fallbacks
        title_font = None
        for font_path in [
            "/System/Library/Fonts/Helvetica.ttc",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        ]:
            try:
                title_font = ImageFont.truetype(font_path, 52)
                break
            except (OSError, IOError):
                continue
        if title_font is None:
            title_font = ImageFont.load_default()

        date_font = None
        for font_path in [
            "/System/Library/Fonts/Helvetica.ttc",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans.ttf",
        ]:
            try:
                date_font = ImageFont.truetype(font_path, 26)
                break
            except (OSError, IOError):
                continue
        if date_font is None:
            date_font = ImageFont.load_default()
    except Exception:
        title_font = ImageFont.load_default()
        date_font = ImageFont.load_default()

    # Format date nicely
    from datetime import datetime
    try:
        dt = datetime.strptime(date, "%Y-%m-%d")
        if lang == "zh":
            date_text = dt.strftime("%Y 年 %-m 月 %-d 日")
        else:
            date_text = dt.strftime("%B %-d, %Y")
    except ValueError:
        date_text = date

    # Center title
    title_bbox = draw.textbbox((0, 0), title, font=title_font)
    tw, th = title_bbox[2] - title_bbox[0], title_bbox[3] - title_bbox[1]
    draw.text(
        ((width - tw) // 2, (height - th) // 2 - 24),
        title, fill=(255, 255, 255), font=title_font,
    )

    # Date below
    date_bbox = draw.textbbox((0, 0), date_text, font=date_font)
    dw, dh = date_bbox[2] - date_bbox[0], date_bbox[3] - date_bbox[1]
    draw.text(
        ((width - dw) // 2, height // 2 + 54),
        date_text, fill=(200, 210, 235), font=date_font,
    )

    buf = BytesIO()
    img.save(buf, format="JPEG", quality=92)
    return buf.getvalue()


def _get_labels(lang: str) -> dict:
    if lang == "zh":
        return {
            "header": "Horizon 每日速递",
            "digest_prefix": "每日技术资讯速览",
        }
    return {
        "header": "Horizon Daily",
        "digest_prefix": "Daily tech brief",
    }
