import aiohttp

API_BASE = "https://api.fluxer.app/v1"


class FluxerREST:
    def __init__(self, token: str):
        self._token = token
        self._session: aiohttp.ClientSession | None = None

    def _headers(self) -> dict:
        return {"Authorization": f"Bot {self._token}"}

    async def _ensure_session(self):
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()

    async def get_gateway_url(self) -> str:
        await self._ensure_session()
        async with self._session.get(
            f"{API_BASE}/gateway/bot", headers=self._headers()
        ) as resp:
            resp.raise_for_status()
            data = await resp.json()
            return data["url"]

    async def send_message(self, channel_id: str, content: str):
        await self._ensure_session()
        async with self._session.post(
            f"{API_BASE}/channels/{channel_id}/messages",
            headers=self._headers(),
            json={"content": content},
        ) as resp:
            resp.raise_for_status()
            return await resp.json()

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()
