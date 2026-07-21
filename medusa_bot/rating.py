from datetime import datetime, timezone

import discord

from .config import get_rating_log_config
from .helpers import log_debug


class RatingModal(discord.ui.Modal, title="Kirim Rating"):
    rating = discord.ui.TextInput(
        label="Rating (1-5)",
        placeholder="Masukkan angka 1 sampai 5",
        required=True,
        min_length=1,
        max_length=1,
    )
    comment = discord.ui.TextInput(
        label="Comment (opsional)",
        placeholder="Tulis review singkat kamu di sini",
        required=False,
        style=discord.TextStyle.paragraph,
        max_length=500,
    )

    async def on_submit(self, interaction: discord.Interaction):
        raw_rating = (self.rating.value or "").strip()
        if not raw_rating.isdigit():
            await interaction.response.send_message("❌ Rating harus berupa angka 1 sampai 5.", ephemeral=True)
            return

        rating_value = int(raw_rating)
        if rating_value < 1 or rating_value > 5:
            await interaction.response.send_message("❌ Rating hanya boleh dari 1 sampai 5.", ephemeral=True)
            return

        if not interaction.guild:
            await interaction.response.send_message("❌ Rating hanya bisa dikirim dari dalam server.", ephemeral=True)
            return

        rating_cfg = get_rating_log_config(interaction.guild.id)
        if not rating_cfg:
            await interaction.response.send_message("❌ Channel log rating belum diset. Gunakan `/ratingsetup` dulu.", ephemeral=True)
            return

        log_channel = interaction.guild.get_channel(rating_cfg["channel_id"]) or interaction.client.get_channel(rating_cfg["channel_id"])
        if not isinstance(log_channel, discord.TextChannel):
            await interaction.response.send_message("❌ Channel log rating tidak ditemukan. Silakan set ulang dengan `/ratingsetup`.", ephemeral=True)
            return

        permissions = log_channel.permissions_for(interaction.guild.me)
        if not permissions.view_channel or not permissions.send_messages or not permissions.embed_links:
            await interaction.response.send_message(
                f"❌ Saya belum punya izin kirim embed ke {log_channel.mention}.",
                ephemeral=True,
            )
            return

        comment_text = (self.comment.value or "").strip()
        filled_stars = "⭐" * rating_value
        empty_stars = "☆" * (5 - rating_value)
        review_color_map = {
            1: 0xE74C3C,
            2: 0xE67E22,
            3: 0xF1C40F,
            4: 0x2ECC71,
            5: 0x00D1D1,
        }
        quoted_comment = f"> {comment_text}" if comment_text else "> _Tanpa comment_"
        now_utc = datetime.now(timezone.utc)
        embed = discord.Embed(
            description=(
                f"## {interaction.user.display_name} memberi review\n\n"
                f"**{filled_stars}**{empty_stars} • **{rating_value}/5**\n\n"
                f"{quoted_comment}"
            ),
            color=review_color_map.get(rating_value, 0x00D1D1),
        )
        embed.set_author(
            name=str(interaction.user),
            icon_url=interaction.user.display_avatar.url,
        )
        embed.add_field(name="Customer", value=interaction.user.mention, inline=True)
        embed.add_field(name="Server", value=interaction.guild.name, inline=True)
        embed.add_field(name="Dikirim", value=f"<t:{int(now_utc.timestamp())}:R>", inline=True)
        embed.timestamp = now_utc
        embed.set_footer(text=f"Terima kasih sudah berbelanja! • Verified Customer")
        embed.set_thumbnail(url=interaction.user.display_avatar.url)

        await log_channel.send(embed=embed)
        log_debug(
            "rating.submitted",
            guild=interaction.guild.id,
            user=interaction.user.id,
            rating=rating_value,
            has_comment=bool(comment_text),
            channel=log_channel.id,
        )
        await interaction.response.send_message("✅ Rating berhasil dikirim. Terima kasih!", ephemeral=True)


class RatingRequestView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Beri Rating",
        style=discord.ButtonStyle.primary,
        custom_id="rating:open_modal",
    )
    async def open_rating_modal(self, interaction: discord.Interaction, _button: discord.ui.Button):
        log_debug("rating.button_clicked", guild=getattr(interaction.guild, "id", None), user=getattr(interaction.user, "id", None))
        await interaction.response.send_modal(RatingModal())

