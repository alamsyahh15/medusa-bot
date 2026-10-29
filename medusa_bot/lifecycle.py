from discord.ext import tasks

from .config import get_autodelete_config, get_leaderboard_config, set_leaderboard_config
from .helpers import (
    handle_autodelete_message,
    handle_raw_autodelete_message_edit,
    post_or_edit_leaderboard,
    sync_autodelete_automod_rule,
)


def register_lifecycle(bot):
    @tasks.loop(minutes=5)
    async def leaderboard_scheduler():
        for guild in bot.guilds:
            await post_or_edit_leaderboard(bot, guild.id, get_leaderboard_config, set_leaderboard_config)

    @leaderboard_scheduler.before_loop
    async def before_scheduler():
        await bot.wait_until_ready()

    @bot.event
    async def on_ready():
        print(f"✅ Bot aktif sebagai {bot.user} (ID: {bot.user.id})")
        print(f"   Terhubung ke {len(bot.guilds)} server")
        if not leaderboard_scheduler.is_running():
            leaderboard_scheduler.start()
            print("✅ Leaderboard scheduler dimulai")

        for guild in bot.guilds:
            cfg = get_autodelete_config(guild.id)
            if cfg.get("enabled"):
                ok, msg = await sync_autodelete_automod_rule(bot, guild)
                print(f"🛡️ Auto-Delete sync [{guild.name}]: {msg}")

    @bot.listen("on_message")
    async def on_message_autodelete(message):
        await handle_autodelete_message(message)

    @bot.listen("on_message_edit")
    async def on_message_edit_autodelete(_before, after):
        await handle_autodelete_message(after)

    @bot.listen("on_raw_message_edit")
    async def on_raw_message_edit_autodelete(payload):
        await handle_raw_autodelete_message_edit(bot, payload)

    return leaderboard_scheduler

