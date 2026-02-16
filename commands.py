import asyncio
import os
from fluxer import Message
from track_queue import Track
from guild_settings import get_allowed_channel, set_allowed_channel


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
                asyncio.create_task(_auto_next(bot, channel_id))
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
        bot.player.stop_playback()
        next_track = bot.queue.next()
        if next_track:
            try:
                title = await bot.player.play(next_track.query)
                next_track.title = title
                await message.reply(f"Skipped. Now playing: **{title}**")
                asyncio.create_task(_auto_next(bot, channel_id))
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
    async def shutdown(message: Message):
        if not await check_channel(bot, message):
            return

        guild_id = str(message.guild_id) if message.guild_id else None
        await message.reply("Shutting down...")
        bot.player.stop_playback()
        bot.queue.clear()
        if guild_id:
            await bot.send_voice_state_update(guild_id, None)
        await bot.player.disconnect()
        await bot.close()
        print("[main] Shutdown requested via command.")
        await asyncio.sleep(0.5)
        os._exit(0)


async def _auto_next(bot, channel_id):
    """Wait for current track to end, then play next in queue."""
    await bot.player.wait_for_playback()
    if not bot.player.is_playing:
        next_track = bot.queue.next()
        if next_track:
            try:
                title = await bot.player.play(next_track.query)
                next_track.title = title
                await bot._http.send_message(channel_id, content=f"Now playing: **{title}**")
                asyncio.create_task(_auto_next(bot, channel_id))
            except Exception as e:
                await bot._http.send_message(channel_id, content=f"Error playing next track: {e}")
