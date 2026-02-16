import asyncio
import os
import sys
import yaml

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
        intents = Intents.default() | Intents.MESSAGE_CONTENT | Intents.GUILD_VOICE_STATES
        super().__init__(command_prefix=config.get("prefix", "!"), intents=intents)
        self.player = AudioPlayer(config)
        self.queue = TrackQueue()
        self.voice_states: dict[str, str] = {}  # user_id -> channel_id
        self.current_guild_id: str | None = None
        self.in_voice = False
        self.voice_ready = asyncio.Event()

    async def send_voice_state_update(self, guild_id: str, channel_id: str | None):
        """Send opcode 4 to join/leave a voice channel."""
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
    from commands import register_commands
    register_commands(bot)

    @bot.event
    async def on_ready():
        print(f"[main] Ready as {bot.user}")

    @bot.on("voice_state_update")
    async def on_voice_state_update(data: dict):
        user_id = data.get("user_id")
        channel_id = data.get("channel_id")
        if user_id:
            if channel_id:
                bot.voice_states[str(user_id)] = str(channel_id)
            else:
                bot.voice_states.pop(str(user_id), None)

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
