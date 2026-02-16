import asyncio
import shutil
import subprocess
import threading
import numpy as np
from livekit import rtc
from yt_dlp import YoutubeDL


def _find_ffmpeg(config_path: str) -> str:
    """Resolve ffmpeg binary: config path > PATH > imageio_ffmpeg fallback."""
    if config_path and config_path != "ffmpeg":
        return config_path
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        return "ffmpeg"


def _is_youtube(query: str) -> bool:
    return any(h in query for h in ("youtube.com", "youtu.be", "music.youtube.com"))


def get_audio_url(query: str, client: str, debug: bool = False) -> dict:
    """Extract audio URL and title via yt-dlp. Supports YouTube, SoundCloud, and others."""
    ydl_opts = {
        "format": "bestaudio/best",
        "noplaylist": True,
        "quiet": not debug,
        "no_warnings": not debug,
        "verbose": debug,
    }

    # Only apply YouTube-specific extractor args for YouTube URLs/searches
    is_yt = _is_youtube(query) or not query.startswith(("http://", "https://"))
    if is_yt:
        # client can be comma-separated, e.g. "web,android_vr"
        # PO tokens are generated automatically by the bgutil plugin
        yt_args = {"player_client": [c.strip() for c in client.split(",")]}
        ydl_opts["extractor_args"] = {"youtube": yt_args}

    if debug:
        print(f"[debug][yt-dlp] Query: {query}")
        print(f"[debug][yt-dlp] Is YouTube: {is_yt}")
        print(f"[debug][yt-dlp] ydl_opts: { {k: v for k, v in ydl_opts.items() if k != 'extractor_args'} }")
        if is_yt:
            print(f"[debug][yt-dlp] YouTube client(s): {client}")

    if not query.startswith(("http://", "https://")):
        query = f"ytsearch:{query}"

    with YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(query, download=False)
        if "entries" in info:
            info = info["entries"][0]

        # Extract HTTP headers that yt-dlp wants us to use (critical for YouTube)
        http_headers = info.get("http_headers", {})

        if debug:
            print(f"[debug][yt-dlp] Title: {info.get('title', 'Unknown')}")
            print(f"[debug][yt-dlp] Extractor: {info.get('extractor', 'N/A')}")
            print(f"[debug][yt-dlp] Format: {info.get('format', 'N/A')}")
            print(f"[debug][yt-dlp] Format ID: {info.get('format_id', 'N/A')}")
            print(f"[debug][yt-dlp] Audio codec: {info.get('acodec', 'N/A')}")
            print(f"[debug][yt-dlp] Video codec: {info.get('vcodec', 'N/A')}")
            print(f"[debug][yt-dlp] Audio bitrate (abr): {info.get('abr', 'N/A')}")
            print(f"[debug][yt-dlp] Sample rate: {info.get('asr', 'N/A')}")
            print(f"[debug][yt-dlp] Filesize: {info.get('filesize', 'N/A')}")
            print(f"[debug][yt-dlp] Duration: {info.get('duration', 'N/A')}s")
            url = info.get("url", "")
            print(f"[debug][yt-dlp] URL length: {len(url)}")
            print(f"[debug][yt-dlp] URL prefix: {url[:120]}...")
            print(f"[debug][yt-dlp] URL contains 'googlevideo': {'googlevideo' in url}")
            print(f"[debug][yt-dlp] URL contains 'soundcloud': {'soundcloud' in url}")
            print(f"[debug][yt-dlp] HTTP headers from yt-dlp: {http_headers}")
            # Log all available formats for comparison
            formats = info.get("formats", [])
            print(f"[debug][yt-dlp] Total formats available: {len(formats)}")
            for i, fmt in enumerate(formats[-5:]):  # Show last 5 (usually best quality)
                print(f"[debug][yt-dlp]   format[{i}]: id={fmt.get('format_id')} "
                      f"ext={fmt.get('ext')} acodec={fmt.get('acodec')} "
                      f"vcodec={fmt.get('vcodec')} abr={fmt.get('abr')} "
                      f"protocol={fmt.get('protocol')}")
        # Always log PO token and visitor data status (even when debug=False)
        url = info.get("url", "")
        if is_yt:
            # Check if PO token is present in the URL
            if "pot=" in url:
                pot_start = url.index("pot=") + 4
                pot_end = url.index("&", pot_start) if "&" in url[pot_start:] else len(url)
                pot_val = url[pot_start:pot_end]
                print(f"[yt-dlp] PO Token: present ({len(pot_val)} chars)")
            else:
                print("[yt-dlp] PO Token: not present in URL")

            # Check for visitor data in cookies
            cookies = info.get("cookies", "")
            visitor_data = ""
            for cookie in info.get("http_headers", {}).get("Cookie", "").split(";"):
                if "VISITOR_INFO1_LIVE" in cookie:
                    visitor_data = cookie.split("=", 1)[-1].strip()
                    break
            if visitor_data:
                print(f"[yt-dlp] Visitor Data: present ({len(visitor_data)} chars)")
            else:
                print("[yt-dlp] Visitor Data: not present in cookies")

        return {"url": info["url"], "title": info.get("title", "Unknown"), "http_headers": http_headers}


class AudioPlayer:
    def __init__(self, config: dict):
        self._config = config
        self._ffmpeg: subprocess.Popen | None = None
        self._room: rtc.Room | None = None
        self._source: rtc.AudioSource | None = None
        self._play_task: asyncio.Task | None = None
        self.is_playing = False
        self.current_track_title: str | None = None

        self._sample_rate = config["audio"]["sample_rate"]
        self._channels = config["audio"]["channels"]
        self._frame_ms = config["audio"]["frame_duration_ms"]
        self._ffmpeg_path = _find_ffmpeg(config.get("ffmpeg_path", "ffmpeg"))
        self._debug = config.get("debug", False)

        if self._debug:
            print(f"[debug][player] Initialized AudioPlayer")
            print(f"[debug][player]   sample_rate={self._sample_rate}, channels={self._channels}, frame_ms={self._frame_ms}")
            print(f"[debug][player]   ffmpeg_path={self._ffmpeg_path}")

    async def connect_to_voice(self, endpoint: str, token: str):
        """Connect to a LiveKit room. Audio track is published on first play."""
        if self._debug:
            print(f"[debug][player] connect_to_voice called")
            print(f"[debug][player]   endpoint={endpoint}")

        # Disconnect existing room if any
        if self._room is not None:
            try:
                await self._room.disconnect()
            except Exception as e:
                if self._debug:
                    print(f"[debug][player] Error disconnecting old room: {e}")
            self._room = None
            self._source = None

        self._room = rtc.Room()
        await self._room.connect(endpoint, token)
        print(f"[audio] Connected to LiveKit room")

    async def _ensure_audio_track(self):
        """Create and publish audio source/track if not already done."""
        if self._source is not None:
            if self._debug:
                print(f"[debug][player] Audio track already exists, skipping creation")
            return
        self._source = rtc.AudioSource(self._sample_rate, self._channels)
        track = rtc.LocalAudioTrack.create_audio_track("music", self._source)
        options = rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE)
        await self._room.local_participant.publish_track(track, options)
        print(f"[audio] Audio track published")
        if self._debug:
            print(f"[debug][player] Audio source created: rate={self._sample_rate}, channels={self._channels}")

    async def play(self, url_or_query: str) -> str:
        """Resolve a URL/query and start playback. Returns track title."""
        if self._debug:
            print(f"[debug][player] play() called with: {url_or_query}")

        await self._ensure_audio_track()
        yt = self._config["youtube"]
        info = await asyncio.get_event_loop().run_in_executor(
            None, get_audio_url, url_or_query, yt["client"], self._debug
        )
        audio_url = info["url"]
        title = info["title"]
        http_headers = info.get("http_headers", {})

        if self._debug:
            print(f"[debug][player] Resolved title: {title}")
            print(f"[debug][player] Audio URL length: {len(audio_url)}")
            print(f"[debug][player] HTTP headers to send: {http_headers}")

        self.stop_playback()

        samples_per_frame = (self._sample_rate * self._frame_ms) // 1000
        bytes_per_frame = samples_per_frame * self._channels * 2  # 16-bit = 2 bytes

        if self._debug:
            print(f"[debug][player] samples_per_frame={samples_per_frame}, bytes_per_frame={bytes_per_frame}")

        # Build ffmpeg command with HTTP headers from yt-dlp
        # YouTube returns 403 Forbidden if these headers (especially User-Agent) are missing
        ffmpeg_cmd = [self._ffmpeg_path]

        if http_headers:
            # ffmpeg expects headers as a single string with \r\n separating each header
            headers_str = "".join(f"{k}: {v}\r\n" for k, v in http_headers.items())
            ffmpeg_cmd += ["-headers", headers_str]
            if self._debug:
                print(f"[debug][ffmpeg] Injecting headers: {headers_str!r}")

        ffmpeg_cmd += [
            "-reconnect", "1",
            "-reconnect_streamed", "1", "-reconnect_delay_max", "5",
            "-i", audio_url,
            "-f", "s16le", "-ar", str(self._sample_rate),
            "-ac", str(self._channels), "pipe:1",
        ]

        if self._debug:
            # Show the command (with truncated URL for readability)
            safe_cmd = ffmpeg_cmd.copy()
            url_idx = safe_cmd.index("-i") + 1
            safe_cmd[url_idx] = safe_cmd[url_idx][:80] + "...(truncated)"
            print(f"[debug][ffmpeg] Command: {' '.join(safe_cmd)}")

        # When debug is on, capture stderr so we can see ffmpeg errors
        stderr_target = subprocess.PIPE if self._debug else subprocess.DEVNULL

        self._ffmpeg = subprocess.Popen(
            ffmpeg_cmd,
            stdout=subprocess.PIPE,
            stderr=stderr_target,
        )

        if self._debug:
            print(f"[debug][ffmpeg] Process started, PID={self._ffmpeg.pid}")
            # Read stderr in a background thread to avoid blocking
            self._ffmpeg_stderr_lines = []
            def _read_stderr(proc, lines_list):
                try:
                    for line in proc.stderr:
                        decoded = line.decode("utf-8", errors="replace").rstrip()
                        lines_list.append(decoded)
                        print(f"[debug][ffmpeg-stderr] {decoded}")
                except Exception:
                    pass
            self._stderr_thread = threading.Thread(
                target=_read_stderr, args=(self._ffmpeg, self._ffmpeg_stderr_lines), daemon=True
            )
            self._stderr_thread.start()

        self.is_playing = True
        self.current_track_title = title

        debug = self._debug  # capture for closure

        async def stream_loop():
            loop = asyncio.get_event_loop()
            frames_sent = 0
            total_bytes_read = 0
            try:
                while self.is_playing and self._ffmpeg and self._ffmpeg.poll() is None:
                    data = await loop.run_in_executor(
                        None, self._ffmpeg.stdout.read, bytes_per_frame
                    )
                    if not data:
                        if debug:
                            print(f"[debug][stream] No data received from ffmpeg (EOF or error)")
                            print(f"[debug][stream] ffmpeg returncode: {self._ffmpeg.returncode}")
                            print(f"[debug][stream] ffmpeg poll: {self._ffmpeg.poll()}")
                        break
                    total_bytes_read += len(data)
                    if len(data) < bytes_per_frame:
                        if debug:
                            print(f"[debug][stream] Partial frame: got {len(data)} bytes, expected {bytes_per_frame}, padding with silence")
                        data += b"\x00" * (bytes_per_frame - len(data))

                    frame = rtc.AudioFrame.create(
                        self._sample_rate, self._channels, samples_per_frame
                    )
                    audio_data = np.frombuffer(data, dtype=np.int16)
                    np.copyto(np.frombuffer(frame.data, dtype=np.int16), audio_data)
                    await self._source.capture_frame(frame)
                    frames_sent += 1

                    if debug and frames_sent == 1:
                        print(f"[debug][stream] First frame sent successfully ({len(data)} bytes)")
                    if debug and frames_sent % 500 == 0:
                        elapsed_sec = (frames_sent * self._frame_ms) / 1000
                        print(f"[debug][stream] Sent {frames_sent} frames ({elapsed_sec:.1f}s), total bytes: {total_bytes_read}")
            except asyncio.CancelledError:
                if debug:
                    print(f"[debug][stream] Stream loop cancelled")
            except Exception as e:
                if debug:
                    print(f"[debug][stream] Stream loop error: {type(e).__name__}: {e}")
            finally:
                if debug:
                    exit_code = self._ffmpeg.poll() if self._ffmpeg else "N/A"
                    print(f"[debug][stream] Stream loop ended. Frames sent: {frames_sent}, total bytes: {total_bytes_read}, ffmpeg exit code: {exit_code}")
                self.is_playing = False
                self.current_track_title = None

        self._play_task = asyncio.create_task(stream_loop())
        return title

    def stop_playback(self):
        """Stop ffmpeg and cancel the streaming task."""
        if self._debug:
            print(f"[debug][player] stop_playback() called")
        self.is_playing = False
        self.current_track_title = None
        if self._ffmpeg:
            try:
                exit_code = self._ffmpeg.poll()
                if self._debug:
                    print(f"[debug][player] Killing ffmpeg (PID={self._ffmpeg.pid}, current exit_code={exit_code})")
                self._ffmpeg.kill()
                self._ffmpeg.wait(timeout=2)
            except Exception as e:
                if self._debug:
                    print(f"[debug][player] Error killing ffmpeg: {e}")
            self._ffmpeg = None
        if self._play_task and not self._play_task.done():
            self._play_task.cancel()
            self._play_task = None

    async def wait_for_playback(self):
        """Wait for the current track to finish."""
        if self._play_task:
            await self._play_task

    async def disconnect(self):
        """Stop playback and disconnect from LiveKit."""
        self.stop_playback()
        if self._room:
            await self._room.disconnect()
            self._room = None
            self._source = None
        print("[audio] Disconnected from LiveKit room")
