import asyncio
import os
import sys
import yaml
from typing import Any

# Load bgutil PO token provider plugin for yt-dlp (must be done before yt-dlp is imported)
# This enables dynamic PO token generation to prevent YouTube IP bans
_base_dir = os.path.dirname(os.path.abspath(__file__))
_plugin_dir = os.path.join(_base_dir, "yt-dlp-plugins", "bgutil-ytdlp-pot-provider")
if os.path.isdir(_plugin_dir) and _plugin_dir not in sys.path:
    sys.path.insert(0, _plugin_dir)

# Ensure deno JS runtime is on PATH (needed for YouTube signature solving)
_deno_dir = os.path.join(os.path.expanduser("~"), ".deno", "bin")
if os.path.isdir(_deno_dir) and _deno_dir not in os.environ.get("PATH", ""):
    os.environ["PATH"] = _deno_dir + os.pathsep + os.environ.get("PATH", "")

# Start bgutil-pot HTTP server in background (PO token provider)
import subprocess
import shutil
_bgutil_name = "bgutil-pot.exe" if sys.platform == "win32" else "bgutil-pot"
_bgutil_path = os.path.join(_base_dir, _bgutil_name)
if not os.path.isfile(_bgutil_path):
    _bgutil_path = shutil.which(_bgutil_name)  # fallback: check PATH
_bgutil_proc = None
if _bgutil_path:
    try:
        _bgutil_proc = subprocess.Popen(
            [_bgutil_path, "server", "--host", "127.0.0.1"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        print(f"[main] bgutil-pot server started (PID {_bgutil_proc.pid})")
    except Exception as e:
        print(f"[main] Warning: could not start bgutil-pot server: {e}")
else:
    print("[main] Warning: bgutil-pot binary not found, PO tokens will not be generated")

import fluxer
from fluxer.gateway import GatewayPayload
from fluxer.enums import GatewayOpcode, Intents
from audio_player import AudioPlayer
from track_queue import TrackQueue


class MusicBot(fluxer.Bot):
    def __init__(self, config: dict):
        intents = Intents.default() | Intents.MESSAGE_CONTENT | Intents.GUILD_VOICE_STATES | Intents.GUILD_MESSAGE_REACTIONS
        super().__init__(command_prefix=config.get("prefix", "!"), intents=intents)
        self.player = AudioPlayer(config)
        self.queue = TrackQueue()
        self.voice_states: dict[str, str] = {}  # user_id -> channel_id
        self.current_guild_id: str | None = None
        self.current_voice_channel_id: str | None = None
        self.current_text_channel_id: str | None = None
        self.in_voice = False
        self.voice_ready = asyncio.Event()
        self.pending_playlists: dict = {}  # message_id -> playlist info
        self._auto_next_task: asyncio.Task | None = None  # prevent duplicate chains
        self._empty_channel_task: asyncio.Task | None = None  # 1-min leave timer

    async def start(self, token: str) -> None:
        """Override to retry the initial API connection indefinitely on failure."""
        attempt = 0
        while True:
            attempt += 1
            self._closed = False
            try:
                await super().start(token)
                return
            except (KeyboardInterrupt, SystemExit):
                raise
            except Exception as e:
                print(f"[main] API connection failed (attempt {attempt}): {e}. Retrying in 5s...")
                await asyncio.sleep(5)

    async def _dispatch(self, event_name: str, data: Any) -> None:
        """Intercept GUILD_CREATE to capture initial voice states before fluxer processes it."""
        if event_name == "GUILD_CREATE" and isinstance(data, dict):
            loaded = 0
            for vs in data.get("voice_states", []):
                user_id = vs.get("user_id")
                channel_id = vs.get("channel_id")
                if user_id and channel_id:
                    self.voice_states[str(user_id)] = str(channel_id)
                    loaded += 1
            print(f"[main] Loaded {loaded} voice states for guild {data.get('id')}")
        await super()._dispatch(event_name, data)

    async def _process_commands(self, message) -> None:
        """Override to fix partial-match false positives and reply on unknown commands."""
        if message.author.bot:
            return
        if not message.content.startswith(self.command_prefix):
            return

        content = message.content[len(self.command_prefix):]
        for cmd, handler in self._commands.items():
            # Require exact match or command followed by a space (prevents !skipping → !skip)
            if content == cmd or content.startswith(cmd + " "):
                if handler:
                    try:
                        await handler(message)
                    except Exception as e:
                        import logging
                        logging.getLogger(__name__).exception("Error in command '%s'", cmd)
                        print(f"[commands] Exception in '{cmd}': {e}")
                return

        await message.reply(f"Unknown command. Type `{self.command_prefix}help` for available commands.")

    async def send_voice_state_update(self, guild_id: str, channel_id: str | None):
        """Send opcode 4 to join/leave a voice channel."""
        self.current_voice_channel_id = channel_id
        payload = GatewayPayload(
            op=GatewayOpcode.VOICE_STATE_UPDATE,
            d={
                "guild_id": guild_id,
                "channel_id": channel_id,
                "self_mute": False,
                "self_deaf": True,
            },
        )
        await self._gateway._send(payload)


def main():
    with open("config.yaml", "r") as f:
        config = yaml.safe_load(f)

    token = config["bot_token"]
    bot = MusicBot(config)
    bot.config = config

    # Register commands
    from commands import register_commands, handle_playlist_reaction
    register_commands(bot)

    async def _do_empty_leave(bot, message: str):
        """Shared teardown: stop playback, clear queue, disconnect, and send a message."""
        guild_id = bot.current_guild_id
        text_channel_id = bot.current_text_channel_id

        bot.queue.clear()
        bot.player.stop_playback()
        bot._auto_next_gen = getattr(bot, '_auto_next_gen', 0) + 1
        if bot._auto_next_task and not bot._auto_next_task.done():
            bot._auto_next_task.cancel()
            bot._auto_next_task = None
        bot._empty_channel_task = None

        bot.in_voice = False
        bot.current_guild_id = None
        if guild_id:
            await bot.send_voice_state_update(guild_id, None)
        await bot.player.disconnect()

        if text_channel_id:
            await bot._http.send_message(text_channel_id, content=message)

    async def _leave_after_timeout(bot):
        """Wait 1 minute, then leave if the channel is still empty."""
        await asyncio.sleep(60)
        if not bot.in_voice or not bot.current_voice_channel_id:
            return
        users_in_channel = sum(
            1 for cid in bot.voice_states.values()
            if cid == bot.current_voice_channel_id
        )
        if users_in_channel > 1:
            return
        await _do_empty_leave(bot, "No one in the voice channel for 1 minute. Leaving.")

    async def _handle_empty_channel(bot):
        if not bot.in_voice or not bot.current_voice_channel_id:
            return
        # Re-check in case someone rejoined between scheduling and running
        users_in_channel = sum(
            1 for cid in bot.voice_states.values()
            if cid == bot.current_voice_channel_id
        )
        if users_in_channel > 1:
            return

        # If music is playing, start a 1-minute timeout instead of leaving immediately
        if bot.player.is_playing:
            if not (bot._empty_channel_task and not bot._empty_channel_task.done()):
                bot._empty_channel_task = asyncio.create_task(_leave_after_timeout(bot))
            return

        await _do_empty_leave(bot, "Everyone left the voice channel. Leaving.")

    @bot.event
    async def on_ready():
        print(f"[main] Ready as {bot.user}")

    @bot.on("message_reaction_add")
    async def on_message_reaction_add(data: dict):
        await handle_playlist_reaction(bot, data)

    @bot.on("voice_state_update")
    async def on_voice_state_update(data: dict):
        user_id = data.get("user_id")
        channel_id = data.get("channel_id")
        if user_id:
            if channel_id:
                bot.voice_states[str(user_id)] = str(channel_id)
            else:
                bot.voice_states.pop(str(user_id), None)

        if bot.in_voice and bot.current_voice_channel_id:
            users_in_channel = sum(
                1 for cid in bot.voice_states.values()
                if cid == bot.current_voice_channel_id
            )
            if users_in_channel <= 1:  # only bot (or nobody) remains
                asyncio.create_task(_handle_empty_channel(bot))
            elif bot._empty_channel_task and not bot._empty_channel_task.done():
                # Someone rejoined — cancel the pending leave timer
                bot._empty_channel_task.cancel()
                bot._empty_channel_task = None

    @bot.on("voice_server_update")
    async def on_voice_server_update(data: dict):
        endpoint = data.get("endpoint")
        voice_token = data.get("token")
        if endpoint and voice_token:
            try:
                await bot.player.connect_to_voice(endpoint, voice_token)
                bot.in_voice = True
                bot.voice_ready.set()
                print(f"[main] Voice connected to {endpoint}")
            except Exception as e:
                print(f"[main] Voice connect error: {e}")
                bot.voice_ready.set()  # unblock waiter even on error

    try:
        bot.run(token)
    finally:
        if _bgutil_proc and _bgutil_proc.poll() is None:
            _bgutil_proc.terminate()
            print("[main] bgutil-pot server stopped")


if __name__ == "__main__":
    main()
