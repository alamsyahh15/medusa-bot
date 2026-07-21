from datetime import datetime, timedelta, timezone

import discord
from discord.ext import commands

from .config import CALC_MIN_ROBUX, ROBLOX_API_KEY, get_guild_config
from .helpers import (
    apply_admin_fee,
    build_calc_result_embed,
    build_calc_usage_embed,
    build_roblox_group_share_url,
    ensure_order_command_access,
    extract_ticket_identity,
    fetch_and_render_leaderboard,
    find_member_in_guild,
    format_datetime_gmt7,
    get_configured_roblox_group_ids,
    get_embed_field_value,
    get_group_membership,
    get_message_image_url,
    log_debug,
    lookup_roblox_user,
    make_dynamic_qris,
    parse_calc_value,
    parse_robux_amount_input,
    place_external_order,
    upload_payment_proof,
    format_rupiah,
    generate_qris_image,
)
from .config import MEDUSABLOX_DISCORD_INVITE_URL, MEDUSABLOX_GUILD_ID


def register_prefix_commands(bot):
    @bot.command(name="qris")
    async def qris_prefix(ctx: commands.Context, amount_raw: str = None):
        guild_config = get_guild_config(ctx.guild.id)
        if not guild_config or not guild_config.get("static_qris") or not guild_config.get("merchant_name"):
            await ctx.send(embed=discord.Embed(title="⚙️ QRIS belum dikonfigurasi", description="Admin perlu setup dulu dengan `/qrissetup`", color=0xE67E22))
            return
        if amount_raw is None:
            await ctx.send("❌ Format salah. Contoh: `!qris 26000`")
            return
        cleaned = amount_raw.replace(".", "").replace(",", "").replace(" ", "")
        if not cleaned.isdigit():
            await ctx.send("❌ Nominal tidak valid.")
            return
        amount = int(cleaned)
        if amount <= 0:
            await ctx.send("❌ Nominal harus lebih dari 0.")
            return
        if amount > 50_000_000:
            await ctx.send("❌ Melebihi batas maksimum Rp 50.000.000.")
            return

        original_amount = amount
        final_amount = apply_admin_fee(amount)

        async with ctx.typing():
            try:
                payload = make_dynamic_qris(guild_config["static_qris"], final_amount)
                image_bytes = generate_qris_image(
                    payload,
                    final_amount,
                    guild_config["merchant_name"],
                    original_amount=original_amount,
                )
            except Exception as e:
                await ctx.send(f"❌ Gagal generate QR: {e}")
                return

        fee_amount = final_amount - original_amount
        desc = f"Subtotal: **{format_rupiah(original_amount)}**\nBiaya admin (0.5%): **{format_rupiah(fee_amount)}**\nTotal bayar: **{format_rupiah(final_amount)}**"

        file = discord.File(image_bytes, filename=f"qris_{final_amount}.png")
        embed = discord.Embed(title="💳 QRIS Payment", description=desc, color=0x1A1F5E)
        embed.set_image(url=f"attachment://qris_{final_amount}.png")
        embed.set_footer(text=f"E-Wallet transaction cannot be refunded • {guild_config['merchant_name']}")
        await ctx.send(file=file, embed=embed)

    @bot.command(name="calc")
    async def calc_prefix(ctx: commands.Context, value_raw: str = None, _type_raw: str = None):
        if value_raw is None:
            await ctx.send(embed=build_calc_usage_embed())
            return

        parsed = parse_calc_value(value_raw)
        if not parsed:
            await ctx.send("❌ Format tidak valid. Contoh: `!calc 500`, `!calc 15k`, atau `!calc 100rb`")
            return

        calc_mode, amount = parsed
        if amount <= 0:
            await ctx.send("❌ Nilai harus lebih dari 0.")
            return

        if calc_mode == "robux" and amount < CALC_MIN_ROBUX:
            await ctx.send(f"❌ Minimum kalkulasi adalah **{CALC_MIN_ROBUX} Robux**.")
            return
        await ctx.send(embed=build_calc_result_embed(calc_mode, amount))

    @bot.command(name="check")
    async def check_prefix(ctx: commands.Context, username_roblox: str = None):
        log_debug("check.invoked", author=getattr(ctx.author, "id", None), guild=getattr(ctx.guild, "id", None), username=username_roblox)
        if not username_roblox:
            log_debug("check.invalid_usage", reason="missing_username")
            await ctx.send("❌ Format salah. Contoh: `!check username_roblox`")
            return

        group_ids = get_configured_roblox_group_ids()
        if not group_ids or not ROBLOX_API_KEY:
            log_debug("check.invalid_config", group_count=len(group_ids), has_api_key=bool(ROBLOX_API_KEY))
            await ctx.send("❌ `ROBLOX_GROUP_IDS` atau `ROBLOX_API_KEY` belum diset di environment bot.")
            return

        async with ctx.typing():
            try:
                user_data = await lookup_roblox_user(bot.http_session, username_roblox)
                if not user_data:
                    log_debug("check.user_not_found", username=username_roblox)
                    await ctx.send(f"❌ Username Roblox `{username_roblox}` tidak ditemukan atau terkena filter banned user.")
                    return

                now_utc = datetime.now(timezone.utc)
                group_results = []
                for group_id in group_ids:
                    membership = await get_group_membership(bot.http_session, group_id, user_data["id"])
                    if not membership:
                        log_debug("check.membership_not_found", username=user_data["name"], user_id=user_data["id"], group_id=group_id)
                        group_results.append({
                            "group_id": group_id,
                            "membership_found": False,
                        })
                        continue

                    create_time_raw = membership.get("createTime")
                    if not create_time_raw:
                        log_debug("check.create_time_missing", username=user_data["name"], user_id=user_data["id"], group_id=group_id)
                        await ctx.send(f"❌ Data `createTime` membership tidak ditemukan untuk group `{group_id}`.")
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
                log_debug("check.exception", username=username_roblox, error=str(e))
                await ctx.send(f"❌ Gagal cek membership Roblox: {e}")
                return

        embed = discord.Embed(
            title="🔎 Hasil Cek Membership Roblox",
            description="User bisa order instant group jika minimal ada satu group yang sudah diikuti selama 3 hari.",
            color=0x1A1F5E,
        )
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
                group_lines.append(
                    f"**Group {index}** (`{group_id}`)\nSudah join sejak {format_datetime_gmt7(create_time)}.\nStatus: siap dipakai untuk order."
                )
            else:
                group_lines.append(
                    f"**Group {index}** (`{group_id}`)\nSudah join sejak {format_datetime_gmt7(create_time)}.\nBisa dipakai untuk order mulai {format_datetime_gmt7(available_at)}."
                )

        if has_ready_group and group_results:
            log_debug("check.eligible", username=user_data["name"], user_id=user_data["id"], group_count=len(group_results))
            embed.add_field(
                name="Status",
                value="✅ User ini sudah eligible untuk order robux instant group karena minimal ada satu group yang sudah 3 hari.",
                inline=False,
            )
            embed.color = 0x2ECC71
        else:
            log_debug(
                "check.not_eligible",
                username=user_data["name"],
                user_id=user_data["id"],
                group_count=len(group_results),
                earliest_available_at=format_datetime_gmt7(earliest_available_at) if earliest_available_at else "unknown",
            )
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
                view.add_item(
                    discord.ui.Button(
                        label=f"Join Group {index}",
                        url=build_roblox_group_share_url(group_id),
                    )
                )
            log_debug("check.join_buttons_added", username=user_data["name"], missing_groups=",".join(missing_group_ids))

        await ctx.send(embed=embed, view=view)

    @bot.command(name="giveaway")
    async def giveaway_prefix(ctx: commands.Context):
        log_debug("giveaway.invoked", author=getattr(ctx.author, "id", None), guild=getattr(ctx.guild, "id", None), has_reply=bool(getattr(ctx.message, "reference", None)))
        reference = ctx.message.reference
        if not reference or not reference.message_id:
            await ctx.send("❌ Command ini harus dipakai dengan cara reply ke message pendaftaran giveaway.")
            return

        try:
            replied_message = reference.resolved
            if replied_message is None:
                replied_message = await ctx.channel.fetch_message(reference.message_id)
        except Exception:
            log_debug("giveaway.reply_fetch_failed", message_id=getattr(reference, "message_id", None))
            await ctx.send("❌ Gagal mengambil message yang direply.")
            return

        ticket_data = extract_ticket_identity(replied_message)
        discord_user_id = ticket_data["discord_user_id"]
        discord_username = ticket_data["discord_username"]
        roblox_username = ticket_data["roblox_username"]
        log_debug(
            "giveaway.ticket_parsed",
            discord_user_id=discord_user_id,
            discord_username=discord_username,
            roblox_username=roblox_username,
        )

        if not discord_user_id or not roblox_username:
            await ctx.send("❌ Data dari message yang direply belum lengkap. Pastikan ada `User ID` dan `Username Roblox`.")
            return

        group_ids = get_configured_roblox_group_ids()
        if not group_ids or not ROBLOX_API_KEY:
            await ctx.send("❌ `ROBLOX_GROUP_IDS` atau `ROBLOX_API_KEY` belum diset di environment bot.")
            return

        async with ctx.typing():
            discord_member_status, member = await find_member_in_guild(bot, MEDUSABLOX_GUILD_ID, discord_user_id)
            log_debug(
                "giveaway.discord_member_checked",
                discord_user_id=discord_user_id,
                status=discord_member_status,
            )
            try:
                roblox_user = await lookup_roblox_user(bot.http_session, roblox_username)
            except Exception as e:
                log_debug("giveaway.roblox_lookup_exception", roblox_username=roblox_username, error=str(e))
                await ctx.send(f"❌ Gagal cek username Roblox: {e}")
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
            embed.add_field(
                name="Member Discord Medusablox",
                value="⚠️ Belum bisa diverifikasi otomatis saat ini.\nBot tetap berjalan tanpa `Server Members Intent`, jadi status join Discord perlu dicek manual sementara review intent masih berlangsung.",
                inline=False,
            )
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

        is_eligible = discord_ok and all_groups_joined
        if is_eligible:
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
            reason_text = ", ".join(reasons) if reasons else "syarat belum terpenuhi"
            embed.add_field(name="Status Giveaway", value=f"⏳ Belum lolos pengecekan giveaway karena {reason_text}.", inline=False)

        view = None
        if (not discord_ok and not discord_unknown) or missing_group_ids:
            view = discord.ui.View()
            if not discord_ok and not discord_unknown:
                view.add_item(discord.ui.Button(label="Join Discord", url=MEDUSABLOX_DISCORD_INVITE_URL))
            for index, group_id in enumerate(missing_group_ids, start=1):
                view.add_item(discord.ui.Button(label=f"Join Group {index}", url=build_roblox_group_share_url(group_id)))

        await ctx.send(embed=embed, view=view)

    @bot.command(name="order")
    async def order_prefix(ctx: commands.Context, amount_raw: str = None):
        log_debug("order.invoked", author=getattr(ctx.author, "id", None), guild=getattr(ctx.guild, "id", None), amount_raw=amount_raw, has_reply=bool(getattr(ctx.message, "reference", None)))
        if not await ensure_order_command_access(ctx):
            log_debug("order.access_denied", author=getattr(ctx.author, "id", None), guild=getattr(ctx.guild, "id", None))
            return
        if not amount_raw:
            log_debug("order.invalid_usage", reason="missing_amount")
            await ctx.send(f"❌ Format salah. Reply message `!check` yang eligible lalu kirim `!order <robux_amount>` (minimum {CALC_MIN_ROBUX} Robux)")
            return

        amount = parse_robux_amount_input(amount_raw)
        if not amount or amount <= 0:
            log_debug("order.invalid_amount", amount_raw=amount_raw, parsed_amount=amount)
            await ctx.send("❌ Nominal Robux tidak valid. Contoh: `!order 125` atau `!order 1k`")
            return

        if amount < CALC_MIN_ROBUX:
            log_debug("order.below_minimum", amount=amount, minimum=CALC_MIN_ROBUX)
            await ctx.send(f"❌ Minimum order adalah **{CALC_MIN_ROBUX} Robux**.")
            return

        reference = ctx.message.reference
        if not reference or not reference.message_id:
            log_debug("order.invalid_usage", reason="missing_reply")
            await ctx.send("❌ Command ini harus dipakai dengan cara reply ke message hasil `!check` yang eligible.")
            return

        try:
            replied_message = reference.resolved
            if replied_message is None:
                replied_message = await ctx.channel.fetch_message(reference.message_id)
        except Exception:
            log_debug("order.reply_fetch_failed", message_id=getattr(reference, "message_id", None))
            await ctx.send("❌ Gagal mengambil message yang direply.")
            return

        if not replied_message.embeds:
            log_debug("order.invalid_reply", reason="no_embeds", replied_message_id=replied_message.id)
            await ctx.send("❌ Message yang direply bukan hasil `!check`.")
            return

        check_embed = replied_message.embeds[0]
        embed_title = check_embed.title or ""
        username = get_embed_field_value(check_embed, "Username")
        status = get_embed_field_value(check_embed, "Status") or ""
        detail_group = get_embed_field_value(check_embed, "Detail Group") or get_embed_field_value(check_embed, "Cek Group") or ""
        is_check_embed = "Membership Roblox" in embed_title and bool(username) and bool(status) and bool(detail_group)
        if not is_check_embed:
            log_debug("order.invalid_reply", reason="not_check_embed", title=embed_title, has_username=bool(username), has_status=bool(status), has_detail_group=bool(detail_group))
            await ctx.send("❌ Reply harus ke message hasil `!check`.")
            return

        if not username:
            log_debug("order.invalid_reply", reason="missing_username_field")
            await ctx.send("❌ Username Roblox tidak ditemukan di message `!check`.")
            return
        normalized_status = status.lower()
        is_eligible = (
            "available to order robux instant group" in normalized_status
            or "sudah eligible untuk order robux instant group" in normalized_status
        )
        if not is_eligible:
            log_debug("order.not_eligible", username=username, status=status)
            await ctx.send("❌ User ini belum eligible untuk order instant group.")
            return

        async with ctx.typing():
            try:
                order_response = await place_external_order(bot.http_session, username, amount)
            except Exception as e:
                log_debug("order.exception", username=username, amount=amount, error=str(e))
                await ctx.send(f"❌ Gagal membuat order: {e}")
                return

        if not order_response or not order_response.get("success"):
            error_message = "Gagal membuat external order."
            if order_response and order_response.get("message"):
                error_message = order_response["message"]
            log_debug("order.failed", username=username, amount=amount, message=error_message)
            await ctx.send(f"❌ {error_message}")
            return

        order_data = order_response.get("data") or {}
        log_debug("order.succeeded", username=order_data.get("username", username), order_number=order_data.get("order_number"), amount=order_data.get("amount", amount), total_price=order_data.get("total_price"))
        embed = discord.Embed(title="✅ Order berhasil dibuat", description=order_response.get("message", "External order placed successfully."), color=0x2ECC71)
        embed.add_field(name="Order Number", value=order_data.get("order_number", "-"), inline=True)
        embed.add_field(name="Username", value=order_data.get("username", username), inline=True)
        embed.add_field(name="Amount", value=f"{order_data.get('amount', amount)} Robux", inline=True)
        embed.add_field(name="Method", value=order_data.get("method", "-"), inline=True)
        embed.add_field(name="Total Price", value=format_rupiah(int(order_data.get("total_price", 0))), inline=True)
        embed.add_field(name="Status", value=order_data.get("status", "-"), inline=True)

        order_url = (order_data.get("order_url") or "").strip().strip("`").strip()
        if order_url:
            embed.add_field(name="Order URL", value=f"[Klik untuk buka order]({order_url})", inline=False)

        avatar_url = (order_data.get("avatar") or "").strip().strip("`").strip()
        if avatar_url:
            embed.set_thumbnail(url=avatar_url)

        guild_config = get_guild_config(ctx.guild.id) if ctx.guild else None
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
                log_debug("order.qris_generated", order_number=order_data.get("order_number"), subtotal=total_price, qris_total=qris_total)
                await ctx.send(embed=embed, file=qris_file)
                return
            except Exception as e:
                log_debug("order.qris_failed", order_number=order_data.get("order_number"), error=str(e))
                embed.add_field(name="QRIS", value=f"Gagal generate QRIS: `{e}`", inline=False)

        await ctx.send(embed=embed)

    @bot.command(name="payment")
    async def payment_prefix(ctx: commands.Context, order_number: str = None):
        log_debug("payment.invoked", author=getattr(ctx.author, "id", None), guild=getattr(ctx.guild, "id", None), order_number=order_number, has_reply=bool(getattr(ctx.message, "reference", None)))
        if not await ensure_order_command_access(ctx):
            log_debug("payment.access_denied", author=getattr(ctx.author, "id", None), guild=getattr(ctx.guild, "id", None))
            return
        if not order_number:
            log_debug("payment.invalid_usage", reason="missing_order_number")
            await ctx.send("❌ Format salah. Reply message customer yang berisi gambar bukti bayar lalu kirim `!payment <order_number>`")
            return

        reference = ctx.message.reference
        if not reference or not reference.message_id:
            log_debug("payment.invalid_usage", reason="missing_reply")
            await ctx.send("❌ Command ini harus dipakai dengan cara reply ke message customer yang berisi bukti pembayaran.")
            return

        try:
            replied_message = reference.resolved
            if replied_message is None:
                replied_message = await ctx.channel.fetch_message(reference.message_id)
        except Exception:
            log_debug("payment.reply_fetch_failed", message_id=getattr(reference, "message_id", None))
            await ctx.send("❌ Gagal mengambil message yang direply.")
            return

        image_url = get_message_image_url(replied_message)
        if not image_url:
            log_debug("payment.image_not_found", replied_message_id=replied_message.id)
            await ctx.send("❌ Tidak ditemukan gambar pada message yang direply. Pastikan customer mengirim attachment atau embed image.")
            return

        async with ctx.typing():
            try:
                upload_response = await upload_payment_proof(bot.http_session, order_number, image_url)
            except Exception as e:
                log_debug("payment.exception", order_number=order_number, image_url=image_url, error=str(e))
                await ctx.send(f"❌ Gagal upload payment proof: {e}")
                return

        if not upload_response or not upload_response.get("success"):
            error_message = "Gagal upload payment proof."
            if upload_response and upload_response.get("message"):
                error_message = upload_response["message"]
            log_debug("payment.failed", order_number=order_number, image_url=image_url, message=error_message)
            await ctx.send(f"❌ {error_message}")
            return

        upload_data = upload_response.get("data") or {}
        log_debug("payment.succeeded", order_number=upload_data.get("order_number", order_number), status=upload_data.get("status", "done"), image_url=image_url)
        embed = discord.Embed(title="✅ Payment berhasil diupload", description=upload_response.get("message", "Payment uploaded successfully."), color=0x2ECC71)
        embed.add_field(name="Order Number", value=upload_data.get("order_number", order_number), inline=True)
        embed.add_field(name="Status", value=upload_data.get("status", "done"), inline=True)
        embed.add_field(name="Image URL", value=f"[Klik untuk buka bukti bayar]({image_url})", inline=False)
        await ctx.send(embed=embed)

    @bot.command(name="leaderboard", aliases=["lb", "top"])
    async def leaderboard_prefix(ctx: commands.Context):
        async with ctx.typing():
            image_bytes = await fetch_and_render_leaderboard(bot.http_session)
            if not image_bytes:
                await ctx.send("❌ Gagal fetch data leaderboard.")
                return
        await ctx.send(file=discord.File(image_bytes, filename="leaderboard.png"))

    @bot.event
    async def on_command_error(ctx, error):
        if isinstance(error, commands.MissingPermissions):
            await ctx.send("❌ Kamu tidak punya izin untuk perintah ini. (Admin only)")
        elif isinstance(error, commands.CommandNotFound):
            pass
