from discord.ext import tasks

from .config import get_leaderboard_config, set_leaderboard_config
from .helpers import post_or_edit_leaderboard


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

    return leaderboard_scheduler

