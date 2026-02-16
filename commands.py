import asyncio
import os
from track_queue import Track, TrackQueue
from audio_player import AudioPlayer
from rest_client import FluxerREST
from gateway import FluxerGateway


class CommandHandler:
    def __init__(
        self,
        prefix: str,
        gateway: FluxerGateway,
        rest: FluxerREST,
        player: AudioPlayer,
        track_queue: TrackQueue,
    ):
        self._prefix = prefix
        self._gateway = gateway
        self._rest = rest
        self._player = player
        self._queue = track_queue
        self._voice_states: dict[str, str] = {}  # user_id -> channel_id
        self._current_guild_id: str | None = None
        self._in_voice = False
        self._voice_ready = asyncio.Event()

    def register(self):
        self._gateway.on("MESSAGE_CREATE", self._on_message)
        self._gateway.on("VOICE_STATE_UPDATE", self._on_voice_state_update)

    def _on_voice_state_update(self, data: dict):
        user_id = data.get("user_id")
        channel_id = data.get("channel_id")
        if user_id:
            if channel_id:
                self._voice_states[user_id] = channel_id
            else:
                self._voice_states.pop(user_id, None)

    async def _on_message(self, data: dict):
        # Ignore messages from the bot itself
        author = data.get("author", {})
        if self._gateway.bot_user and author.get("id") == self._gateway.bot_user.get("id"):
            return

        content: str = data.get("content", "")
        if not content.startswith(self._prefix):
            return

        channel_id = data.get("channel_id")
        guild_id = data.get("guild_id")
        user_id = author.get("id")
        parts = content[len(self._prefix):].strip().split(maxsplit=1)
        command = parts[0].lower() if parts else ""
        args = parts[1] if len(parts) > 1 else ""

        if command == "play":
            await self._handle_play(channel_id, guild_id, user_id, args)
        elif command == "stop":
            await self._handle_stop(channel_id, guild_id)
        elif command == "skip":
            await self._handle_skip(channel_id)
        elif command == "queue":
            await self._handle_queue(channel_id)
        elif command == "shutdown":
            await self._handle_shutdown(channel_id, guild_id)

    async def _handle_play(self, channel_id: str, guild_id: str, user_id: str, query: str):
        if not query:
            await self._rest.send_message(channel_id, "Usage: `!play <url or search>`")
            return

        voice_channel = self._voice_states.get(user_id)
        if not voice_channel:
            await self._rest.send_message(channel_id, "You need to be in a voice channel.")
            return

        # Join voice if not already connected
        if not self._in_voice:
            self._current_guild_id = guild_id
            self._voice_ready.clear()
            await self._gateway.send_voice_state_update(guild_id, voice_channel)
            try:
                await asyncio.wait_for(self._voice_ready.wait(), timeout=10)
            except asyncio.TimeoutError:
                await self._rest.send_message(channel_id, "Timed out waiting for voice connection.")
                return

        track = Track(query=query, title="Resolving...", requested_by=user_id)

        if self._player.is_playing:
            self._queue.add(track)
            await self._rest.send_message(channel_id, f"Added to queue: **{query}**")
        else:
            await self._rest.send_message(channel_id, f"Now playing: **{query}**")
            try:
                title = await self._player.play(query)
                track.title = title

                # Auto-advance queue when track finishes
                asyncio.create_task(self._auto_next(channel_id))
            except Exception as e:
                await self._rest.send_message(channel_id, f"Error playing track: {e}")

    async def _auto_next(self, channel_id: str):
        """Wait for current track to end, then play next in queue."""
        await self._player.wait_for_playback()
        if not self._player.is_playing:
            next_track = self._queue.next()
            if next_track:
                try:
                    title = await self._player.play(next_track.query)
                    next_track.title = title
                    await self._rest.send_message(channel_id, f"Now playing: **{title}**")
                    asyncio.create_task(self._auto_next(channel_id))
                except Exception as e:
                    await self._rest.send_message(channel_id, f"Error playing next track: {e}")

    async def _handle_stop(self, channel_id: str, guild_id: str):
        self._player.stop_playback()
        self._queue.clear()
        if guild_id:
            await self._gateway.send_voice_state_update(guild_id, None)
        await self._player.disconnect()
        self._in_voice = False
        self._current_guild_id = None
        await self._rest.send_message(channel_id, "Stopped playback and left voice.")

    async def _handle_skip(self, channel_id: str):
        self._player.stop_playback()
        next_track = self._queue.next()
        if next_track:
            try:
                title = await self._player.play(next_track.query)
                next_track.title = title
                await self._rest.send_message(channel_id, f"Skipped. Now playing: **{title}**")
                asyncio.create_task(self._auto_next(channel_id))
            except Exception as e:
                await self._rest.send_message(channel_id, f"Error playing next track: {e}")
        else:
            await self._rest.send_message(channel_id, "Skipped. Queue is empty.")

    async def _handle_queue(self, channel_id: str):
        tracks = self._queue.list()
        if not tracks:
            msg = "Queue is empty."
            if self._player.current_track_title:
                msg = f"Now playing: **{self._player.current_track_title}**\nQueue is empty."
        else:
            lines = []
            if self._player.current_track_title:
                lines.append(f"Now playing: **{self._player.current_track_title}**")
            for i, t in enumerate(tracks, 1):
                lines.append(f"{i}. {t.query}")
            msg = "\n".join(lines)
        await self._rest.send_message(channel_id, msg)

    async def _handle_shutdown(self, channel_id: str, guild_id: str):
        """Safely shut down the bot."""
        await self._rest.send_message(channel_id, "Shutting down... 👋")
        self._player.stop_playback()
        self._queue.clear()
        if guild_id:
            await self._gateway.send_voice_state_update(guild_id, None)
        await self._player.disconnect()
        await self._gateway.close()
        await self._rest.close()
        print("[main] Shutdown requested via command.")
        # Give a moment for the message to send, then exit
        await asyncio.sleep(0.5)
        os._exit(0)
