import re
from datetime import datetime, timezone
from typing import Optional

import discord

from .config import (
    delete_rating_session,
    get_all_rating_log_configs,
    get_next_rating_order_id,
    get_rating_log_config,
    get_rating_session,
    save_rating_session,
)
from .helpers import format_rupiah, log_debug

RATING_DIVIDER = "──────────────────────────"


def format_rating_nominal(raw_nominal: str) -> str:
    raw = (raw_nominal or "").strip()
    if not raw:
        return "Rp 0"

    cleaned = raw.lower().replace("rp", "").replace("idr", "").strip()
    shorthand_match = re.fullmatch(r"(\d+(?:[.,]\d+)?)\s*(k|rb|ribu|jt|juta|m)", cleaned)
    if shorthand_match:
        num_str = shorthand_match.group(1).replace(",", ".")
        unit = shorthand_match.group(2)
        try:
            val = float(num_str)
            multiplier = 1_000_000 if unit in ("jt", "juta", "m") else 1_000
            return format_rupiah(int(round(val * multiplier)))
        except ValueError:
            pass

    digits_only = re.sub(r"[.\s,]", "", cleaned)
    if digits_only.isdigit():
        return format_rupiah(int(digits_only))

    if raw.lower().startswith("rp"):
        return raw
    return f"Rp {raw}"


def extract_rating_metadata_from_message(message: Optional[discord.Message]) -> dict:
    meta = {
        "order_id": "0001",
        "product": "-",
        "price": "Rp 0",
        "admin_id": None,
        "admin_mention": "-",
        "guild_id": None,
        "guild_name": None,
    }
    if not message:
        return meta

    session = get_rating_session(message.id)
    if isinstance(session, dict):
        meta["order_id"] = session.get("order_id") or meta["order_id"]
        meta["product"] = session.get("product") or meta["product"]
        meta["price"] = session.get("price") or meta["price"]
        meta["admin_id"] = session.get("admin_id")
        meta["guild_id"] = session.get("guild_id")
        meta["guild_name"] = session.get("guild_name")
        if session.get("admin_mention"):
            meta["admin_mention"] = session["admin_mention"]
        elif meta["admin_id"]:
            meta["admin_mention"] = f"<@{meta['admin_id']}>"
        return meta

    if message.embeds:
        embed = message.embeds[0]
        for field in embed.fields:
            fname = (field.name or "").strip().lower()
            fval = (field.value or "").strip()
            if "order id" in fname:
                meta["order_id"] = fval.strip("`")
            elif "produk" in fname:
                meta["product"] = fval
            elif "harga" in fname or "nominal" in fname:
                meta["price"] = fval.replace("💵", "").strip()
            elif "admin" in fname:
                meta["admin_mention"] = fval
            elif "server" in fname:
                meta["guild_name"] = fval

    return meta


RATING_PRODUCTS = [
    "Robux Payout",
    "Robux Gamepass",
    "Robux GIG",
    "Limited Item",
]


class RatingAdminModal(discord.ui.Modal):
    def __init__(
        self,
        product_name: str,
        target_user: Optional[discord.abc.Snowflake] = None,
        setup_interaction: Optional[discord.Interaction] = None,
    ):
        super().__init__(title=f"Harga — {product_name}"[:45])
        self.product_name = product_name
        self.target_user = target_user
        self.setup_interaction = setup_interaction
        self.nominal_input = discord.ui.TextInput(
            label="Nominal / Harga",
            placeholder="Contoh: 2950000, 2.950.000, atau 150k",
            required=True,
            max_length=64,
        )
        self.add_item(self.nominal_input)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        if not interaction.guild:
            await interaction.followup.send("❌ Command ini hanya bisa dijalankan dari dalam server.", ephemeral=True)
            return

        product_name = (self.product_name or "").strip()
        raw_nominal = (self.nominal_input.value or "").strip()

        if not product_name or not raw_nominal:
            await interaction.followup.send("❌ Produk dan Harga wajib diisi.", ephemeral=True)
            return

        formatted_price = format_rating_nominal(raw_nominal)
        order_id = get_next_rating_order_id(interaction.guild.id)
        admin_mention = interaction.user.mention

        embed = discord.Embed(
            title="⭐ Beri Rating & Ulasan",
            description=(
                f"Halo {self.target_user.mention if self.target_user else ''}! "
                "Terima kasih telah berbelanja di **"
                f"{interaction.guild.name}**.\n"
                "Silakan pilih **rating bintang (1 sampai 5)** pada menu dropdown di bawah "
                "dan tuliskan ulasan/testimoni kamu."
            ).replace("Halo ! ", ""),
            color=0x2B2D31,
        )
        embed.add_field(name="🧾 Order ID", value=f"`{order_id}`", inline=True)
        embed.add_field(name="📦 Produk", value=product_name, inline=True)
        embed.add_field(name="💰 Harga", value=f"💵 {formatted_price}", inline=True)
        embed.add_field(name="🛡️ Admin Handler", value=admin_mention, inline=True)
        embed.add_field(name="🌐 Server Asal", value=interaction.guild.name, inline=True)
        embed.set_footer(text="Pilih bintang 1 - 5 pada dropdown di bawah untuk mengirim testimoni.")

        if self.target_user:
            try:
                user_obj = self.target_user
                if not hasattr(user_obj, "send"):
                    user_obj = interaction.client.get_user(self.target_user.id) or await interaction.client.fetch_user(
                        self.target_user.id
                    )
                sent_msg = await user_obj.send(embed=embed, view=RatingRequestView())
                destination_text = f"Direct Message (DM) {user_obj.mention} (`{user_obj.name}`)"
            except discord.Forbidden:
                await interaction.followup.send(
                    f"❌ Gagal mengirim survey rating ke DM {self.target_user.mention}. User mematikan DM dari anggota server.",
                    ephemeral=True,
                )
                return
            except Exception as e:
                log_debug("rating.admin_send_dm_error", error=str(e))
                await interaction.followup.send(
                    f"❌ Gagal mengirim survey rating ke DM: {e}",
                    ephemeral=True,
                )
                return
        else:
            if not interaction.channel:
                await interaction.followup.send("❌ Channel tidak ditemukan.", ephemeral=True)
                return
            try:
                sent_msg = await interaction.channel.send(embed=embed, view=RatingRequestView())
                destination_text = f"Channel {interaction.channel.mention}"
            except Exception as e:
                log_debug("rating.admin_send_panel_error", error=str(e))
                await interaction.followup.send(
                    f"❌ Gagal mengirim panel rating ke channel ini: {e}",
                    ephemeral=True,
                )
                return

        save_rating_session(
            sent_msg.id,
            {
                "order_id": order_id,
                "product": product_name,
                "price": formatted_price,
                "admin_id": interaction.user.id,
                "admin_mention": admin_mention,
                "guild_id": interaction.guild.id,
                "guild_name": interaction.guild.name,
            },
        )

        done_embed = discord.Embed(
            title="✅ Survey Rating Berhasil Dikirim",
            description=(
                f"Panel survey rating telah dikirim untuk diisi oleh customer.\n\n"
                f"• **Tujuan:** {destination_text}\n"
                f"• **Order ID:** `{order_id}`\n"
                f"• **Produk:** `{product_name}`\n"
                f"• **Harga:** `{formatted_price}`"
            ),
            color=0x2ECC71,
        )
        if self.setup_interaction:
            try:
                await self.setup_interaction.edit_original_response(embed=done_embed, view=None)
                return
            except Exception:
                pass

        await interaction.followup.send(embed=done_embed, ephemeral=True)


class RatingAdminProductView(discord.ui.View):
    def __init__(self, target_user: Optional[discord.User] = None):
        super().__init__(timeout=180)
        self.selected_user: Optional[discord.User] = target_user

    @discord.ui.select(
        cls=discord.ui.UserSelect,
        placeholder="📩 Pilih customer untuk kirim ke DM (Opsional)...",
        min_values=0,
        max_values=1,
        row=0,
    )
    async def select_target_user(self, interaction: discord.Interaction, select: discord.ui.UserSelect):
        self.selected_user = select.values[0] if select.values else None
        if self.selected_user:
            msg_desc = (
                f"📩 **Target DM dipilih:** {self.selected_user.mention} (`{self.selected_user.name}`)\n\n"
                "Sekarang pilih **Produk** pada dropdown di bawah untuk mengisi **Nominal / Harga**."
            )
        else:
            msg_desc = (
                "📢 **Target:** Channel ini (Publik)\n\n"
                "Pilih **Produk** pada dropdown di bawah untuk mengisi **Nominal / Harga**."
            )
        updated_embed = discord.Embed(
            title="⚙️ Setup Rating Customer",
            description=msg_desc,
            color=0x2B2D31,
        )
        await interaction.response.edit_message(embed=updated_embed, view=self)

    @discord.ui.select(
        placeholder="📦 Pilih produk...",
        min_values=1,
        max_values=1,
        row=1,
        options=[
            discord.SelectOption(
                label="Robux Payout",
                value="Robux Payout",
                emoji="👥",
            ),
            discord.SelectOption(
                label="Robux Gamepass",
                value="Robux Gamepass",
                emoji="🎫",
            ),
            discord.SelectOption(
                label="Robux GIG",
                value="Robux GIG",
                emoji="🎁",
            ),
            discord.SelectOption(
                label="Limited Item",
                value="Limited Item",
                emoji="👑",
            ),
        ],
    )
    async def select_product(self, interaction: discord.Interaction, select: discord.ui.Select):
        selected_product = select.values[0]
        await interaction.response.send_modal(
            RatingAdminModal(
                product_name=selected_product,
                target_user=self.selected_user,
                setup_interaction=interaction,
            )
        )


class RatingModal(discord.ui.Modal):
    def __init__(self, rating_value: int = 5, original_message: Optional[discord.Message] = None):
        stars_str = "⭐" * rating_value
        super().__init__(title=f"Kirim Ulasan ({stars_str})")
        self.rating_value = rating_value
        self.original_message = original_message

    comment = discord.ui.TextInput(
        label="Komentar / Testimoni",
        placeholder="Tulis review atau testimoni kamu di sini...",
        required=False,
        style=discord.TextStyle.paragraph,
        max_length=500,
    )

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        rating_value = self.rating_value
        if rating_value < 1 or rating_value > 5:
            await interaction.followup.send("❌ Rating tidak valid (harus 1 sampai 5).", ephemeral=True)
            return

        target_message = self.original_message or interaction.message
        meta = extract_rating_metadata_from_message(target_message)
        order_id = meta["order_id"]
        product_name = meta["product"]
        price_display = meta["price"]
        admin_mention = meta["admin_mention"]
        origin_guild_id = meta.get("guild_id") or (interaction.guild.id if interaction.guild else None)
        origin_guild_name = meta.get("guild_name") or (interaction.guild.name if interaction.guild else "MedusaBlox")

        all_targets = get_all_rating_log_configs()
        if origin_guild_id:
            local_cfg = get_rating_log_config(int(origin_guild_id))
            if local_cfg and not any(t["channel_id"] == local_cfg["channel_id"] for t in all_targets):
                all_targets.append({
                    "guild_id": int(origin_guild_id),
                    "channel_id": local_cfg["channel_id"],
                })

        if not all_targets:
            await interaction.followup.send("❌ Channel log rating belum diset di server manapun. Gunakan `/ratingsetup` dulu.", ephemeral=True)
            return

        comment_text = (self.comment.value or "").strip()
        comment_box = comment_text.replace("```", "'''") if comment_text else "Terima kasih!"
        stars_spaced = " ".join(["⭐"] * rating_value)

        now_utc = datetime.now(timezone.utc)
        now_ts = int(now_utc.timestamp())
        avatar_url = str(interaction.user.display_avatar.url)

        v2_components = [
            {
                "type": 17,
                "components": [
                    {
                        "type": 9,
                        "components": [
                            {
                                "type": 10,
                                "content": (
                                    f"## ✨ Testimoni Baru\n"
                                    f"🧾 **Order ID :** {order_id}\n"
                                    f"📦 **Produk :** {product_name}\n"
                                    f"💰 **Harga :** 💵 {price_display}"
                                ),
                            }
                        ],
                        "accessory": {
                            "type": 11,
                            "media": {
                                "url": avatar_url,
                            },
                        },
                    },
                    {
                        "type": 14,
                        "divider": True,
                        "spacing": 1,
                    },
                    {
                        "type": 10,
                        "content": f"🌟 **Rating :** {stars_spaced}",
                    },
                    {
                        "type": 14,
                        "divider": True,
                        "spacing": 1,
                    },
                    {
                        "type": 10,
                        "content": f"💬 **Komentar**\n```\n{comment_box}\n```",
                    },
                    {
                        "type": 14,
                        "divider": True,
                        "spacing": 1,
                    },
                    {
                        "type": 10,
                        "content": (
                            f"🛒 **Buyer :** {interaction.user.mention}\n"
                            f"🛡️ **Admin Handler :** {admin_mention}\n"
                            f"🌐 **Server Asal :** {origin_guild_name}\n"
                            f"🕒 **Dikirim :** <t:{now_ts}:R>\n\n"
                            f"-# Terima kasih sudah berbelanja! • Verified Customer • <t:{now_ts}:f>"
                        ),
                    },
                ],
            }
        ]

        fallback_divider = "▬▬▬▬▬▬▬▬▬▬▬▬▬▬"
        testimoni_desc = (
            f"🧾 **Order ID :** {order_id}\n"
            f"📦 **Produk :** {product_name}\n"
            f"💰 **Harga :** 💵 {price_display}\n"
            f"{fallback_divider}\n"
            f"🌟 **Rating :** {stars_spaced}\n"
            f"{fallback_divider}\n"
            f"💬 **Komentar**\n"
            f"```\n{comment_box}\n```\n"
            f"{fallback_divider}\n"
            f"🛒 **Buyer :** {interaction.user.mention}\n"
            f"🛡️ **Admin Handler :** {admin_mention}\n"
            f"🌐 **Server Asal :** {origin_guild_name}\n"
            f"🕒 **Dikirim :** <t:{now_ts}:R>"
        )

        fallback_embed = discord.Embed(
            title="✨ Testimoni Baru",
            description=testimoni_desc,
            color=0x2B2D31,
            timestamp=now_utc,
        )
        fallback_embed.set_thumbnail(url=avatar_url)
        fallback_embed.set_footer(text="Terima kasih sudah berbelanja! • Verified Customer")

        sent_count = 0
        for target in all_targets:
            ch_id = target["channel_id"]
            target_guild = interaction.client.get_guild(target["guild_id"])
            target_ch = None
            if target_guild:
                target_ch = target_guild.get_channel(ch_id)
            if not target_ch:
                target_ch = interaction.client.get_channel(ch_id)
            if not target_ch:
                try:
                    target_ch = await interaction.client.fetch_channel(ch_id)
                except Exception:
                    target_ch = None

            if isinstance(target_ch, discord.TextChannel):
                guild_me = target_ch.guild.me or target_ch.guild.get_member(interaction.client.user.id)
                if not guild_me:
                    continue
                perms = target_ch.permissions_for(guild_me)
                if perms.view_channel and perms.send_messages and perms.embed_links:
                    try:
                        route = discord.http.Route(
                            "POST",
                            "/channels/{channel_id}/messages",
                            channel_id=target_ch.id,
                        )
                        sent_data = await interaction.client.http.request(
                            route,
                            json={
                                "flags": 32768,
                                "components": v2_components,
                            },
                        )
                        sent_count += 1
                        sent_msg_id = int(sent_data["id"]) if isinstance(sent_data, dict) and sent_data.get("id") else None
                        if sent_msg_id:
                            try:
                                partial_msg = target_ch.get_partial_message(sent_msg_id)
                                await partial_msg.add_reaction("✅")
                                await partial_msg.add_reaction("❤️")
                            except (discord.HTTPException, discord.Forbidden):
                                pass
                    except Exception as v2_err:
                        log_debug("rating.v2_send_fallback", channel=ch_id, error=str(v2_err))
                        try:
                            sent_msg = await target_ch.send(embed=fallback_embed)
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
            await interaction.followup.send("❌ Gagal mengirim ulasan ke channel log rating. Pastikan bot memiliki izin di channel tujuan.", ephemeral=True)
            return

        log_debug(
            "rating.submitted",
            guild=origin_guild_id,
            user=interaction.user.id,
            rating=rating_value,
            order_id=order_id,
            product=product_name,
            price=price_display,
            has_comment=bool(comment_text),
            sent_count=sent_count,
            total_targets=len(all_targets),
        )

        thank_you_embed = discord.Embed(
            title="✨ Testimoni Terkirim!",
            description=testimoni_desc,
            color=0x2ECC71,
            timestamp=now_utc,
        )
        thank_you_embed.set_thumbnail(url=avatar_url)
        thank_you_embed.set_footer(text="Terima kasih sudah berbelanja! • Verified Customer")

        if target_message:
            delete_rating_session(target_message.id)
            try:
                patch_route = discord.http.Route(
                    "PATCH",
                    "/channels/{channel_id}/messages/{message_id}",
                    channel_id=target_message.channel.id,
                    message_id=target_message.id,
                )
                await interaction.client.http.request(
                    patch_route,
                    json={
                        "flags": 32768,
                        "content": "",
                        "embeds": [],
                        "components": v2_components,
                    },
                )
            except Exception:
                try:
                    await target_message.edit(content=None, embed=thank_you_embed, view=None)
                except Exception as e:
                    log_debug("rating.edit_message_exception", error=str(e))
                    if interaction.guild:
                        try:
                            await target_message.delete()
                        except Exception:
                            pass

        await interaction.followup.send("✅ Rating dan testimoni berhasil dikirim. Terima kasih!", ephemeral=True)

    async def on_error(self, interaction: discord.Interaction, error: Exception):
        log_debug("rating.modal_error", error=str(error), user=getattr(interaction.user, "id", None))
        try:
            if not interaction.response.is_done():
                await interaction.response.send_message("❌ Terjadi kesalahan saat memproses rating. Silakan coba lagi.", ephemeral=True)
            else:
                await interaction.followup.send("❌ Terjadi kesalahan saat memproses rating. Silakan coba lagi.", ephemeral=True)
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
        meta = extract_rating_metadata_from_message(interaction.message)
        origin_guild_id = meta.get("guild_id") or (interaction.guild.id if interaction.guild else None)
        rating_cfg = get_rating_log_config(int(origin_guild_id)) if origin_guild_id else None
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
            guild=origin_guild_id,
            user=getattr(interaction.user, "id", None),
            rating=rating_val,
        )
        modal = RatingModal(rating_value=rating_val, original_message=interaction.message)
        await interaction.response.send_modal(modal)



