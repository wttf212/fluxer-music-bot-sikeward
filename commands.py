import asyncio
import os
from urllib.parse import quote as url_quote
from fluxer import Message
from fluxer.http import Route
from track_queue import Track
from audio_player import is_playlist_url, extract_playlist_info
from guild_settings import get_allowed_channel, set_allowed_channel


PLAYLIST_EMOJI = "\u2705"  # ✅


async def check_channel(bot, message: Message) -> bool:
    """Check if command is in the allowed channel. Deletes message and notifies if not."""
    guild_id = str(message.guild_id) if message.guild_id else None
    if not guild_id:
        return True

    allowed = get_allowed_channel(guild_id)
    if not allowed:
        return True

    if str(message.channel_id) == allowed:
        return True

    # Wrong channel: delete and notify
    try:
        await message.delete()
    except Exception:
        pass
    await bot._http.send_message(
        allowed,
        content=f"<@{message.author.id}>, please use commands in <#{allowed}>.",
    )
    return False


# ---------------------------------------------------------------------------
# Reaction helpers (fluxer.py HTTP client doesn't have these built-in)
# ---------------------------------------------------------------------------

async def add_reaction(http, channel_id, message_id, emoji: str):
    """PUT /channels/{channel_id}/messages/{message_id}/reactions/{emoji}/@me"""
    encoded = url_quote(emoji, safe="")
    route = Route(
        "PUT",
        "/channels/{channel_id}/messages/{message_id}/reactions/" + encoded + "/@me",
        channel_id=channel_id,
        message_id=message_id,
    )
    await http.request(route)


# ---------------------------------------------------------------------------
# Playlist interaction handler
# ---------------------------------------------------------------------------

async def handle_playlist_reaction(bot, data: dict):
    """Called when MESSAGE_REACTION_ADD fires. Loads remaining playlist tracks if ✅ on a pending message."""
    emoji = data.get("emoji", {})
    emoji_name = emoji.get("name", "")
    if emoji_name != PLAYLIST_EMOJI:
        return

    message_id = data.get("message_id", "")
    user_id = data.get("user_id", "")

    # Ignore the bot's own reaction
    if bot.user and str(user_id) == str(bot.user.id):
        return

    pending = bot.pending_playlists.get(str(message_id))
    if not pending:
        return

    # Remove from pending so it can't be triggered twice
    bot.pending_playlists.pop(str(message_id), None)
    # Also clean up channel reference
    channel_id = pending["channel_id"]
    if bot.pending_playlists.get(f"channel_{channel_id}") == str(message_id):
        bot.pending_playlists.pop(f"channel_{channel_id}", None)

    tracks = pending["tracks"]
    if not tracks:
        await bot._http.send_message(channel_id, content="No remaining tracks to load.")
        return

    # Enqueue all remaining tracks
    for t in tracks:
        track = Track(query=t["url"], title=t["title"], requested_by=str(user_id))
        bot.queue.add(track)

    await bot._http.send_message(
        channel_id,
        content=f"📋 Added **{len(tracks)}** tracks to the queue.",
    )


def register_commands(bot):
    @bot.command()
    async def settc(message: Message):
        owner_id = str(bot.config.get("owner_id", ""))
        if str(message.author.id) != owner_id:
            await message.reply("Only the bot owner can use this command.")
            return

        guild_id = str(message.guild_id) if message.guild_id else None
        if not guild_id:
            await message.reply("This command can only be used in a server.")
            return

        channel_id = str(message.channel_id)
        set_allowed_channel(guild_id, channel_id)
        await message.reply(f"Commands are now restricted to <#{channel_id}>.")

    @bot.command()
    async def play(message: Message):
        if not await check_channel(bot, message):
            return

        query = message.content[len(bot.command_prefix) + 4:].strip()
        if not query:
            await message.reply("Usage: `!play <url or search>`")
            return

        user_id = str(message.author.id)
        guild_id = str(message.guild_id)
        voice_channel = bot.voice_states.get(user_id)
        if not voice_channel:
            await message.reply("You need to be in a voice channel.")
            return

        # ---------------------------------------------------------------
        # Playlist detection: play first track, offer to add the rest
        # ---------------------------------------------------------------
        if is_playlist_url(query):
            status_msg = await message.reply("🔍 Fetching playlist info...")
            try:
                yt_client = bot.config.get("youtube", {}).get("client", "web")
                playlist_info = await asyncio.get_event_loop().run_in_executor(
                    None, extract_playlist_info, query, yt_client
                )
            except Exception as e:
                await status_msg.edit(content=f"Error fetching playlist: {e}")
                return

            tracks = playlist_info["tracks"]
            if not tracks:
                await status_msg.edit(content="No tracks found in this playlist.")
                return

            playlist_title = playlist_info["title"]
            first_track_info = tracks[0]
            remaining_tracks = tracks[1:]

            # Join voice if not already connected
            if not bot.in_voice:
                bot.current_guild_id = guild_id
                bot.voice_ready.clear()
                await bot.send_voice_state_update(guild_id, voice_channel)
                try:
                    await asyncio.wait_for(bot.voice_ready.wait(), timeout=10)
                except asyncio.TimeoutError:
                    await status_msg.edit(content="Timed out waiting for voice connection.")
                    return

            # Play the first track immediately
            try:
                title = await bot.player.play(first_track_info["url"])
            except Exception as e:
                await status_msg.edit(content=f"Error playing first track: {e}")
                return

            channel_id = str(message.channel_id)

            if not remaining_tracks:
                await status_msg.edit(
                    content=f"▶️ Now playing: **{title}** (from **{playlist_title}**)"
                )
                _start_auto_next(bot, channel_id)
                return

            # Show offer to load the rest
            count = len(remaining_tracks)
            await status_msg.edit(
                content=(
                    f"▶️ Now playing: **{title}**\n"
                    f"📋 **{playlist_title}** has **{count}** more tracks.\n"
                    f"React ✅ or type `{bot.command_prefix}loadall` to add them to the queue."
                )
            )

            # Try adding ✅ reaction
            try:
                await add_reaction(bot._http, message.channel_id, status_msg.id, PLAYLIST_EMOJI)
            except Exception as e:
                print(f"[commands] Could not add reaction: {e}")

            # Store pending playlist for reaction or !loadall
            bot.pending_playlists[str(status_msg.id)] = {
                "query": query,
                "user_id": user_id,
                "guild_id": guild_id,
                "channel_id": channel_id,
                "tracks": remaining_tracks,
                "playlist_title": playlist_title,
            }
            # Also store by channel for !loadall lookup
            bot.pending_playlists[f"channel_{channel_id}"] = str(status_msg.id)

            _start_auto_next(bot, channel_id)

            # Auto-expire after 120 seconds
            async def _expire_playlist(msg_id: str, ch_id: str):
                await asyncio.sleep(120)
                removed = bot.pending_playlists.pop(msg_id, None)
                # Also clean up channel reference
                if bot.pending_playlists.get(f"channel_{ch_id}") == msg_id:
                    bot.pending_playlists.pop(f"channel_{ch_id}", None)
                if removed:
                    try:
                        await bot._http.edit_message(
                            ch_id, msg_id,
                            content=(
                                f"▶️ Now playing: **{title}**\n"
                                f"📋 **{playlist_title}** had **{count}** more tracks.\n"
                                f"~~React ✅ or type `{bot.command_prefix}loadall`~~ *(expired)*"
                            )
                        )
                    except Exception:
                        pass

            asyncio.create_task(_expire_playlist(str(status_msg.id), channel_id))
            return

        # ---------------------------------------------------------------
        # Single track flow (existing behaviour)
        # ---------------------------------------------------------------

        # Join voice if not already connected
        if not bot.in_voice:
            bot.current_guild_id = guild_id
            bot.voice_ready.clear()
            await bot.send_voice_state_update(guild_id, voice_channel)
            try:
                await asyncio.wait_for(bot.voice_ready.wait(), timeout=10)
            except asyncio.TimeoutError:
                await message.reply("Timed out waiting for voice connection.")
                return

        track = Track(query=query, title="Resolving...", requested_by=user_id)
        channel_id = message.channel_id

        if bot.player.is_playing:
            bot.queue.add(track)
            await message.reply(f"Added to queue: **{query}**")
        else:
            await message.reply(f"Now playing: **{query}**")
            try:
                title = await bot.player.play(query)
                track.title = title
                _start_auto_next(bot, channel_id)
            except Exception as e:
                await message.reply(f"Error playing track: {e}")

    @bot.command()
    async def stop(message: Message):
        if not await check_channel(bot, message):
            return

        guild_id = str(message.guild_id) if message.guild_id else None
        bot.player.stop_playback()
        bot.queue.clear()
        if guild_id:
            await bot.send_voice_state_update(guild_id, None)
        await bot.player.disconnect()
        bot.in_voice = False
        bot.current_guild_id = None
        await message.reply("Stopped playback and left voice.")

    @bot.command()
    async def skip(message: Message):
        if not await check_channel(bot, message):
            return

        channel_id = message.channel_id
        # Cancel the existing auto-next task and immediately invalidate its
        # generation so it self-terminates before popping any tracks.
        if bot._auto_next_task and not bot._auto_next_task.done():
            bot._auto_next_task.cancel()
            bot._auto_next_task = None
        bot._auto_next_gen = getattr(bot, '_auto_next_gen', 0) + 1
        bot.player.stop_playback()
        next_track = bot.queue.next()
        if next_track:
            try:
                title = await bot.player.play(next_track.query)
                next_track.title = title
                await message.reply(f"Skipped. Now playing: **{title}**")
                _start_auto_next(bot, channel_id)
            except Exception as e:
                await message.reply(f"Error playing next track: {e}")
        else:
            await message.reply("Skipped. Queue is empty.")

    @bot.command()
    async def queue(message: Message):
        if not await check_channel(bot, message):
            return

        tracks = bot.queue.list()
        if not tracks:
            msg = "Queue is empty."
            if bot.player.current_track_title:
                msg = f"Now playing: **{bot.player.current_track_title}**\nQueue is empty."
        else:
            lines = []
            if bot.player.current_track_title:
                lines.append(f"Now playing: **{bot.player.current_track_title}**")
            for i, t in enumerate(tracks, 1):
                lines.append(f"{i}. {t.query}")
            msg = "\n".join(lines)
        await message.reply(msg)

    @bot.command()
    async def loadall(message: Message):
        """Load remaining playlist tracks from the most recent pending playlist in this channel."""
        if not await check_channel(bot, message):
            return

        channel_id = str(message.channel_id)
        msg_id = bot.pending_playlists.get(f"channel_{channel_id}")
        if not msg_id:
            await message.reply("No pending playlist to load.")
            return

        pending = bot.pending_playlists.pop(msg_id, None)
        bot.pending_playlists.pop(f"channel_{channel_id}", None)
        if not pending:
            await message.reply("No pending playlist to load.")
            return

        tracks = pending["tracks"]
        user_id = str(message.author.id)
        channel_id_str = str(message.channel_id)

        for t in tracks:
            track = Track(query=t["url"], title=t["title"], requested_by=user_id)
            bot.queue.add(track)

        await message.reply(f"📋 Added **{len(tracks)}** tracks to the queue.")

        # Start playback if nothing is currently playing
        if not bot.player.is_playing:
            next_track = bot.queue.next()
            if next_track:
                try:
                    title = await bot.player.play(next_track.query)
                    next_track.title = title
                    await bot._http.send_message(channel_id_str, content=f"▶️ Now playing: **{title}**")
                    _start_auto_next(bot, channel_id_str)
                except Exception as e:
                    await bot._http.send_message(channel_id_str, content=f"Error playing track: {e}")

    @bot.command()
    async def shutdown(message: Message):
        if not await check_channel(bot, message):
            return

        guild_id = str(message.guild_id) if message.guild_id else None
        await message.reply("Shutting down...")
        if bot._auto_next_task and not bot._auto_next_task.done():
            bot._auto_next_task.cancel()
            bot._auto_next_task = None
        bot.player.stop_playback()
        bot.queue.clear()
        if guild_id:
            await bot.send_voice_state_update(guild_id, None)
        await bot.player.disconnect()
        print("[main] Shutdown requested via command.")
        os._exit(0)


def _start_auto_next(bot, channel_id):
    """Cancel any existing auto-next chain and start a fresh one."""
    if bot._auto_next_task and not bot._auto_next_task.done():
        bot._auto_next_task.cancel()
    # Increment generation so any surviving zombie tasks self-terminate
    gen = getattr(bot, '_auto_next_gen', 0) + 1
    bot._auto_next_gen = gen
    bot._auto_next_task = asyncio.create_task(_auto_next(bot, channel_id, gen))


async def _auto_next(bot, channel_id, generation):
    """Wait for current track to end, then play next in queue."""
    consecutive_errors = 0
    MAX_CONSECUTIVE_ERRORS = 3
    try:
        while True:
            # If a newer auto-next was started, this one is a zombie — exit
            if getattr(bot, '_auto_next_gen', 0) != generation:
                return
            await bot.player.wait_for_playback()
            # Check again after waking up
            if getattr(bot, '_auto_next_gen', 0) != generation:
                return
            if bot.player.is_playing:
                break  # something else started playing
            next_track = bot.queue.next()
            if not next_track:
                break  # queue empty
            try:
                title = await bot.player.play(next_track.query)
                next_track.title = title
                consecutive_errors = 0  # reset on success
                await bot._http.send_message(channel_id, content=f"Now playing: **{title}**")
            except Exception as e:
                consecutive_errors += 1
                await bot._http.send_message(channel_id, content=f"Error playing track, skipping: {e}")
                if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                    await bot._http.send_message(channel_id, content=f"Too many consecutive errors ({MAX_CONSECUTIVE_ERRORS}), stopping auto-play.")
                    break
                continue  # try the next track instead of dying
    except asyncio.CancelledError:
        pass  # chain cancelled by _start_auto_next or !stop


