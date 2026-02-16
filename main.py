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

from rest_client import FluxerREST
from gateway import FluxerGateway
from audio_player import AudioPlayer
from track_queue import TrackQueue
from commands import CommandHandler


async def main():
    with open("config.yaml", "r") as f:
        config = yaml.safe_load(f)

    token = config["bot_token"]
    prefix = config.get("prefix", "!")

    rest = FluxerREST(token)
    gateway = FluxerGateway(token, rest)
    player = AudioPlayer(config)
    track_queue = TrackQueue()
    handler = CommandHandler(prefix, gateway, rest, player, track_queue)
    handler.register()

    # When we receive VOICE_SERVER_UPDATE, connect the audio player to LiveKit
    async def on_voice_server_update(data: dict):
        endpoint = data.get("endpoint")
        voice_token = data.get("token")
        if endpoint and voice_token:
            try:
                await player.connect_to_voice(endpoint, voice_token)
                handler._in_voice = True
                handler._voice_ready.set()
                print(f"[main] Voice connected to {endpoint}")
            except Exception as e:
                print(f"[main] Voice connect error: {e}")
                handler._voice_ready.set()  # unblock waiter even on error

    gateway.on("VOICE_SERVER_UPDATE", on_voice_server_update)

    try:
        await gateway.connect()
    except KeyboardInterrupt:
        pass
    finally:
        await player.disconnect()
        await gateway.close()
        await rest.close()
        if _bgutil_proc and _bgutil_proc.poll() is None:
            _bgutil_proc.terminate()
            print("[main] bgutil-pot server stopped")


if __name__ == "__main__":
    asyncio.run(main())
