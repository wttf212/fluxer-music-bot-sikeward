import asyncio
import json
import random
import sys
import websockets


class FluxerGateway:
    def __init__(self, token: str, rest):
        self._token = token
        self._rest = rest
        self._ws = None
        self._heartbeat_interval: float = 41.25  # seconds, overridden by hello
        self._sequence: int | None = None
        self._session_id: str | None = None
        self._resume_url: str | None = None
        self._listeners: dict[str, list] = {}
        self._heartbeat_task: asyncio.Task | None = None
        self._running = False
        self.bot_user: dict | None = None

    def on(self, event: str, callback):
        self._listeners.setdefault(event, []).append(callback)

    def _dispatch(self, event: str, data):
        for cb in self._listeners.get(event, []):
            result = cb(data)
            if asyncio.iscoroutine(result):
                asyncio.create_task(result)

    async def _send(self, op: int, d=None):
        payload = {"op": op}
        if d is not None:
            payload["d"] = d
        await self._ws.send(json.dumps(payload))

    async def _identify(self):
        await self._send(2, {
            "token": self._token,
            "properties": {
                "os": sys.platform,
                "browser": "fluxer-music-bot",
                "device": "fluxer-music-bot",
            },
        })

    async def _resume(self):
        await self._send(6, {
            "token": self._token,
            "session_id": self._session_id,
            "seq": self._sequence,
        })

    async def _heartbeat_loop(self):
        try:
            # Initial jitter before first heartbeat
            await asyncio.sleep(self._heartbeat_interval * random.random())
            while self._running:
                await self._send(1, self._sequence)
                await asyncio.sleep(self._heartbeat_interval)
        except (asyncio.CancelledError, websockets.ConnectionClosed):
            pass

    async def send_voice_state_update(self, guild_id: str, channel_id: str | None):
        await self._send(4, {
            "guild_id": guild_id,
            "channel_id": channel_id,
            "self_mute": False,
            "self_deaf": True,
        })

    async def connect(self):
        gateway_url = await self._rest.get_gateway_url()
        url = f"{gateway_url}?v=1&encoding=json"
        await self._connect(url, resume=False)

    async def _connect(self, url: str, resume: bool):
        self._running = True
        try:
            async for self._ws in websockets.connect(url):
                try:
                    await self._handle_connection(resume)
                except websockets.ConnectionClosed as e:
                    print(f"[gateway] Connection closed ({e.code}), reconnecting...")
                finally:
                    resume = self._session_id is not None
                    if self._heartbeat_task:
                        self._heartbeat_task.cancel()
                        self._heartbeat_task = None
        except Exception as e:
            print(f"[gateway] Fatal error: {e}")
            self._running = False
            raise

    async def _handle_connection(self, resume: bool):
        async for raw in self._ws:
            msg = json.loads(raw)
            op = msg.get("op")
            d = msg.get("d")
            t = msg.get("t")
            s = msg.get("s")

            if s is not None:
                self._sequence = s

            if op == 10:  # Hello
                interval_ms = d.get("heartbeat_interval", 41250)
                self._heartbeat_interval = interval_ms / 1000.0
                if self._heartbeat_task:
                    self._heartbeat_task.cancel()
                self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
                if resume and self._session_id:
                    await self._resume()
                else:
                    await self._identify()

            elif op == 11:  # Heartbeat ACK
                pass

            elif op == 1:  # Heartbeat request
                await self._send(1, self._sequence)

            elif op == 7:  # Reconnect
                print("[gateway] Server requested reconnect")
                await self._ws.close()
                break

            elif op == 9:  # Invalid session
                resumable = d if isinstance(d, bool) else False
                print(f"[gateway] Invalid session (resumable={resumable})")
                if not resumable:
                    self._session_id = None
                    self._sequence = None
                await asyncio.sleep(1)
                await self._ws.close()
                break

            elif op == 0:  # Dispatch
                if t == "READY":
                    self._session_id = d.get("session_id")
                    self._resume_url = d.get("resume_gateway_url")
                    self.bot_user = d.get("user")
                    username = self.bot_user.get("username", "?") if self.bot_user else "?"
                    print(f"[gateway] READY as {username}")
                self._dispatch(t, d)

    async def close(self):
        self._running = False
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
        if self._ws:
            await self._ws.close()
