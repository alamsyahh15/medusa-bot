from datetime import datetime, timedelta, timezone
from typing import Optional

import discord
from discord import app_commands

from .config import (
    CALC_MIN_ROBUX,
    MEDUSABLOX_DISCORD_INVITE_URL,
    MEDUSABLOX_GUILD_ID,
    ROBLOX_API_KEY,
    delete_guild_config,
    delete_leaderboard_config,
    delete_rating_log_config,
    get_guild_config,
    get_leaderboard_config,
    get_order_role_ids,
    get_rating_log_config,
    has_qris_config,
    set_guild_config,
    set_leaderboard_config,
    set_order_role_config,
    set_rating_log_config,
)
from .helpers import (
    apply_admin_fee,
    build_calc_result_embed,
    build_calc_usage_embed,
    build_roblox_group_share_url,
    ensure_order_interaction_access,
    extract_ticket_identity,
    fetch_and_render_leaderboard,
    find_member_in_guild,
    format_datetime_gmt7,
    format_rupiah,
    generate_qris_image,
    get_configured_roblox_group_ids,
    get_group_membership,
    get_message_image_url,
    get_order_roles,
    log_debug,
    lookup_roblox_user,
    make_dynamic_qris,
    parse_calc_value,
    parse_robux_amount_input,
    place_external_order,
    resolve_text_channel,
    sanitize_roblox_username,
    send_interaction_message,
    upload_payment_proof,
    validate_qris,
)
from .rating import RatingRequestView


class PaymentContextModal(discord.ui.Modal, title="Upload Payment"):
    def __init__(self, bot, message: discord.Message):
        super().__init__()
        self.bot = bot
        self.message = message
        self.order_number = discord.ui.TextInput(
            label="Order Number",
            placeholder="Contoh: EXT-ABCDEFGH",
            required=True,
            max_length=64,
        )
        self.add_item(self.order_number)

    async def on_submit(self, interaction: discord.Interaction):
        if not await ensure_order_interaction_access(interaction):
            return

        order_number = (self.order_number.value or "").strip()
        if not order_number:
            await interaction.response.send_message("❌ Order number wajib diisi.", ephemeral=True)
            return

        image_url = get_message_image_url(self.message)
        if not image_url:
            await interaction.response.send_message(
                "❌ Message yang dipilih tidak punya gambar bukti pembayaran.",
                ephemeral=True,
            )
            return

        log_debug(
            "payment.context_submit",
            author=getattr(interaction.user, "id", None),
            guild=getattr(interaction.guild, "id", None),
            order_number=order_number,
            message_id=getattr(self.message, "id", None),
        )
        await interaction.response.defer(thinking=True)
        try:
            upload_response = await upload_payment_proof(self.bot.http_session, order_number, image_url)
        except Exception as e:
            await interaction.followup.send(f"❌ Gagal upload payment proof: {e}")
            return

        if not upload_response or not upload_response.get("success"):
            error_message = upload_response["message"] if upload_response and upload_response.get("message") else "Gagal upload payment proof."
            await interaction.followup.send(f"❌ {error_message}")
            return

        upload_data = upload_response.get("data") or {}
        embed = discord.Embed(
            title="✅ Payment berhasil diupload",
            description=upload_response.get("message", "Payment uploaded successfully."),
            color=0x2ECC71,
        )
        embed.add_field(name="Order Number", value=upload_data.get("order_number", order_number), inline=True)
        embed.add_field(name="Status", value=upload_data.get("status", "done"), inline=True)
        embed.add_field(name="Image URL", value=f"[Klik untuk buka bukti bayar]({image_url})", inline=False)
        await interaction.followup.send(embed=embed)


def register_slash_commands(bot):
    @bot.tree.command(name="qris", description="Generate QRIS dinamis")
    @app_commands.describe(amount="Nominal IDR, contoh 26000")
    async def qris_generate(interaction: discord.Interaction, amount: int):
        guild_config = get_guild_config(interaction.guild.id)
        if not guild_config or not guild_config.get("static_qris") or not guild_config.get("merchant_name"):
            await interaction.response.send_message(
                embed=discord.Embed(
                    title="⚙️ QRIS belum dikonfigurasi",
                    description="Admin perlu setup dulu dengan `/qrissetup`",
                    color=0xE67E22,
                ),
                ephemeral=True,
            )
            return

        if amount <= 0:
            await interaction.response.send_message("❌ Nominal harus lebih dari 0.", ephemeral=True)
            return
        if amount > 50_000_000:
            await interaction.response.send_message("❌ Melebihi batas maksimum Rp 50.000.000.", ephemeral=True)
            return

        original_amount = amount
        final_amount = apply_admin_fee(amount)
        await interaction.response.defer(thinking=True)
        try:
            payload = make_dynamic_qris(guild_config["static_qris"], final_amount)
            image_bytes = generate_qris_image(
                payload,
                final_amount,
                guild_config["merchant_name"],
                original_amount=original_amount,
            )
        except Exception as e:
            await interaction.followup.send(f"❌ Gagal generate QR: {e}")
            return

        fee_amount = final_amount - original_amount
        desc = (
            f"Subtotal: **{format_rupiah(original_amount)}**\n"
            f"Biaya admin (0.5%): **{format_rupiah(fee_amount)}**\n"
            f"Total bayar: **{format_rupiah(final_amount)}**"
        )
        file = discord.File(image_bytes, filename=f"qris_{final_amount}.png")
        embed = discord.Embed(title="💳 QRIS Payment", description=desc, color=0x1A1F5E)
        embed.set_image(url=f"attachment://qris_{final_amount}.png")
        embed.set_footer(text=f"E-Wallet transaction cannot be refunded • {guild_config['merchant_name']}")
        await interaction.followup.send(file=file, embed=embed)

    @bot.tree.command(name="calc", description="Kalkulasi Robux dan IDR")
    @app_commands.describe(value="Contoh: 500, 15k, atau 100rb")
    async def calc_slash(interaction: discord.Interaction, value: Optional[str] = None):
        if not value:
            await interaction.response.send_message(embed=build_calc_usage_embed(), ephemeral=True)
            return

        parsed = parse_calc_value(value)
        if not parsed:
            await interaction.response.send_message(
                "❌ Format tidak valid. Contoh: `/calc value:500`, `/calc value:15k`, atau `/calc value:100rb`",
                ephemeral=True,
            )
            return

        calc_mode, amount = parsed
        if amount <= 0:
            await interaction.response.send_message("❌ Nilai harus lebih dari 0.", ephemeral=True)
            return
        if calc_mode == "robux" and amount < CALC_MIN_ROBUX:
            await interaction.response.send_message(
                f"❌ Minimum kalkulasi adalah **{CALC_MIN_ROBUX} Robux**.",
                ephemeral=True,
            )
            return
        await interaction.response.send_message(embed=build_calc_result_embed(calc_mode, amount))

    @bot.tree.command(name="qrissetup", description="Setup QRIS untuk server ini (Admin only)")
    @app_commands.describe(
        static_payload="Payload QRIS statis dari QR merchant kamu",
        merchant_name="Nama merchant yang tampil di QR",
    )
    @app_commands.default_permissions(administrator=True)
    async def qris_setup(interaction: discord.Interaction, static_payload: str, merchant_name: str):
        await interaction.response.defer(ephemeral=True)
        if not validate_qris(static_payload):
            await interaction.followup.send(embed=discord.Embed(title="❌ Payload tidak valid", description="Pastikan payload dimulai `000201`, mengandung `5802ID`, dan merupakan QRIS statis (`010211`).", color=0xE74C3C), ephemeral=True)
            return
        set_guild_config(interaction.guild.id, static_payload, merchant_name)
        fee_status = "✅ Aktif untuk semua nominal (+0.5%)"
        embed = discord.Embed(title="✅ QRIS berhasil dikonfigurasi!", color=0x2ECC71)
        embed.add_field(name="Merchant", value=merchant_name, inline=False)
        embed.add_field(name="Payload (preview)", value=f"`{static_payload[:50]}...`", inline=False)
        embed.add_field(name="Biaya Admin", value=fee_status, inline=False)
        embed.add_field(name="Test sekarang", value="`/qris amount:10000`", inline=False)
        await interaction.followup.send(embed=embed, ephemeral=True)

    @bot.tree.command(name="qrisinfo", description="Lihat konfigurasi QRIS server ini")
    async def qris_info(interaction: discord.Interaction):
        cfg = get_guild_config(interaction.guild.id)
        if not cfg or not cfg.get("static_qris") or not cfg.get("merchant_name"):
            await interaction.response.send_message(embed=discord.Embed(title="⚙️ Belum ada konfigurasi QRIS", description="Admin gunakan `/qrissetup`.", color=0xE67E22), ephemeral=True)
            return
        fee_status = "✅ Aktif untuk semua nominal (+0.5%)"
        allowed_roles = get_order_roles(interaction.guild)
        allowed_role_text = ", ".join(role.mention for role in allowed_roles) if allowed_roles else "Semua member"
        embed = discord.Embed(title="📋 Konfigurasi QRIS Server", color=0x1A1F5E)
        embed.add_field(name="Merchant", value=cfg["merchant_name"], inline=False)
        embed.add_field(name="Payload (preview)", value=f"`{cfg['static_qris'][:50]}...`", inline=False)
        embed.add_field(name="Biaya Admin", value=fee_status, inline=False)
        embed.add_field(name="Role Order/Payment", value=allowed_role_text, inline=False)
        embed.add_field(name="Status", value="✅ Aktif", inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @bot.tree.command(name="qrisreset", description="Hapus konfigurasi QRIS server ini (Admin only)")
    @app_commands.default_permissions(administrator=True)
    async def qris_reset(interaction: discord.Interaction):
        if not has_qris_config(interaction.guild.id):
            await interaction.response.send_message("⚠️ Server ini belum memiliki konfigurasi QRIS.", ephemeral=True)
            return
        delete_guild_config(interaction.guild.id)
        await interaction.response.send_message(embed=discord.Embed(title="🗑️ Konfigurasi QRIS dihapus", description="Gunakan `/qrissetup` untuk mengatur ulang.", color=0xE74C3C), ephemeral=True)

    @bot.tree.command(name="qrishelp", description="Tampilkan semua perintah QRIS Bot")
    async def qris_help(interaction: discord.Interaction):
        embed = discord.Embed(title="📖 QRIS Bot — Bantuan", color=0x1A1F5E)
        embed.add_field(name="/qris", value="Generate QRIS. Contoh: `/qris amount:26000`", inline=False)
        embed.add_field(name="/calc", value="Kalkulasi Robux/IDR untuk semua metode sekaligus. Contoh: `/calc value:500`, `/calc value:15k`, atau `/calc value:100rb`", inline=False)
        embed.add_field(name="/check", value="Cek apakah user Roblox sudah 3 hari di group. Contoh: `/check username_roblox`", inline=False)
        embed.add_field(name="Apps > Giveaway Check", value="Klik kanan message pendaftaran lalu jalankan context menu ini untuk cek giveaway.", inline=False)
        embed.add_field(name="/order", value="Buat order manual via slash. Contoh: `/order username amount`", inline=False)
        embed.add_field(name="Apps > Upload Payment", value="Klik kanan message bukti bayar lalu isi `order_number` di popup modal.", inline=False)
        embed.add_field(name="/ratingsetup 🔒", value="Set atau hapus channel log review/rating.", inline=False)
        embed.add_field(name="/rating 🔒", value="Kirim pesan rating yang punya tombol untuk buka form review.", inline=False)
        embed.add_field(name="/leaderboard", value="Tampilkan leaderboard Top 3.", inline=False)
        embed.add_field(name="/setrole 🔒", value="Kelola beberapa role untuk akses `/order`, `/payment`, dan `Apps > Upload Payment` dengan action `add`, `remove`, atau `clear`.", inline=False)
        embed.add_field(name="/qrissetup 🔒", value="Setup QRIS server ini. (Admin only)", inline=False)
        embed.add_field(name="/qrisinfo", value="Lihat konfigurasi QRIS.", inline=False)
        embed.add_field(name="/qrisreset 🔒", value="Hapus konfigurasi QRIS. (Admin only)", inline=False)
        embed.add_field(name="/leaderboardset 🔒", value="Set atau remove channel leaderboard. (Admin only)", inline=False)
        embed.add_field(name="/leaderboard-update 🔒", value="Update leaderboard sekarang. (Admin only)", inline=False)
        embed.add_field(name="Privacy Policy", value="[Klik di sini](https://alamsyahh15.github.io/medusa-bot/privacy.html)", inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @bot.tree.command(name="check", description="Cek apakah user Roblox sudah eligible order instant group")
    @app_commands.describe(username_roblox="Username Roblox yang mau dicek")
    async def check_slash(interaction: discord.Interaction, username_roblox: str):
        username_roblox = sanitize_roblox_username(username_roblox) or username_roblox.strip()
        log_debug("check.slash_invoked", author=getattr(interaction.user, "id", None), guild=getattr(interaction.guild, "id", None), username=username_roblox)
        if not username_roblox:
            await interaction.response.send_message("❌ Username Roblox wajib diisi.", ephemeral=True)
            return

        group_ids = get_configured_roblox_group_ids()
        if not group_ids or not ROBLOX_API_KEY:
            await interaction.response.send_message("❌ `ROBLOX_GROUP_IDS` atau `ROBLOX_API_KEY` belum diset di environment bot.", ephemeral=True)
            return

        await interaction.response.defer(thinking=True)
        try:
            user_data = await lookup_roblox_user(bot.http_session, username_roblox)
            if not user_data:
                await interaction.followup.send(f"❌ Username Roblox `{username_roblox}` tidak ditemukan atau terkena filter banned user.")
                return

            now_utc = datetime.now(timezone.utc)
            group_results = []
            for group_id in group_ids:
                membership = await get_group_membership(bot.http_session, group_id, user_data["id"])
                if not membership:
                    group_results.append({"group_id": group_id, "membership_found": False})
                    continue

                create_time_raw = membership.get("createTime")
                if not create_time_raw:
                    await interaction.followup.send(f"❌ Data `createTime` membership tidak ditemukan untuk group `{group_id}`.")
                    return

                create_time = datetime.fromisoformat(create_time_raw.replace("Z", "+00:00"))
                available_at = create_time + timedelta(days=3)
                group_results.append({
                    "group_id": group_id,
                    "membership_found": True,
                    "create_time": create_time,
                    "available_at": available_at,
                    "is_ready": now_utc >= available_at,
                })
        except Exception as e:
            await interaction.followup.send(f"❌ Gagal cek membership Roblox: {e}")
            return

        embed = discord.Embed(title="🔎 Hasil Cek Membership Roblox", description="User bisa order instant group jika minimal ada satu group yang sudah diikuti selama 3 hari.", color=0x1A1F5E)
        embed.add_field(name="Username", value=user_data["name"], inline=True)
        embed.add_field(name="Display Name", value=user_data.get("displayName", "-"), inline=True)
        embed.add_field(name="User ID", value=str(user_data["id"]), inline=True)

        group_lines = []
        has_ready_group = False
        earliest_available_at = None
        missing_group_ids = []
        for index, result in enumerate(group_results, start=1):
            group_id = result["group_id"]
            if not result["membership_found"]:
                missing_group_ids.append(group_id)
                group_lines.append(f"**Group {index}** (`{group_id}`)\nBelum join group ini.")
                continue
            create_time = result["create_time"]
            available_at = result["available_at"]
            if earliest_available_at is None or available_at < earliest_available_at:
                earliest_available_at = available_at
            if result["is_ready"]:
                has_ready_group = True
                group_lines.append(f"**Group {index}** (`{group_id}`)\nSudah join sejak {format_datetime_gmt7(create_time)}.\nStatus: siap dipakai untuk order.")
            else:
                group_lines.append(f"**Group {index}** (`{group_id}`)\nSudah join sejak {format_datetime_gmt7(create_time)}.\nBisa dipakai untuk order mulai {format_datetime_gmt7(available_at)}.")

        if has_ready_group and group_results:
            embed.add_field(name="Status", value="✅ User ini sudah eligible untuk order robux instant group karena minimal ada satu group yang sudah 3 hari.", inline=False)
            embed.color = 0x2ECC71
        else:
            status_value = "⏳ User ini belum eligible untuk order instant group."
            if earliest_available_at:
                status_value += f"\nEstimasi paling cepat bisa order: **{format_datetime_gmt7(earliest_available_at)}**."
            if missing_group_ids:
                status_value += "\nMasih ada group yang belum di-join, tapi cukup salah satu group yang siap 3 hari."
            embed.add_field(name="Status", value=status_value, inline=False)
            embed.color = 0xF1C40F

        embed.add_field(name="Detail Group", value="\n\n".join(group_lines), inline=False)

        view = None
        if missing_group_ids:
            view = discord.ui.View()
            for index, group_id in enumerate(missing_group_ids, start=1):
                view.add_item(discord.ui.Button(label=f"Join Group {index}", url=build_roblox_group_share_url(group_id)))
        await send_interaction_message(interaction, embed=embed, view=view)

    @bot.tree.command(name="leaderboard", description="Tampilkan leaderboard Top 3")
    async def leaderboard_slash(interaction: discord.Interaction):
        await interaction.response.defer(thinking=True)
        image_bytes = await fetch_and_render_leaderboard(bot.http_session)
        if not image_bytes:
            await interaction.followup.send("❌ Gagal fetch data leaderboard.")
            return
        await interaction.followup.send(file=discord.File(image_bytes, filename="leaderboard.png"))

    @bot.tree.command(name="giveaway", description="Cek kelolosan giveaway via input manual")
    @app_commands.describe(
        discord_user_id="User ID Discord peserta",
        roblox_username="Username Roblox peserta",
        discord_username="Username Discord peserta (opsional, untuk tampilan embed)",
    )
    async def giveaway_slash(interaction: discord.Interaction, discord_user_id: str, roblox_username: str, discord_username: Optional[str] = None):
        cleaned_user_id = "".join(char for char in (discord_user_id or "") if char.isdigit())
        roblox_username = sanitize_roblox_username(roblox_username) or roblox_username.strip()
        discord_username = discord_username.strip() if discord_username else None
        if not cleaned_user_id or not roblox_username:
            await interaction.response.send_message("❌ `discord_user_id` dan `roblox_username` wajib diisi.", ephemeral=True)
            return

        group_ids = get_configured_roblox_group_ids()
        if not group_ids or not ROBLOX_API_KEY:
            await interaction.response.send_message("❌ `ROBLOX_GROUP_IDS` atau `ROBLOX_API_KEY` belum diset di environment bot.", ephemeral=True)
            return

        await interaction.response.defer(thinking=True)
        numeric_user_id = int(cleaned_user_id)
        discord_member_status, member = await find_member_in_guild(bot, MEDUSABLOX_GUILD_ID, numeric_user_id)
        try:
            roblox_user = await lookup_roblox_user(bot.http_session, roblox_username)
        except Exception as e:
            await interaction.followup.send(f"❌ Gagal cek username Roblox: {e}")
            return

        group_results = []
        if roblox_user:
            for group_id in group_ids:
                membership = await get_group_membership(bot.http_session, group_id, roblox_user["id"])
                group_results.append({"group_id": group_id, "joined": bool(membership)})

        discord_ok = discord_member_status == "found"
        discord_unknown = discord_member_status == "unknown"
        roblox_ok = roblox_user is not None
        joined_group_ids = [item["group_id"] for item in group_results if item["joined"]]
        missing_group_ids = [item["group_id"] for item in group_results if not item["joined"]]
        all_groups_joined = roblox_ok and len(group_results) > 0 and not missing_group_ids

        embed = discord.Embed(title="🎁 Hasil Cek Giveaway", color=0x1A1F5E)
        embed.add_field(name="Discord User ID", value=str(numeric_user_id), inline=True)
        embed.add_field(name="Discord Username", value=discord_username or "-", inline=True)
        embed.add_field(name="Roblox Username", value=roblox_username, inline=True)

        if discord_ok:
            embed.add_field(name="Member Discord Medusablox", value=f"✅ Sudah join guild target.\nMember: {member.mention}", inline=False)
        elif discord_unknown:
            embed.add_field(name="Member Discord Medusablox", value="⚠️ Belum bisa diverifikasi otomatis saat ini.\nBot tetap berjalan tanpa `Server Members Intent`, jadi status join Discord perlu dicek manual sementara review intent masih berlangsung.", inline=False)
        else:
            embed.add_field(name="Member Discord Medusablox", value=f"❌ Belum ditemukan sebagai member guild `{MEDUSABLOX_GUILD_ID}`.", inline=False)

        if not roblox_ok:
            embed.add_field(name="Status Roblox", value="❌ Username Roblox tidak ditemukan atau terkena filter banned user.", inline=False)
        else:
            joined_text = ", ".join(f"`{group_id}`" for group_id in joined_group_ids) if joined_group_ids else "-"
            roblox_status_lines = [f"✅ Sudah join: {joined_text}"]
            if missing_group_ids:
                missing_text = ", ".join(f"`{group_id}`" for group_id in missing_group_ids)
                roblox_status_lines.append(f"❌ Belum join: {missing_text}")
            embed.add_field(name="Join Group Roblox", value="\n".join(roblox_status_lines), inline=False)

        if discord_ok and all_groups_joined:
            embed.color = 0x2ECC71
            embed.add_field(name="Status Giveaway", value="✅ Lolos pengecekan giveaway. User sudah join Discord Medusablox dan sudah join semua group Roblox yang diwajibkan.", inline=False)
        else:
            embed.color = 0xE67E22
            reasons = []
            if discord_unknown:
                reasons.append("status member Discord belum bisa diverifikasi otomatis")
            elif not discord_ok:
                reasons.append("belum join Discord Medusablox")
            if not roblox_ok:
                reasons.append("username Roblox tidak valid")
            elif missing_group_ids:
                reasons.append("belum join semua group Roblox yang diwajibkan")
            embed.add_field(name="Status Giveaway", value=f"⏳ Belum lolos pengecekan giveaway karena {', '.join(reasons) if reasons else 'syarat belum terpenuhi'}.", inline=False)

        view = None
        if (not discord_ok and not discord_unknown) or missing_group_ids:
            view = discord.ui.View()
            if not discord_ok and not discord_unknown:
                view.add_item(discord.ui.Button(label="Join Discord", url=MEDUSABLOX_DISCORD_INVITE_URL))
            for index, group_id in enumerate(missing_group_ids, start=1):
                view.add_item(discord.ui.Button(label=f"Join Group {index}", url=build_roblox_group_share_url(group_id)))

        await send_interaction_message(interaction, embed=embed, view=view)

    @bot.tree.context_menu(name="Giveaway Check")
    async def giveaway_context_menu(interaction: discord.Interaction, message: discord.Message):
        ticket_data = extract_ticket_identity(message)
        discord_user_id = ticket_data["discord_user_id"]
        discord_username = ticket_data["discord_username"]
        roblox_username = ticket_data["roblox_username"]
        if not discord_user_id or not roblox_username:
            await interaction.response.send_message("❌ Data dari message target belum lengkap. Pastikan ada `User ID` dan `Username Roblox`.", ephemeral=True)
            return

        group_ids = get_configured_roblox_group_ids()
        if not group_ids or not ROBLOX_API_KEY:
            await interaction.response.send_message("❌ `ROBLOX_GROUP_IDS` atau `ROBLOX_API_KEY` belum diset di environment bot.", ephemeral=True)
            return

        await interaction.response.defer(thinking=True)
        discord_member_status, member = await find_member_in_guild(bot, MEDUSABLOX_GUILD_ID, discord_user_id)
        try:
            roblox_user = await lookup_roblox_user(bot.http_session, roblox_username)
        except Exception as e:
            await interaction.followup.send(f"❌ Gagal cek username Roblox: {e}")
            return

        group_results = []
        if roblox_user:
            for group_id in group_ids:
                membership = await get_group_membership(bot.http_session, group_id, roblox_user["id"])
                group_results.append({"group_id": group_id, "joined": bool(membership)})

        discord_ok = discord_member_status == "found"
        discord_unknown = discord_member_status == "unknown"
        roblox_ok = roblox_user is not None
        joined_group_ids = [item["group_id"] for item in group_results if item["joined"]]
        missing_group_ids = [item["group_id"] for item in group_results if not item["joined"]]
        all_groups_joined = roblox_ok and len(group_results) > 0 and not missing_group_ids

        embed = discord.Embed(title="🎁 Hasil Cek Giveaway", color=0x1A1F5E)
        embed.add_field(name="Discord User ID", value=str(discord_user_id), inline=True)
        embed.add_field(name="Discord Username", value=discord_username or "-", inline=True)
        embed.add_field(name="Roblox Username", value=roblox_username, inline=True)

        if discord_ok:
            embed.add_field(name="Member Discord Medusablox", value=f"✅ Sudah join guild target.\nMember: {member.mention}", inline=False)
        elif discord_unknown:
            embed.add_field(name="Member Discord Medusablox", value="⚠️ Belum bisa diverifikasi otomatis saat ini.\nBot tetap berjalan tanpa `Server Members Intent`, jadi status join Discord perlu dicek manual sementara review intent masih berlangsung.", inline=False)
        else:
            embed.add_field(name="Member Discord Medusablox", value=f"❌ Belum ditemukan sebagai member guild `{MEDUSABLOX_GUILD_ID}`.", inline=False)

        if not roblox_ok:
            embed.add_field(name="Status Roblox", value="❌ Username Roblox tidak ditemukan atau terkena filter banned user.", inline=False)
        else:
            joined_text = ", ".join(f"`{group_id}`" for group_id in joined_group_ids) if joined_group_ids else "-"
            roblox_status_lines = [f"✅ Sudah join: {joined_text}"]
            if missing_group_ids:
                missing_text = ", ".join(f"`{group_id}`" for group_id in missing_group_ids)
                roblox_status_lines.append(f"❌ Belum join: {missing_text}")
            embed.add_field(name="Join Group Roblox", value="\n".join(roblox_status_lines), inline=False)

        if discord_ok and all_groups_joined:
            embed.color = 0x2ECC71
            embed.add_field(name="Status Giveaway", value="✅ Lolos pengecekan giveaway. User sudah join Discord Medusablox dan sudah join semua group Roblox yang diwajibkan.", inline=False)
        else:
            embed.color = 0xE67E22
            reasons = []
            if discord_unknown:
                reasons.append("status member Discord belum bisa diverifikasi otomatis")
            elif not discord_ok:
                reasons.append("belum join Discord Medusablox")
            if not roblox_ok:
                reasons.append("username Roblox tidak valid")
            elif missing_group_ids:
                reasons.append("belum join semua group Roblox yang diwajibkan")
            embed.add_field(name="Status Giveaway", value=f"⏳ Belum lolos pengecekan giveaway karena {', '.join(reasons) if reasons else 'syarat belum terpenuhi'}.", inline=False)

        view = None
        if (not discord_ok and not discord_unknown) or missing_group_ids:
            view = discord.ui.View()
            if not discord_ok and not discord_unknown:
                view.add_item(discord.ui.Button(label="Join Discord", url=MEDUSABLOX_DISCORD_INVITE_URL))
            for index, group_id in enumerate(missing_group_ids, start=1):
                view.add_item(discord.ui.Button(label=f"Join Group {index}", url=build_roblox_group_share_url(group_id)))

        await send_interaction_message(interaction, embed=embed, view=view)

    @bot.tree.command(name="order", description="Buat external order Roblox secara manual")
    @app_commands.describe(
        username="Username Roblox yang mau dibuatkan order",
        amount="Jumlah Robux, contoh: 125 atau 1k",
    )
    async def order_slash(interaction: discord.Interaction, username: str, amount: str):
        if not await ensure_order_interaction_access(interaction):
            return
        username = sanitize_roblox_username(username) or username.strip()
        if not username:
            await interaction.response.send_message("❌ Username Roblox wajib diisi.", ephemeral=True)
            return
        parsed_amount = parse_robux_amount_input(amount)
        if not parsed_amount or parsed_amount <= 0:
            await interaction.response.send_message("❌ Nominal Robux tidak valid. Contoh: `125` atau `1k`.", ephemeral=True)
            return
        if parsed_amount < CALC_MIN_ROBUX:
            await interaction.response.send_message(f"❌ Minimum order adalah **{CALC_MIN_ROBUX} Robux**.", ephemeral=True)
            return

        await interaction.response.defer(thinking=True)
        try:
            order_response = await place_external_order(bot.http_session, username, parsed_amount)
        except Exception as e:
            await interaction.followup.send(f"❌ Gagal membuat order: {e}")
            return

        if not order_response or not order_response.get("success"):
            error_message = order_response["message"] if order_response and order_response.get("message") else "Gagal membuat external order."
            await interaction.followup.send(f"❌ {error_message}")
            return

        order_data = order_response.get("data") or {}
        embed = discord.Embed(title="✅ Order berhasil dibuat", description=order_response.get("message", "External order placed successfully."), color=0x2ECC71)
        embed.add_field(name="Order Number", value=order_data.get("order_number", "-"), inline=True)
        embed.add_field(name="Username", value=order_data.get("username", username), inline=True)
        embed.add_field(name="Amount", value=f"{order_data.get('amount', parsed_amount)} Robux", inline=True)
        embed.add_field(name="Method", value=order_data.get("method", "-"), inline=True)
        embed.add_field(name="Total Price", value=format_rupiah(int(order_data.get("total_price", 0))), inline=True)
        embed.add_field(name="Status", value=order_data.get("status", "-"), inline=True)

        order_url = (order_data.get("order_url") or "").strip().strip("`").strip()
        if order_url:
            embed.add_field(name="Order URL", value=f"[Klik untuk buka order]({order_url})", inline=False)

        avatar_url = (order_data.get("avatar") or "").strip().strip("`").strip()
        if avatar_url:
            embed.set_thumbnail(url=avatar_url)

        guild_config = get_guild_config(interaction.guild.id) if interaction.guild else None
        total_price = int(order_data.get("total_price", 0) or 0)
        has_order_qris = bool(guild_config and guild_config.get("static_qris") and guild_config.get("merchant_name"))
        if has_order_qris and total_price > 0:
            try:
                qris_total = apply_admin_fee(total_price)
                qris_payload = make_dynamic_qris(guild_config["static_qris"], qris_total)
                qris_image = generate_qris_image(qris_payload, qris_total, guild_config["merchant_name"], original_amount=total_price)
                qris_file = discord.File(qris_image, filename="order_qris.png")
                embed.set_image(url="attachment://order_qris.png")
                embed.add_field(name="QRIS Total", value=format_rupiah(qris_total), inline=True)
                await interaction.followup.send(embed=embed, file=qris_file)
                return
            except Exception as e:
                embed.add_field(name="QRIS", value=f"Gagal generate QRIS: `{e}`", inline=False)
        await interaction.followup.send(embed=embed)

    @bot.tree.command(name="payment", description="Upload bukti bayar order via attachment gambar")
    @app_commands.describe(order_number="Nomor order yang ingin ditandai paid", image="Attachment screenshot bukti pembayaran")
    async def payment_slash(interaction: discord.Interaction, order_number: str, image: discord.Attachment):
        if not await ensure_order_interaction_access(interaction):
            return
        order_number = (order_number or "").strip()
        if not order_number:
            await interaction.response.send_message("❌ Order number wajib diisi.", ephemeral=True)
            return
        content_type = image.content_type or ""
        lower_name = (image.filename or "").lower()
        if not content_type.startswith("image/") and not lower_name.endswith((".png", ".jpg", ".jpeg", ".webp", ".gif")):
            await interaction.response.send_message("❌ Attachment harus berupa gambar bukti pembayaran.", ephemeral=True)
            return
        image_url = image.url
        await interaction.response.defer(thinking=True)
        try:
            upload_response = await upload_payment_proof(bot.http_session, order_number, image_url)
        except Exception as e:
            await interaction.followup.send(f"❌ Gagal upload payment proof: {e}")
            return

        if not upload_response or not upload_response.get("success"):
            error_message = upload_response["message"] if upload_response and upload_response.get("message") else "Gagal upload payment proof."
            await interaction.followup.send(f"❌ {error_message}")
            return

        upload_data = upload_response.get("data") or {}
        embed = discord.Embed(title="✅ Payment berhasil diupload", description=upload_response.get("message", "Payment uploaded successfully."), color=0x2ECC71)
        embed.add_field(name="Order Number", value=upload_data.get("order_number", order_number), inline=True)
        embed.add_field(name="Status", value=upload_data.get("status", "done"), inline=True)
        embed.add_field(name="Image URL", value=f"[Klik untuk buka bukti bayar]({image_url})", inline=False)
        await interaction.followup.send(embed=embed)

    @bot.tree.context_menu(name="Upload Payment")
    async def payment_context_menu(interaction: discord.Interaction, message: discord.Message):
        if not await ensure_order_interaction_access(interaction):
            return
        if not get_message_image_url(message):
            await interaction.response.send_message(
                "❌ Message yang dipilih tidak punya gambar bukti pembayaran.",
                ephemeral=True,
            )
            return
        await interaction.response.send_modal(PaymentContextModal(bot, message))

    @bot.tree.command(name="ratingsetup", description="Set atau hapus channel log rating/review")
    @app_commands.describe(channel="Channel tujuan log rating", action="Pilih set atau remove")
    @app_commands.choices(action=[app_commands.Choice(name="set", value="set"), app_commands.Choice(name="remove", value="remove")])
    @app_commands.default_permissions(administrator=True)
    async def rating_setup(interaction: discord.Interaction, channel: Optional[app_commands.AppCommandChannel] = None, action: Optional[app_commands.Choice[str]] = None):
        action_value = action.value if action else "set"
        if action_value == "remove":
            current_config = get_rating_log_config(interaction.guild.id)
            if not current_config:
                await interaction.response.send_message("⚠️ Channel log rating belum pernah diset.", ephemeral=True)
                return
            delete_rating_log_config(interaction.guild.id)
            old_channel = interaction.guild.get_channel(current_config["channel_id"])
            embed = discord.Embed(title="🗑️ Channel log rating berhasil dihapus", color=0xE67E22)
            embed.add_field(name="Channel Sebelumnya", value=old_channel.mention if old_channel else str(current_config["channel_id"]), inline=False)
            embed.add_field(name="Status", value="Log review/rating dimatikan untuk server ini.", inline=False)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        if channel is None:
            await interaction.response.send_message("❌ Untuk action `set`, pilih text channel tujuan log rating.", ephemeral=True)
            return
        target_channel = resolve_text_channel(channel)
        if target_channel is None:
            await interaction.response.send_message("❌ Channel harus berupa text channel biasa, bukan forum, category, atau jenis channel lain.", ephemeral=True)
            return
        permissions = target_channel.permissions_for(interaction.guild.me)
        if not permissions.view_channel or not permissions.send_messages or not permissions.embed_links:
            await interaction.response.send_message(f"❌ Saya belum punya izin yang cukup di {target_channel.mention}. Pastikan ada izin `View Channel`, `Send Messages`, dan `Embed Links`.", ephemeral=True)
            return
        set_rating_log_config(interaction.guild.id, target_channel.id)
        embed = discord.Embed(title="✅ Channel log rating berhasil diset", color=0x2ECC71)
        embed.add_field(name="Channel", value=target_channel.mention, inline=False)
        embed.add_field(name="Kegunaan", value="Semua form rating yang dikirim user akan masuk ke channel ini.", inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @bot.tree.command(name="rating", description="Kirim pesan rating dengan tombol review")
    @app_commands.default_permissions(administrator=True)
    async def rating_command(interaction: discord.Interaction):
        rating_cfg = get_rating_log_config(interaction.guild.id)
        if not rating_cfg:
            await interaction.response.send_message("❌ Channel log rating belum diset. Gunakan `/ratingsetup` dulu.", ephemeral=True)
            return

        embed = discord.Embed(title="⭐ Beri Rating", description="Klik tombol di bawah untuk isi form rating skala **1 sampai 5** dan comment opsional.", color=0x1A1F5E)
        embed.add_field(name="Yang Diisi", value="`Rating 1-5`\n`Comment` opsional", inline=False)
        embed.add_field(name="Tujuan", value="Hasil review akan otomatis dikirim ke channel log rating server ini.", inline=False)
        embed.set_footer(text="Gunakan tombol di bawah untuk membuka form rating.")
        await interaction.response.send_message(embed=embed, view=RatingRequestView())

    @bot.tree.command(name="setrole", description="Atur role yang boleh memakai /order dan upload payment")
    @app_commands.describe(action="Pilih add, remove, atau clear", role="Role target untuk add/remove")
    @app_commands.choices(action=[app_commands.Choice(name="add", value="add"), app_commands.Choice(name="remove", value="remove"), app_commands.Choice(name="clear", value="clear")])
    @app_commands.default_permissions(administrator=True)
    async def set_role(interaction: discord.Interaction, action: app_commands.Choice[str], role: Optional[discord.Role] = None):
        current_role_ids = get_order_role_ids(interaction.guild.id)
        if action.value in ("add", "remove") and role is None:
            await interaction.response.send_message("❌ Role wajib dipilih untuk action `add` atau `remove`.", ephemeral=True)
            return
        if action.value == "clear":
            set_order_role_config(interaction.guild.id, [])
            active_roles_text = "Semua member"
            note_text = "Semua pembatasan role dihapus. Admin tetap selalu bisa memakai command."
        elif action.value == "add":
            if role.id not in current_role_ids:
                current_role_ids.append(role.id)
            set_order_role_config(interaction.guild.id, current_role_ids)
            updated_roles = get_order_roles(interaction.guild)
            active_roles_text = ", ".join(item.mention for item in updated_roles)
            note_text = f"Role {role.mention} ditambahkan ke daftar akses `/order`, `/payment`, dan `Apps > Upload Payment`."
        else:
            updated_role_ids = [role_id for role_id in current_role_ids if role_id != role.id]
            set_order_role_config(interaction.guild.id, updated_role_ids)
            updated_roles = get_order_roles(interaction.guild)
            active_roles_text = ", ".join(item.mention for item in updated_roles) if updated_roles else "Semua member"
            if role.id in current_role_ids:
                note_text = f"Role {role.mention} dihapus dari daftar akses."
            else:
                note_text = f"Role {role.mention} sebelumnya memang belum ada di daftar akses."
        embed = discord.Embed(title="✅ Role order/payment berhasil diupdate", color=0x2ECC71)
        embed.add_field(name="Action", value=action.value, inline=True)
        embed.add_field(name="Role Aktif", value=active_roles_text, inline=False)
        embed.add_field(name="Berlaku untuk", value="`/order`, `/payment`, dan `Apps > Upload Payment`", inline=False)
        embed.add_field(name="Catatan", value=note_text, inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @bot.tree.command(name="leaderboardset", description="Set atau remove channel auto-update leaderboard (Admin only)")
    @app_commands.describe(channel="Channel tujuan leaderboard", action="Pilih set atau remove")
    @app_commands.choices(action=[app_commands.Choice(name="set", value="set"), app_commands.Choice(name="remove", value="remove")])
    @app_commands.default_permissions(administrator=True)
    async def leaderboard_set(interaction: discord.Interaction, channel: Optional[app_commands.AppCommandChannel] = None, action: Optional[app_commands.Choice[str]] = None):
        action_value = action.value if action else "set"
        if action_value == "remove":
            current_config = get_leaderboard_config(interaction.guild.id)
            if not current_config:
                await interaction.response.send_message("⚠️ Channel leaderboard belum pernah diset.", ephemeral=True)
                return
            delete_leaderboard_config(interaction.guild.id)
            old_channel = interaction.guild.get_channel(current_config["channel_id"])
            embed = discord.Embed(title="🗑️ Leaderboard channel berhasil dihapus", color=0xE67E22)
            embed.add_field(name="Channel Sebelumnya", value=old_channel.mention if old_channel else str(current_config["channel_id"]), inline=False)
            embed.add_field(name="Status", value="Auto-update leaderboard dimatikan untuk server ini.", inline=False)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        if channel is None:
            await interaction.response.send_message("❌ Untuk action `set`, pilih text channel tujuan leaderboard.", ephemeral=True)
            return
        target_channel = resolve_text_channel(channel)
        if target_channel is None:
            await interaction.response.send_message("❌ Channel harus berupa text channel biasa, bukan forum, category, atau jenis channel lain.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        permissions = target_channel.permissions_for(interaction.guild.me)
        if not permissions.view_channel or not permissions.send_messages or not permissions.attach_files:
            await interaction.followup.send(f"❌ Saya belum punya izin yang cukup di {target_channel.mention}. Pastikan ada izin `View Channel`, `Send Messages`, dan `Attach Files`.", ephemeral=True)
            return

        set_leaderboard_config(interaction.guild.id, target_channel.id, None)
        image_bytes = await fetch_and_render_leaderboard(bot.http_session)
        if not image_bytes:
            await interaction.followup.send("❌ Gagal fetch data. Channel disimpan, akan dicoba lagi 5 menit.", ephemeral=True)
            return
        msg = await target_channel.send(file=discord.File(image_bytes, filename="leaderboard.png"))
        set_leaderboard_config(interaction.guild.id, target_channel.id, msg.id)
        embed = discord.Embed(title="✅ Leaderboard channel berhasil diset!", color=0x2ECC71)
        embed.add_field(name="Channel", value=target_channel.mention, inline=False)
        embed.add_field(name="Auto-update", value="Setiap 5 menit", inline=False)
        embed.add_field(name="Manual update", value="`/leaderboard-update`", inline=False)
        await interaction.followup.send(embed=embed, ephemeral=True)

    @bot.tree.command(name="leaderboard-update", description="Update leaderboard sekarang (Admin only)")
    @app_commands.default_permissions(administrator=True)
    async def leaderboard_update(interaction: discord.Interaction):
        from .helpers import post_or_edit_leaderboard

        await interaction.response.defer(ephemeral=True)
        lb_cfg = get_leaderboard_config(interaction.guild.id)
        if not lb_cfg:
            await interaction.followup.send("❌ Channel leaderboard belum diset. Gunakan `/leaderboardset` dulu.", ephemeral=True)
            return
        await post_or_edit_leaderboard(bot, interaction.guild.id, get_leaderboard_config, set_leaderboard_config)
        channel = bot.get_channel(lb_cfg["channel_id"])
        embed = discord.Embed(title="✅ Leaderboard berhasil diupdate!", color=0x2ECC71)
        embed.add_field(name="Channel", value=channel.mention if channel else str(lb_cfg["channel_id"]), inline=False)
        await interaction.followup.send(embed=embed, ephemeral=True)
