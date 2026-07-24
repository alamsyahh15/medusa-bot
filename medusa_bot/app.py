import aiohttp
import discord
from discord.ext import commands

from .config import DISCORD_TOKEN, ENABLE_MEMBERS_INTENT, HTTP_TIMEOUT_SECONDS, set_bot_meta_value, should_sync_slash_commands
from .lifecycle import register_lifecycle
from .rating import RatingRequestView
from .slash_commands import register_slash_commands


intents = discord.Intents.default()
intents.message_content = False
intents.members = ENABLE_MEMBERS_INTENT


class QRISBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=intents)
        self.http_session: aiohttp.ClientSession = None

    async def setup_hook(self):
        timeout = aiohttp.ClientTimeout(total=HTTP_TIMEOUT_SECONDS)
        self.http_session = aiohttp.ClientSession(timeout=timeout)
        self.add_view(RatingRequestView())
        should_sync, remaining_seconds = should_sync_slash_commands()
        if not should_sync:
            print(f"⏭️ Slash command sync dilewati untuk hindari rate limit. Coba lagi dalam {remaining_seconds} detik.")
            return

        try:
            synced = await self.tree.sync()
            from datetime import datetime, timezone

            set_bot_meta_value("last_slash_sync_at", int(datetime.now(timezone.utc).timestamp()))
            print(f"✅ Slash commands synced: {len(synced)} commands")
        except Exception as e:
            print(f"❌ Sync error: {e}")

    async def close(self):
        if self.http_session:
            await self.http_session.close()
        await super().close()


def create_bot() -> QRISBot:
    bot = QRISBot()
    register_slash_commands(bot)
    register_lifecycle(bot)
    return bot


def run_bot():
    bot = create_bot()
    if not DISCORD_TOKEN:
        print("❌ DISCORD_TOKEN belum diset!")
    else:
        bot.run(DISCORD_TOKEN)
