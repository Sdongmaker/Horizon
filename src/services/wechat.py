"""WeChat Official Account publishing service.

Workflow: material/add_material -> draft/add -> freepublish/submit -> freepublish/get
Requires: verified account (微信认证), IP whitelisted, AppID + AppSecret
"""

from io import BytesIO

import httpx

BASE_URL = "https://api.weixin.qq.com/cgi-bin"


class WeChatClient:
    def __init__(self, appid: str, secret: str):
        self.appid = appid
        self.secret = secret
        self._token: str | None = None

    async def _ensure_token(self) -> str:
        if self._token:
            return self._token
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{BASE_URL}/token",
                params={
                    "grant_type": "client_credential",
                    "appid": self.appid,
                    "secret": self.secret,
                },
            )
            data = resp.json()
            if "errcode" in data:
                raise RuntimeError(f"access_token failed: {data}")
            self._token = data["access_token"]
            return self._token

    async def test_connection(self) -> bool:
        try:
            await self._ensure_token()
            return True
        except RuntimeError:
            return False

    # ---- material ----

    async def upload_permanent_image(self, filename: str, data: bytes) -> dict:
        """Upload permanent image material → returns {"media_id": "...", "url": "..."}."""
        token = await self._ensure_token()
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{BASE_URL}/material/add_material",
                params={"access_token": token, "type": "image"},
                files={"media": (filename, data, "image/jpeg")},
            )
            return resp.json()

    async def upload_content_image(self, file_path: str) -> dict:
        """Upload image for inline article content → returns {"url": "..."}."""
        token = await self._ensure_token()
        async with httpx.AsyncClient() as client:
            with open(file_path, "rb") as f:
                resp = await client.post(
                    f"{BASE_URL}/media/uploadimg",
                    params={"access_token": token},
                    files={"media": f},
                )
            return resp.json()

    # ---- drafts ----

    async def create_draft(
        self,
        *,
        title: str,
        content: str,
        thumb_media_id: str,
        author: str = "",
        digest: str = "",
        content_source_url: str = "",
        need_open_comment: bool = False,
    ) -> dict:
        """Create a draft. `thumb_media_id` is required — upload a permanent image first."""
        token = await self._ensure_token()
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{BASE_URL}/draft/add",
                params={"access_token": token},
                json={
                    "articles": [
                        {
                            "title": title,
                            "author": author,
                            "digest": digest,
                            "content": content,
                            "content_source_url": content_source_url,
                            "thumb_media_id": thumb_media_id,
                            "need_open_comment": 1 if need_open_comment else 0,
                        }
                    ]
                },
            )
            return resp.json()

    async def get_draft(self, media_id: str) -> dict:
        token = await self._ensure_token()
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{BASE_URL}/draft/get",
                params={"access_token": token},
                json={"media_id": media_id},
            )
            return resp.json()

    async def list_drafts(self, offset: int = 0, count: int = 20) -> dict:
        token = await self._ensure_token()
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{BASE_URL}/draft/batchget",
                params={"access_token": token},
                json={"offset": offset, "count": count, "no_content": 0},
            )
            return resp.json()

    async def delete_draft(self, media_id: str) -> dict:
        token = await self._ensure_token()
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{BASE_URL}/draft/delete",
                params={"access_token": token},
                json={"media_id": media_id},
            )
            return resp.json()

    # ---- publish ----

    async def publish_draft(self, media_id: str) -> dict:
        """Submit a draft for publishing. Returns {"publish_id": "...", "msg_data_id": "..."}."""
        token = await self._ensure_token()
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{BASE_URL}/freepublish/submit",
                params={"access_token": token},
                json={"media_id": media_id},
            )
            return resp.json()

    async def check_publish_status(self, publish_id: str) -> dict:
        token = await self._ensure_token()
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{BASE_URL}/freepublish/get",
                params={"access_token": token},
                json={"publish_id": publish_id},
            )
            return resp.json()

    async def list_published(self, offset: int = 0, count: int = 10) -> dict:
        token = await self._ensure_token()
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{BASE_URL}/freepublish/batchget",
                params={"access_token": token},
                json={"offset": offset, "count": count},
            )
            return resp.json()

    # ---- cover image helper ----

    @staticmethod
    def generate_cover_image(width: int = 900, height: int = 383) -> bytes:
        """Generate a solid-color placeholder cover (2.35:1). Needs `pip install Pillow`."""
        from PIL import Image

        buf = BytesIO()
        Image.new("RGB", (width, height), color=(30, 100, 180)).save(
            buf, format="JPEG", quality=85
        )
        return buf.getvalue()
