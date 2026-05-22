"""Draft registry for tracking WeChat drafts pending approval via Telegram bot.

Persisted to data/drafts.json — same pattern as subscribers.json.
"""

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

DRAFTS_FILE = "data/drafts.json"


class DraftRegistry:
    """Tracks WeChat drafts awaiting Telegram bot approval."""

    def __init__(self, base_dir: str = "."):
        self._path = Path(base_dir) / DRAFTS_FILE

    def _load(self) -> dict:
        if not self._path.exists():
            return {"drafts": []}
        try:
            return json.loads(self._path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"Failed to read draft registry: {e}")
            return {"drafts": []}

    def _save(self, data: dict) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def register(
        self,
        media_id: str,
        date: str,
        lang: str,
        title: str,
        digest: str,
        telegram_msg_id: int | None = None,
    ) -> str:
        """Register a new draft. Returns the draft_id."""
        import uuid

        draft_id = uuid.uuid4().hex[:12]
        data = self._load()
        data["drafts"].append(
            {
                "draft_id": draft_id,
                "media_id": media_id,
                "date": date,
                "lang": lang,
                "title": title,
                "digest": digest,
                "telegram_msg_id": telegram_msg_id,
                "status": "pending",
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        self._save(data)
        logger.info(f"Draft registered: {draft_id} → media_id={media_id}")
        return draft_id

    def update_telegram_msg_id(self, draft_id: str, msg_id: int) -> None:
        data = self._load()
        for d in data["drafts"]:
            if d["draft_id"] == draft_id:
                d["telegram_msg_id"] = msg_id
                break
        self._save(data)

    def get_by_id(self, draft_id: str) -> Optional[dict]:
        data = self._load()
        for d in data["drafts"]:
            if d["draft_id"] == draft_id:
                return d
        return None

    def get_pending(self) -> list[dict]:
        data = self._load()
        return [d for d in data["drafts"] if d["status"] == "pending"]

    def mark_published(self, draft_id: str) -> Optional[dict]:
        return self._update_status(draft_id, "published")

    def mark_rejected(self, draft_id: str) -> Optional[dict]:
        return self._update_status(draft_id, "rejected")

    def _update_status(self, draft_id: str, status: str) -> Optional[dict]:
        data = self._load()
        for d in data["drafts"]:
            if d["draft_id"] == draft_id:
                d["status"] = status
                d["updated_at"] = datetime.now(timezone.utc).isoformat()
                self._save(data)
                logger.info(f"Draft {draft_id} → {status}")
                return d
        return None

    def cleanup(self, max_age_days: int = 7) -> int:
        """Remove drafts older than N days. Returns count removed."""
        cutoff = datetime.now(timezone.utc).isoformat()
        data = self._load()
        old_len = len(data["drafts"])
        # Simple approach: keep drafts from last N days based on ISO date prefix
        from datetime import timedelta

        cutoff_dt = datetime.now(timezone.utc) - timedelta(days=max_age_days)
        cutoff_str = cutoff_dt.isoformat()
        data["drafts"] = [
            d for d in data["drafts"] if d.get("created_at", "") >= cutoff_str
        ]
        removed = old_len - len(data["drafts"])
        if removed:
            self._save(data)
            logger.info(f"Cleaned up {removed} stale drafts")
        return removed
