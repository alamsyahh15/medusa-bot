from datetime import datetime, timezone
from typing import Optional

import discord

from .config import get_all_rating_log_configs, get_rating_log_config
from .helpers import log_debug


class RatingModal(discord.ui.Modal):
    def __init__(self, rating_value: int = 5, original_message: Optional[discord.Message] = None):
        stars_str = "⭐" * rating_value
        super().__init__(title=f"Kirim Ulasan ({stars_str})")
        self.rating_value = rating_value
        self.original_message = original_message

    comment = discord.ui.TextInput(
        label="Comment / Feedback (opsional)",
        placeholder="Tulis review atau saran kamu di sini...",
        required=False,
        style=discord.TextStyle.paragraph,
        max_length=500,
    )

    async def on_submit(self, interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message("❌ Rating hanya bisa dikirim dari dalam server.", ephemeral=True)
            return

        rating_value = self.rating_value
        if rating_value < 1 or rating_value > 5:
            await interaction.response.send_message("❌ Rating tidak valid (harus 1 sampai 5).", ephemeral=True)
            return

        all_targets = get_all_rating_log_configs()
        local_cfg = get_rating_log_config(interaction.guild.id)
        if local_cfg and not any(t["channel_id"] == local_cfg["channel_id"] for t in all_targets):
            all_targets.append({
                "guild_id": interaction.guild.id,
                "channel_id": local_cfg["channel_id"],
            })

        if not all_targets:
            await interaction.response.send_message("❌ Channel log rating belum diset di server manapun. Gunakan `/ratingsetup` dulu.", ephemeral=True)
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
        embed.add_field(name="Customer", value=f"{interaction.user.mention} (`{interaction.user.name}`)", inline=True)
        embed.add_field(name="Server Asal", value=interaction.guild.name, inline=True)
        embed.add_field(name="Dikirim", value=f"<t:{int(now_utc.timestamp())}:R>", inline=True)
        embed.timestamp = now_utc
        embed.set_footer(text="Terima kasih sudah berbelanja! • Verified Customer")
        embed.set_thumbnail(url=interaction.user.display_avatar.url)

        sent_count = 0
        for target in all_targets:
            target_guild = interaction.client.get_guild(target["guild_id"])
            ch_id = target["channel_id"]
            target_ch = None
            if target_guild:
                target_ch = target_guild.get_channel(ch_id)
            if not target_ch:
                target_ch = interaction.client.get_channel(ch_id)

            if isinstance(target_ch, discord.TextChannel):
                perms = target_ch.permissions_for(target_ch.guild.me)
                if perms.view_channel and perms.send_messages and perms.embed_links:
                    try:
                        sent_msg = await target_ch.send(embed=embed)
                        sent_count += 1
                        try:
                            await sent_msg.add_reaction("✅")
                            await sent_msg.add_reaction("❤️")
                        except (discord.HTTPException, discord.Forbidden):
                            pass
                    except Exception as e:
                        log_debug("rating.send_broadcast_error", channel=ch_id, guild=target["guild_id"], error=str(e))
                else:
                    log_debug("rating.broadcast_missing_perms", channel=ch_id, guild=target["guild_id"])

        if sent_count == 0:
            await interaction.response.send_message("❌ Gagal mengirim ulasan ke channel log rating.", ephemeral=True)
            return

        log_debug(
            "rating.submitted",
            guild=interaction.guild.id,
            user=interaction.user.id,
            rating=rating_value,
            has_comment=bool(comment_text),
            sent_count=sent_count,
            total_targets=len(all_targets),
        )

        thank_you_embed = discord.Embed(
            title="⭐ Thank you for your rating!",
            description=(
                f"Terima kasih {interaction.user.mention} sudah memberikan rating!\n\n"
                f"**Rating:** {filled_stars}{empty_stars} • **{rating_value}/5**\n"
                f"{quoted_comment}\n\n"
                f"_Rating dan ulasan kamu telah berhasil dicatat._"
            ),
            color=review_color_map.get(rating_value, 0x00D1D1),
        )
        thank_you_embed.set_author(
            name=str(interaction.user),
            icon_url=interaction.user.display_avatar.url,
        )
        thank_you_embed.set_footer(text="Verified Customer Rating • Thank you!")
        thank_you_embed.timestamp = now_utc

        target_message = self.original_message or interaction.message
        edited_original = False

        try:
            await interaction.response.edit_message(content=None, embed=thank_you_embed, view=None)
            edited_original = True
        except Exception as e:
            log_debug("rating.edit_message_exception", error=str(e))

        if not interaction.response.is_done():
            await interaction.response.send_message("✅ Rating berhasil dikirim. Terima kasih!", ephemeral=True)
        else:
            try:
                await interaction.followup.send("✅ Rating berhasil dikirim. Terima kasih!", ephemeral=True)
            except Exception:
                pass

        if not edited_original and target_message:
            try:
                await target_message.edit(content=None, embed=thank_you_embed, view=None)
            except Exception:
                try:
                    await target_message.delete()
                except Exception:
                    pass


RatingCommentModal = RatingModal


class RatingRequestView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.select(
        placeholder="Pilih rating bintang (1 - 5)...",
        custom_id="rating:select_stars",
        min_values=1,
        max_values=1,
        options=[
            discord.SelectOption(
                label="⭐ 1 Bintang",
                value="1",
                description="1 dari 5 bintang (Sangat Buruk)",
            ),
            discord.SelectOption(
                label="⭐⭐ 2 Bintang",
                value="2",
                description="2 dari 5 bintang (Kurang)",
            ),
            discord.SelectOption(
                label="⭐⭐⭐ 3 Bintang",
                value="3",
                description="3 dari 5 bintang (Cukup)",
            ),
            discord.SelectOption(
                label="⭐⭐⭐⭐ 4 Bintang",
                value="4",
                description="4 dari 5 bintang (Bagus)",
            ),
            discord.SelectOption(
                label="⭐⭐⭐⭐⭐ 5 Bintang",
                value="5",
                description="5 dari 5 bintang (Sangat Puas)",
            ),
        ],
    )
    async def select_stars(self, interaction: discord.Interaction, select: discord.ui.Select):
        if not interaction.guild:
            await interaction.response.send_message("❌ Rating hanya bisa dikirim dari dalam server.", ephemeral=True)
            return

        rating_cfg = get_rating_log_config(interaction.guild.id)
        if not rating_cfg and not get_all_rating_log_configs():
            await interaction.response.send_message("❌ Channel log rating belum diset di server manapun. Gunakan `/ratingsetup` dulu.", ephemeral=True)
            return

        raw_val = select.values[0] if select.values else "5"
        try:
            rating_val = int(raw_val)
        except ValueError:
            rating_val = 5

        log_debug(
            "rating.select_chosen",
            guild=getattr(interaction.guild, "id", None),
            user=getattr(interaction.user, "id", None),
            rating=rating_val,
        )
        modal = RatingModal(rating_value=rating_val, original_message=interaction.message)
        await interaction.response.send_modal(modal)

