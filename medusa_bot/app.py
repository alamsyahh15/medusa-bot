import aiohttp
import discord
from discord.ext import commands

from .config import (
    DISCORD_TOKEN,
    ENABLE_MEMBERS_INTENT,
    ENABLE_MESSAGE_CONTENT_INTENT,
    HTTP_TIMEOUT_SECONDS,
    set_bot_meta_value,
    should_sync_slash_commands,
)
from .lifecycle import register_lifecycle
from .rating import RatingRequestView
from .stock import StockNotifView
from .slash_commands import register_slash_commands


intents = discord.Intents.default()
intents.message_content = ENABLE_MESSAGE_CONTENT_INTENT
intents.members = ENABLE_MEMBERS_INTENT


class QRISBot(commands.Bot):
    def __init__(self, custom_intents: discord.Intents = None):
        super().__init__(command_prefix="!", intents=custom_intents or intents)
        self.http_session: aiohttp.ClientSession = None

    async def setup_hook(self):
        timeout = aiohttp.ClientTimeout(total=HTTP_TIMEOUT_SECONDS)
        self.http_session = aiohttp.ClientSession(timeout=timeout)
        self.add_view(RatingRequestView())
        self.add_view(StockNotifView())
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


def create_bot(custom_intents: discord.Intents = None) -> QRISBot:
    bot = QRISBot(custom_intents=custom_intents)
    register_slash_commands(bot)
    register_lifecycle(bot)
    return bot


def run_bot():
    if not DISCORD_TOKEN:
        print("❌ DISCORD_TOKEN belum diset!")
        return

    bot = create_bot()
    try:
        bot.run(DISCORD_TOKEN)
    except discord.PrivilegedIntentsRequired:
        print(
            "⚠️ Privileged Intent (Message Content / Server Members) belum diaktifkan di Discord Developer Portal! "
            "Menjalankan ulang dengan fallback intent..."
        )
        fallback_intents = discord.Intents.default()
        fallback_intents.message_content = False
        fallback_intents.members = False
        fallback_bot = create_bot(custom_intents=fallback_intents)
        fallback_bot.run(DISCORD_TOKEN)
