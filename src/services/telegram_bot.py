"""Telegram bot for WeChat draft approval workflow.

Uses raw httpx against the Telegram Bot API — no third-party dependency.
Runs long-polling in the same container after the main Horizon pipeline finishes.
"""

import asyncio
import logging
import os
from typing import Optional

import httpx

from ..models import TelegramBotConfig
from ..storage.draft_registry import DraftRegistry
from .wechat import WeChatClient

logger = logging.getLogger(__name__)

TG_BASE = "https://api.telegram.org/bot"


class TelegramBot:
    """Sends approval messages and polls for button clicks.

    Single-container design: after the Horizon pipeline creates WeChat
    drafts, the same process enters poll_loop() to listen for callbacks.
    """

    def __init__(
        self,
        config: TelegramBotConfig,
        registry: DraftRegistry,
        console=None,
    ):
        self.config = config
        self.registry = registry
        self.bot_token = os.getenv(config.bot_token_env, "")
        raw_chat = os.getenv(config.chat_id_env, "")
        self.chat_id = raw_chat

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

        self._wechat_client: Optional[WeChatClient] = None
        self._wechat_appid: str = ""
        self._wechat_secret: str = ""
        self._last_update_id: int = 0

    def set_wechat_credentials(self, appid: str, secret: str) -> None:
        self._wechat_appid = appid
        self._wechat_secret = secret

    async def _get_wechat_client(self) -> WeChatClient:
        if self._wechat_client is not None:
            return self._wechat_client
        if not self._wechat_appid or not self._wechat_secret:
            raise RuntimeError("WeChat credentials not set on TelegramBot")
        self._wechat_client = WeChatClient(self._wechat_appid, self._wechat_secret)
        ok = await self._wechat_client.test_connection()
        if not ok:
            raise RuntimeError("WeChat access_token failed in TelegramBot")
        return self._wechat_client

    # ------------------------------------------------------------------
    # Self-test
    # ------------------------------------------------------------------

    async def run_self_test(self) -> dict:
        """Test Telegram bot connectivity: getMe → send test message → delete.

        Returns {"status": "ok"} or {"status": "failed", "reason": ...}.
        """
        if not self.bot_token:
            self.console.print("[yellow]⚠️  Telegram self-test skipped: bot token not set[/yellow]")
            return {"status": "skipped", "reason": "bot token not set"}

        self.console.print("[bold]🤖 Telegram self-test starting...[/bold]")
        results = {}

        try:
            # 1. getMe — verify token
            self.console.print("  [1/3] Testing bot token (getMe)...")
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(f"{TG_BASE}{self.bot_token}/getMe")
                data = resp.json()
            if not data.get("ok"):
                self.console.print(f"  [red]✗[/red] Invalid bot token: {data}")
                return {"status": "failed", "results": {"getMe": str(data)}}
            bot_name = data["result"]["username"]
            self.console.print(f"  [green]✓[/green] Bot @{bot_name} connected")
            results["getMe"] = "ok"
        except Exception as e:
            self.console.print(f"  [red]✗[/red] getMe failed: {e}")
            return {"status": "failed", "results": {"getMe": str(e)}}

        if not self.chat_id:
            self.console.print("[yellow]  Telegram chat_id not set — skip message test[/yellow]")
            self.console.print("[bold green]✅ Telegram self-test passed (token only)![/bold green]\n")
            return {"status": "ok", "results": results}

        try:
            # 2. Send test message
            self.console.print("  [2/3] Sending test message...")
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(
                    f"{TG_BASE}{self.bot_token}/sendMessage",
                    json={
                        "chat_id": self.chat_id,
                        "text": "🤖 <b>Horizon 启动自测</b>\n\nTelegram Bot 连通性测试通过。\n\n此消息将在 3 秒后自动删除。",
                        "parse_mode": "HTML",
                    },
                )
                data = resp.json()
            if not data.get("ok"):
                self.console.print(f"  [red]✗[/red] sendMessage failed: {data}")
                return {"status": "failed", "results": {**results, "sendMessage": str(data)}}
            msg_id = data["result"]["message_id"]
            self.console.print(f"  [green]✓[/green] Test message sent → msg_id={msg_id}")
            results["sendMessage"] = "ok"
        except Exception as e:
            self.console.print(f"  [red]✗[/red] sendMessage failed: {e}")
            return {"status": "failed", "results": {**results, "sendMessage": str(e)}}

        try:
            # 3. Delete test message
            self.console.print("  [3/3] Deleting test message...")
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(
                    f"{TG_BASE}{self.bot_token}/deleteMessage",
                    json={"chat_id": self.chat_id, "message_id": msg_id},
                )
                data = resp.json()
            if not data.get("ok"):
                self.console.print(f"  [yellow]⚠[/yellow] deleteMessage failed: {data}")
                results["deleteMessage"] = str(data)
            else:
                self.console.print(f"  [green]✓[/green] Test message deleted")
                results["deleteMessage"] = "ok"
        except Exception as e:
            self.console.print(f"  [yellow]⚠[/yellow] deleteMessage failed: {e}")
            results["deleteMessage"] = str(e)

        self.console.print("[bold green]✅ Telegram self-test passed![/bold green]\n")
        return {"status": "ok", "results": results}

    # ------------------------------------------------------------------
    # Sending
    # ------------------------------------------------------------------

    async def send_draft_notification(
        self,
        draft_id: str,
        date: str,
        lang: str,
        title: str,
        digest: str,
        preview: str = "",
    ) -> Optional[int]:
        """Send a Telegram notification that a WeChat draft has been saved.

        No interactive buttons — the user publishes manually from the WeChat
        backend (API publish requires a verified account).

        Returns the Telegram message_id, or None on failure.
        """
        if not self.bot_token or not self.chat_id:
            self.console.print(
                "[yellow]⚠️  Telegram bot token or chat_id not set — skip draft notification[/yellow]"
            )
            return None

        lang_label = lang.upper()
        label_map = {"ZH": "微信公众平台", "EN": "WeChat Official Account"}
        backend = label_map.get(lang_label, "WeChat Official Account")
        text = (
            f"📝 <b>草稿已保存 | {lang_label}</b>\n"
            f"<b>{title}</b>\n"
            f"{digest}"
        )
        if preview:
            max_preview = 3500 - len(text)
            if len(preview) > max_preview:
                preview = preview[:max_preview].rsplit("\n", 1)[0] + "\n..."
            text += f"\n\n{preview}"
        text += (
            f"\n\n请前往 <b>{backend}</b> 后台手动发布"
            f"\n草稿 ID: <code>{draft_id[:12]}</code>"
        )

        try:
            async with httpx.AsyncClient(timeout=self.config.request_timeout) as client:
                resp = await client.post(
                    f"{TG_BASE}{self.bot_token}/sendMessage",
                    json={
                        "chat_id": self.chat_id,
                        "text": text,
                        "parse_mode": "HTML",
                    },
                )
                data = resp.json()
                if not data.get("ok"):
                    self.console.print(
                        f"[red]❌ Telegram sendMessage failed: {data}[/red]"
                    )
                    return None

                msg_id = data["result"]["message_id"]
                self.registry.update_telegram_msg_id(draft_id, msg_id)
                self.console.print(
                    f"📬 Telegram approval message sent ({lang_label}) → msg_id={msg_id}"
                )
                return msg_id
        except Exception as e:
            logger.error(f"Telegram sendMessage error: {e}")
            self.console.print(f"[red]❌ Telegram sendMessage error: {e}[/red]")
            return None

    # ------------------------------------------------------------------
    # Polling
    # ------------------------------------------------------------------

    async def poll_loop(self) -> None:
        """Long-poll getUpdates in a loop. Blocks until Ctrl+C or SIGTERM."""
        if not self.bot_token:
            self.console.print("[yellow]⚠️  Telegram bot token not set — skip polling[/yellow]")
            return

        self.console.print(
            f"[bold]🤖 Telegram bot listening for approval callbacks "
            f"(polling every {self.config.polling_interval}s)...[/bold]\n"
            f"[dim]   Press Ctrl+C to stop[/dim]"
        )

        while True:
            try:
                await self._poll_once()
            except Exception as e:
                logger.error(f"Telegram polling error: {e}")
                self.console.print(f"[yellow]⚠️  Telegram poll error: {e} — retrying...[/yellow]")
            await asyncio.sleep(self.config.polling_interval)

    async def _poll_once(self) -> None:
        params: dict = {"timeout": self.config.polling_interval, "allowed_updates": ["callback_query"]}
        if self._last_update_id:
            params["offset"] = self._last_update_id + 1

        async with httpx.AsyncClient(timeout=self.config.request_timeout + 10) as client:
            resp = await client.post(
                f"{TG_BASE}{self.bot_token}/getUpdates",
                json=params,
            )
            data = resp.json()

        if not data.get("ok"):
            return

        for update in data["result"]:
            update_id = update["update_id"]
            self._last_update_id = max(self._last_update_id, update_id)

            callback = update.get("callback_query")
            if not callback:
                continue

            await self._handle_callback(callback)

    # ------------------------------------------------------------------
    # Callback handling
    # ------------------------------------------------------------------

    async def _handle_callback(self, callback: dict) -> None:
        cb_data = callback.get("data", "")
        cb_id = callback.get("id", "")
        msg = callback.get("message", {})
        msg_id = msg.get("message_id")
        chat_id = str(msg.get("chat", {}).get("id", ""))

        if not cb_data or "_" not in cb_data:
            return

        action, draft_id = cb_data.split("_", 1)
        entry = self.registry.get_by_id(draft_id)

        if not entry:
            await self._answer_callback(cb_id, "草稿记录已过期")
            return

        if action == "publish":
            await self._do_publish(cb_id, msg_id, chat_id, entry)
        elif action == "reject":
            await self._do_reject(cb_id, msg_id, chat_id, entry)

    async def _do_publish(
        self, cb_id: str, msg_id: int, chat_id: str, entry: dict
    ) -> None:
        media_id = entry["media_id"]
        lang = entry.get("lang", "?").upper()

        await self._answer_callback(cb_id, "正在发布到微信公众号...")

        try:
            client = await self._get_wechat_client()
            pub = await client.publish_draft(media_id)
            publish_id = pub.get("publish_id")

            if publish_id:
                self.registry.mark_published(entry["draft_id"])
                new_text = (
                    f"✅ <b>已发布 | {lang}</b>\n"
                    f"<b>{entry['title']}</b>\n"
                    f"publish_id: <code>{publish_id}</code>"
                )
                await self._edit_message(msg_id, chat_id, new_text)
                self.console.print(
                    f"[green]✅ WeChat draft published via Telegram: "
                    f"{entry['draft_id']} → publish_id={publish_id}[/green]"
                )
            else:
                errcode = pub.get("errcode", "unknown")
                errmsg = pub.get("errmsg", str(pub))
                await self._answer_callback(cb_id, f"发布失败: errcode={errcode}")
                self.console.print(
                    f"[red]❌ Telegram approval publish failed: "
                    f"errcode={errcode} errmsg={errmsg}[/red]"
                )
        except Exception as e:
            await self._answer_callback(cb_id, f"发布异常: {e}")
            self.console.print(f"[red]❌ Telegram approval error: {e}[/red]")

    async def _do_reject(
        self, cb_id: str, msg_id: int, chat_id: str, entry: dict
    ) -> None:
        lang = entry.get("lang", "?").upper()
        self.registry.mark_rejected(entry["draft_id"])
        new_text = (
            f"🗑️ <b>已废弃 | {lang}</b>\n"
            f"<b>{entry['title']}</b>\n"
            f"草稿未发布，可在公众号后台手动处理"
        )
        await self._edit_message(msg_id, chat_id, new_text)
        await self._answer_callback(cb_id, "已废弃")
        self.console.print(
            f"[dim]🗑️  WeChat draft rejected via Telegram: {entry['draft_id']}[/dim]"
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _answer_callback(self, cb_id: str, text: str) -> None:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                await client.post(
                    f"{TG_BASE}{self.bot_token}/answerCallbackQuery",
                    json={"callback_query_id": cb_id, "text": text},
                )
        except Exception:
            pass

    async def _edit_message(self, msg_id: int, chat_id: str, text: str) -> None:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                await client.post(
                    f"{TG_BASE}{self.bot_token}/editMessageText",
                    json={
                        "chat_id": chat_id,
                        "message_id": msg_id,
                        "text": text,
                        "parse_mode": "HTML",
                    },
                )
        except Exception as e:
            logger.error(f"Telegram editMessageText error: {e}")
