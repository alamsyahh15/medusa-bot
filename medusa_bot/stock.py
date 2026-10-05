import asyncio
import json
import os
from datetime import datetime, timezone
from typing import Optional

import discord
from discord import app_commands

from .helpers import log_debug

NOTIF_USER_FILE = "notif_stock_user.json"
STOCK_INFO_FILE = "stock_info.json"

TYPE_LABELS = {
    "gamepass": "Robux Gamepass",
    "group_payout": "Group Payout",
    "via_username": "Via Username",
    "all": "Semua Tipe",
}


def load_notif_stock_users() -> dict:
    if os.path.exists(NOTIF_USER_FILE):
        try:
            with open(NOTIF_USER_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return {
                    "gamepass": [int(uid) for uid in data.get("gamepass", [])],
                    "group_payout": [int(uid) for uid in data.get("group_payout", [])],
                    "via_username": [int(uid) for uid in data.get("via_username", [])],
                }
        except Exception as e:
            log_debug("load_notif_stock_users.error", error=str(e))
    return {"gamepass": [], "group_payout": [], "via_username": []}


def save_notif_stock_users(data: dict):
    clean_data = {
        "gamepass": sorted(list(set(int(uid) for uid in data.get("gamepass", [])))),
        "group_payout": sorted(list(set(int(uid) for uid in data.get("group_payout", [])))),
        "via_username": sorted(list(set(int(uid) for uid in data.get("via_username", [])))),
    }
    with open(NOTIF_USER_FILE, "w", encoding="utf-8") as f:
        json.dump(clean_data, f, indent=2)


def toggle_notif_stock_user(user_id: int, type_key: str) -> bool:
    data = load_notif_stock_users()
    user_list = data.get(type_key, [])
    if user_id in user_list:
        user_list.remove(user_id)
        is_subscribed = False
    else:
        user_list.append(user_id)
        is_subscribed = True
    data[type_key] = user_list
    save_notif_stock_users(data)
    return is_subscribed


def load_stock_info() -> dict:
    if os.path.exists(STOCK_INFO_FILE):
        try:
            with open(STOCK_INFO_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return {
                    "gamepass": int(data.get("gamepass", 0)),
                    "group_payout": int(data.get("group_payout", 0)),
                    "via_username": int(data.get("via_username", 0)),
                    "last_updated": int(data.get("last_updated", int(datetime.now(timezone.utc).timestamp()))),
                    "embed_channel_id": data.get("embed_channel_id"),
                    "embed_message_id": data.get("embed_message_id"),
                }
        except Exception as e:
            log_debug("load_stock_info.error", error=str(e))
    return {
        "gamepass": 0,
        "group_payout": 0,
        "via_username": 0,
        "last_updated": int(datetime.now(timezone.utc).timestamp()),
        "embed_channel_id": None,
        "embed_message_id": None,
    }


def save_stock_info(data: dict):
    with open(STOCK_INFO_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def format_robux_amount(amount: int) -> str:
    return f"{amount:,}".replace(",", ".")


def build_stock_embed(guild_name: Optional[str] = None) -> discord.Embed:
    stock_data = load_stock_info()
    gp_stock = format_robux_amount(stock_data["gamepass"])
    grp_stock = format_robux_amount(stock_data["group_payout"])
    usr_stock = format_robux_amount(stock_data["via_username"])
    last_updated = stock_data["last_updated"]

    title = "📦 Medusablox — Stock Info"

    desc = (
        "🛍️ **ROBUX GAMEPASS**\n"
        f"` 🔘 IN STOCK ` **{gp_stock} R$**\n"
        "⏱️ Estimasi: 3–7 hari kerja\n\n"
        "👥 **GROUP PAYOUT**\n"
        f"` 🔘 IN STOCK ` **{grp_stock} R$**\n"
        "⚡ Estimasi: Instant – maks 6 jam\n\n"
        "🆔 **VIA USERNAME**\n"
        f"` 🔘 IN STOCK ` **{usr_stock} R$**\n"
        "⚡ Estimasi: Instant — di atas 5.000 R$ bisa >1 hari\n\n"
        f"Update terakhir <t:{last_updated}:R>\n"
        "Selamat berbelanja! :3"
    )

    embed = discord.Embed(
        title=title,
        description=desc,
        color=0x2B2D31,
    )
    return embed


class StockNotifView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Gamepass",
        style=discord.ButtonStyle.secondary,
        emoji="🔔",
        custom_id="notif_stock:gamepass",
    )
    async def notif_gamepass(self, interaction: discord.Interaction, _button: discord.ui.Button):
        await self.handle_notif_click(interaction, "gamepass", "Robux Gamepass")

    @discord.ui.button(
        label="Group Payout",
        style=discord.ButtonStyle.secondary,
        emoji="🔔",
        custom_id="notif_stock:group_payout",
    )
    async def notif_group_payout(self, interaction: discord.Interaction, _button: discord.ui.Button):
        await self.handle_notif_click(interaction, "group_payout", "Group Payout")

    @discord.ui.button(
        label="Via Username",
        style=discord.ButtonStyle.secondary,
        emoji="🔔",
        custom_id="notif_stock:via_username",
    )
    async def notif_via_username(self, interaction: discord.Interaction, _button: discord.ui.Button):
        await self.handle_notif_click(interaction, "via_username", "Via Username")

    async def handle_notif_click(self, interaction: discord.Interaction, type_key: str, type_label: str):
        is_subbed = toggle_notif_stock_user(interaction.user.id, type_key)
        if is_subbed:
            msg = (
                f"🔔 **Notifikasi Dinyalakan**\n"
                f"Kamu akan menerima pesan DM secara otomatis ketika stok **{type_label}** diupdate!\n\n"
                f"📌 *Pastikan DM dari member server ini terbuka agar pesan notifikasi dapat terkirim.*"
            )
        else:
            msg = (
                f"🔕 **Notifikasi Dimatikan**\n"
                f"Kamu telah membatalkan langganan notifikasi DM untuk stok **{type_label}**."
            )
        await interaction.response.send_message(msg, ephemeral=True)


def register_stock_commands(bot):
    @bot.tree.command(name="notif_stock", description="Kirim embed info stok Robux beserta tombol notifikasi DM")
    @app_commands.default_permissions(administrator=True)
    async def notif_stock_command(interaction: discord.Interaction):
        embed = build_stock_embed(interaction.guild.name if interaction.guild else None)
        view = StockNotifView()
        await interaction.response.send_message(embed=embed, view=view)
        try:
            msg = await interaction.original_response()
            if msg:
                stock_data = load_stock_info()
                stock_data["embed_channel_id"] = msg.channel.id
                stock_data["embed_message_id"] = msg.id
                save_stock_info(stock_data)
        except Exception as e:
            log_debug("notif_stock.save_msg_error", error=str(e))

    @bot.tree.command(name="adjust_stock", description="Update stok Robux dan kirim notifikasi DM ke user")
    @app_commands.describe(
        tipe="Pilih tipe stok yang ingin diupdate",
        stock="Jumlah stok baru dalam Robux (R$)",
        pesan="Catatan atau pengumuman tambahan di DM (opsional)",
    )
    @app_commands.choices(
        tipe=[
            app_commands.Choice(name="Robux Gamepass", value="gamepass"),
            app_commands.Choice(name="Group Payout", value="group_payout"),
            app_commands.Choice(name="Via Username", value="via_username"),
            app_commands.Choice(name="Semua Tipe", value="all"),
        ]
    )
    @app_commands.default_permissions(administrator=True)
    async def adjust_stock_command(
        interaction: discord.Interaction,
        tipe: app_commands.Choice[str],
        stock: int,
        pesan: Optional[str] = None,
    ):
        await interaction.response.defer(ephemeral=True)

        if stock < 0:
            await interaction.followup.send("❌ Jumlah stok tidak boleh negatif.", ephemeral=True)
            return

        type_key = tipe.value
        type_label = TYPE_LABELS.get(type_key, type_key)

        # 1. Update stock_info.json
        stock_data = load_stock_info()
        now_ts = int(datetime.now(timezone.utc).timestamp())
        stock_data["last_updated"] = now_ts

        if type_key == "all":
            stock_data["gamepass"] = stock
            stock_data["group_payout"] = stock
            stock_data["via_username"] = stock
        else:
            stock_data[type_key] = stock

        save_stock_info(stock_data)

        # 2. Try to update active stock embed message in channel
        embed_updated = False
        ch_id = stock_data.get("embed_channel_id")
        msg_id = stock_data.get("embed_message_id")
        if ch_id and msg_id:
            try:
                channel = bot.get_channel(ch_id)
                if not channel:
                    channel = await bot.fetch_channel(ch_id)
                if channel and isinstance(channel, discord.TextChannel):
                    msg = await channel.fetch_message(msg_id)
                    if msg:
                        new_embed = build_stock_embed(interaction.guild.name if interaction.guild else None)
                        await msg.edit(embed=new_embed, view=StockNotifView())
                        embed_updated = True
            except Exception as e:
                log_debug("adjust_stock.edit_embed_error", error=str(e))

        # 3. Target users from notif_stock_user.json (hanya kirim DM jika stok > 0)
        target_uids = set()
        success_count = 0
        failed_count = 0

        if stock > 0:
            users_data = load_notif_stock_users()
            if type_key == "all":
                for k in ["gamepass", "group_payout", "via_username"]:
                    target_uids.update(users_data.get(k, []))
            else:
                target_uids.update(users_data.get(type_key, []))

        # 4. Dispatch DM notifications
        if target_uids:
            gp_formatted = format_robux_amount(stock_data["gamepass"])
            grp_formatted = format_robux_amount(stock_data["group_payout"])
            usr_formatted = format_robux_amount(stock_data["via_username"])

            dm_embed = discord.Embed(
                title="🔔 Notifikasi Stok Robux!",
                description=(
                    f"Halo! Stok Robux untuk **{type_label}** telah diupdate.\n\n"
                    f"📦 **Stok Saat Ini:**\n"
                    f"• 🛍️ Robux Gamepass: **{gp_formatted} R$**\n"
                    f"• 👥 Group Payout: **{grp_formatted} R$**\n"
                    f"• 🆔 Via Username: **{usr_formatted} R$**\n"
                    + (f"\n💬 **Pesan Admin:**\n> {pesan}\n" if pesan else "")
                    + f"\n🗓️ *Update: <t:{now_ts}:R>*"
                ),
                color=0x00D1D1,
            )
            dm_embed.set_footer(text="Medusablox Stock Reminder • Notifikasi DM otomatis")

            for uid in target_uids:
                try:
                    user = bot.get_user(uid)
                    if not user:
                        user = await bot.fetch_user(uid)
                    if user:
                        await user.send(embed=dm_embed)
                        success_count += 1
                    else:
                        failed_count += 1
                except (discord.Forbidden, discord.HTTPException):
                    failed_count += 1
                except Exception as e:
                    log_debug("adjust_stock.dm_error", uid=uid, error=str(e))
                    failed_count += 1
                await asyncio.sleep(0.1)

        # 5. Send report back to admin
        result_embed = discord.Embed(
            title="✅ Stok Berhasil Diupdate!",
            color=0x2ECC71,
        )
        result_embed.add_field(name="Tipe Notif", value=type_label, inline=True)
        result_embed.add_field(name="Stok Baru", value=f"**{format_robux_amount(stock)} R$**", inline=True)
        result_embed.add_field(
            name="Embed Live",
            value="✅ Ter-update" if embed_updated else "⚠️ Belum ada / tidak ditemukan",
            inline=True,
        )

        if stock == 0:
            notif_report_value = "⏭️ Dilewati (Stok set ke 0 R$)"
        else:
            notif_report_value = (
                f"Total target: **{len(target_uids)}** user\n"
                f"✅ Sukses terkirim: **{success_count}**\n"
                f"❌ Gagal (DM ditutup/error): **{failed_count}**"
            )

        result_embed.add_field(
            name="Pengiriman DM Notifikasi",
            value=notif_report_value,
            inline=False,
        )
        await interaction.followup.send(embed=result_embed, ephemeral=True)
