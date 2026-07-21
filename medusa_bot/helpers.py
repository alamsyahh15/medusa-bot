import asyncio
import io
import math
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

import aiohttp
import discord
import qrcode
from discord import app_commands
from discord.ext import commands
from PIL import Image, ImageDraw, ImageFont

from .config import (
    ADMIN_FEE_RATE,
    CALC_MIN_ROBUX,
    CALC_RATES,
    CALC_TYPE_ICONS,
    CALC_TYPE_LABELS,
    CALC_TYPE_ORDER,
    ENABLE_MEMBERS_INTENT,
    HTTP_TIMEOUT_SECONDS,
    LEADERBOARD_API,
    ROBLOX_API_KEY,
    ROBLOX_EXTERNAL_ORDER_API,
    ROBLOX_EXTERNAL_UPLOAD_PAYMENT_API,
    ROBLOX_GROUP_IDS,
    ROBLOX_USER_LOOKUP_API,
    get_order_role_ids,
)


def crc16(data: str) -> str:
    crc = 0xFFFF
    for char in data:
        crc ^= ord(char) << 8
        for _ in range(8):
            crc = (crc << 1) ^ 0x1021 if crc & 0x8000 else crc << 1
            crc &= 0xFFFF
    return format(crc, "04X")


def validate_qris(payload: str) -> bool:
    return (
        payload.startswith("000201")
        and "5802ID" in payload
        and len(payload) > 50
        and ("010211" in payload or "010212" in payload)
    )


def apply_admin_fee(amount: int) -> int:
    fee_amount = math.ceil(amount * ADMIN_FEE_RATE)
    return amount + fee_amount


def make_dynamic_qris(static: str, amount: int) -> str:
    payload = static.replace("010211", "010212")[:-4]
    amount_str = str(amount)
    amount_field = f"54{len(amount_str):02d}{amount_str}"
    insert_at = payload.find("5802ID")
    if insert_at == -1:
        raise ValueError("Format payload QRIS tidak dikenali")
    payload = payload[:insert_at] + amount_field + payload[insert_at:]
    return payload + crc16(payload)


def format_rupiah(amount: int) -> str:
    return "Rp {:,.0f}".format(amount).replace(",", ".")


def get_configured_roblox_group_ids():
    group_ids = []
    for group_id in ROBLOX_GROUP_IDS.split(","):
        cleaned = group_id.strip()
        if cleaned and cleaned not in group_ids:
            group_ids.append(cleaned)
    return group_ids


def build_roblox_group_share_url(group_id: str) -> str:
    return f"https://www.roblox.com/share/g/{group_id}"


def get_order_roles(guild: Optional[discord.Guild]):
    if not guild:
        return []
    roles = []
    for role_id in get_order_role_ids(guild.id):
        role = guild.get_role(role_id)
        if role:
            roles.append(role)
    return roles


def can_use_order_commands(member, guild: Optional[discord.Guild]) -> bool:
    if not guild or not isinstance(member, discord.Member):
        return False
    if member.guild_permissions.administrator:
        return True
    allowed_roles = get_order_roles(guild)
    if not allowed_roles:
        return True
    return any(role in member.roles for role in allowed_roles)


async def ensure_order_command_access(ctx: commands.Context) -> bool:
    if not ctx.guild or not isinstance(ctx.author, discord.Member):
        await ctx.send("❌ Command ini hanya bisa dipakai di server.")
        return False
    allowed_roles = get_order_roles(ctx.guild)
    if can_use_order_commands(ctx.author, ctx.guild):
        return True
    if allowed_roles:
        role_mentions = ", ".join(role.mention for role in allowed_roles)
        await ctx.send(f"❌ Command ini hanya bisa dipakai role {role_mentions} atau admin.")
    else:
        await ctx.send("❌ Command ini tidak bisa dipakai saat ini.")
    return False


async def send_interaction_message(
    interaction: discord.Interaction,
    content: str = None,
    embed: discord.Embed = None,
    view: discord.ui.View = None,
    file: discord.File = None,
    ephemeral: bool = False,
):
    payload = {"ephemeral": ephemeral}
    if content is not None:
        payload["content"] = content
    if embed is not None:
        payload["embed"] = embed
    if view is not None:
        payload["view"] = view
    if file is not None:
        payload["file"] = file

    if interaction.response.is_done():
        await interaction.followup.send(**payload)
        return
    await interaction.response.send_message(**payload)


async def ensure_order_interaction_access(interaction: discord.Interaction) -> bool:
    member = interaction.user
    guild = interaction.guild
    if not guild or not isinstance(member, discord.Member):
        await send_interaction_message(interaction, "❌ Command ini hanya bisa dipakai di server.", ephemeral=True)
        return False
    allowed_roles = get_order_roles(guild)
    if can_use_order_commands(member, guild):
        return True
    if allowed_roles:
        role_mentions = ", ".join(role.mention for role in allowed_roles)
        await send_interaction_message(
            interaction,
            f"❌ Command ini hanya bisa dipakai role {role_mentions} atau admin.",
            ephemeral=True,
        )
    else:
        await send_interaction_message(interaction, "❌ Command ini tidak bisa dipakai saat ini.", ephemeral=True)
    return False


def log_debug(event: str, **kwargs):
    parts = []
    for key, value in kwargs.items():
        if isinstance(value, str):
            value = value.replace("\n", " ").strip()
            if len(value) > 160:
                value = value[:157] + "..."
        parts.append(f"{key}={value}")
    suffix = f" | {' | '.join(parts)}" if parts else ""
    print(f"[Debug] {event}{suffix}")


def normalize_calc_type(raw_type: str = None) -> Optional[str]:
    if not raw_type:
        return "group"

    aliases = {
        "group": "group",
        "groupfund": "group",
        "groupfunds": "group",
        "gf": "group",
        "gamepass": "gamepass",
        "gp": "gamepass",
        "gig": "gig",
        "send": "send",
    }
    return aliases.get(raw_type.lower().replace(" ", "").replace("-", ""))


def parse_calc_value(raw_value: str):
    cleaned = raw_value.lower().replace(".", "").replace(",", "").replace(" ", "")
    if cleaned.endswith("rb"):
        amount_part = cleaned[:-2]
        if not amount_part.isdigit():
            return None
        amount_idr = int(amount_part) * 1000
        return ("idr", amount_idr)

    if cleaned.endswith("k"):
        robux_part = cleaned[:-1]
        if not robux_part.isdigit():
            return None
        robux_amount = int(robux_part) * 1000
        return ("robux", robux_amount)

    if cleaned.isdigit():
        return ("robux", int(cleaned))

    return None


def build_calc_usage_embed() -> discord.Embed:
    embed = discord.Embed(title="🧮 Kalkulator Harga", color=0x1A1F5E)
    embed.description = (
        "**Robux -> IDR**\n"
        "`!calc <robux>`\n\n"
        "**IDR -> Robux**\n"
        "`!calc <amount>rb`"
    )
    embed.add_field(
        name="Metode yang dihitung",
        value="`Instant Group`, `Gamepass`, `Gig`, `Send`",
        inline=False,
    )
    embed.add_field(
        name="Rate",
        value=(
            "`Instant Group` = Rp 138.000 / 1.000 Robux\n"
            "`Gamepass` = Rp 128.000 / 1.000 Robux\n"
            "`Gig` = Rp 115.000 / 1.000 Robux\n"
            "`Send` = Rp 143.000 / 1.000 Robux"
        ),
        inline=False,
    )
    embed.add_field(
        name="Contoh",
        value="`!calc 500`\n`!calc 15k`\n`!calc 100rb`",
        inline=False,
    )
    embed.set_footer(text=f"Semua metode dihitung sekaligus • Minimum: {CALC_MIN_ROBUX} Robux")
    return embed


def build_calc_result_embed(calc_mode: str, amount: int) -> discord.Embed:
    if calc_mode == "robux":
        embed = discord.Embed(title="🧮 Kalkulator Harga", color=0x2ECC71)
        embed.description = f"**{amount:,} Robux -> IDR**".replace(",", ".")
        result_lines = []
        for calc_type in CALC_TYPE_ORDER:
            rate = CALC_RATES[calc_type]
            total_idr = math.ceil(amount * rate / 1000)
            rate_per_robux = math.ceil(rate / 1000)
            result_lines.append(
                f"{CALC_TYPE_ICONS[calc_type]} **{CALC_TYPE_LABELS[calc_type]}**\n"
                f"> **{format_rupiah(total_idr)}** (`Rp{rate_per_robux}/Robux`)"
            )
        embed.add_field(name="Hasil", value="\n\n".join(result_lines), inline=False)
        embed.set_footer(text="Perhitungan IDR dibulatkan ke atas.")
        return embed

    embed = discord.Embed(title="🧮 Kalkulator Harga", color=0x3498DB)
    embed.description = f"**{format_rupiah(amount)} -> Robux**"
    result_lines = []
    for calc_type in CALC_TYPE_ORDER:
        rate = CALC_RATES[calc_type]
        total_robux = math.floor(amount * 1000 / rate)
        suffix = ""
        if total_robux < CALC_MIN_ROBUX:
            suffix = f" • minimum {CALC_MIN_ROBUX}"
        result_lines.append(
            f"{CALC_TYPE_ICONS[calc_type]} **{CALC_TYPE_LABELS[calc_type]}**\n"
            f"> **{total_robux:,} Robux**{suffix}".replace(",", ".")
        )
    embed.add_field(name="Hasil", value="\n\n".join(result_lines), inline=False)
    embed.set_footer(text="Estimasi Robux dibulatkan ke bawah.")
    return embed


def format_datetime_gmt7(dt_utc: datetime) -> str:
    bulan = [
        "Januari", "Februari", "Maret", "April", "Mei", "Juni",
        "Juli", "Agustus", "September", "Oktober", "November", "Desember",
    ]
    jakarta_tz = timezone(timedelta(hours=7))
    local_dt = dt_utc.astimezone(jakarta_tz)
    return f"{local_dt.day:02d} {bulan[local_dt.month - 1]} {local_dt.year} {local_dt.hour:02d}:{local_dt.minute:02d} GMT+7"


def get_embed_field_value(embed: discord.Embed, field_name: str) -> Optional[str]:
    for field in embed.fields:
        if field.name == field_name:
            return field.value
    return None


def parse_robux_amount_input(raw_value: str) -> Optional[int]:
    cleaned = raw_value.lower().replace(".", "").replace(",", "").replace(" ", "")
    if cleaned.endswith("k"):
        number_part = cleaned[:-1]
        if not number_part.isdigit():
            return None
        return int(number_part) * 1000
    if cleaned.isdigit():
        return int(cleaned)
    return None


def get_message_image_url(message: discord.Message) -> Optional[str]:
    for attachment in message.attachments:
        content_type = attachment.content_type or ""
        lower_name = attachment.filename.lower()
        if content_type.startswith("image/") or lower_name.endswith((".png", ".jpg", ".jpeg", ".webp", ".gif")):
            return attachment.url

    for embed in message.embeds:
        if embed.image and embed.image.url:
            return embed.image.url
        if embed.thumbnail and embed.thumbnail.url:
            return embed.thumbnail.url

    return None


def build_message_search_text(message: discord.Message) -> str:
    parts = []
    if message.content:
        parts.append(message.content)
    for embed in message.embeds:
        if embed.title:
            parts.append(embed.title)
        if embed.description:
            parts.append(embed.description)
        for field in embed.fields:
            parts.append(f"{field.name}\n{field.value}")
    return "\n".join(parts)


def normalize_label_text(value: str) -> str:
    cleaned = (value or "").lower()
    cleaned = cleaned.replace("`", "").replace("*", "").replace("_", "").replace("\u200b", "")
    cleaned = cleaned.replace(":", " ").replace("-", " ")
    return " ".join(cleaned.split())


def sanitize_roblox_username(value: Optional[str]) -> Optional[str]:
    if not value:
        return None

    cleaned = value.strip().strip("`").strip()
    cleaned = cleaned.replace("\u200b", "").replace("\u200e", "").replace("\u200f", "")
    cleaned = cleaned.replace("*", "").replace("_ _", "")
    cleaned = cleaned.replace("\\_", "_").replace("\\*", "*").replace("\\`", "`")
    cleaned = cleaned.replace("\\~", "~").replace("\\|", "|")
    cleaned = cleaned.replace("\\", "")

    import re
    match = re.search(r"[A-Za-z0-9_]{3,20}", cleaned)
    if match:
        return match.group(0)
    return cleaned or None


def extract_labeled_value(text: str, label: str) -> Optional[str]:
    lines = [line.strip() for line in (text or "").splitlines()]
    normalized_label = normalize_label_text(label)

    for index, raw_line in enumerate(lines):
        line = raw_line.strip().strip("`")
        if not line:
            continue

        normalized_line = normalize_label_text(line)
        if normalized_line == normalized_label:
            for next_line in lines[index + 1:]:
                candidate = next_line.strip().strip("`")
                if candidate:
                    return candidate

        if normalized_line.startswith(normalized_label):
            if ":" in line:
                candidate = line.split(":", 1)[1].strip().strip("`")
                if candidate:
                    return candidate
            remainder = normalized_line[len(normalized_label):].strip()
            if remainder:
                return remainder

    import re
    match = re.search(rf"{re.escape(label)}\s*:\s*([^\n\r]+)", text or "", flags=re.IGNORECASE)
    if match:
        candidate = match.group(1).strip().strip("`")
        if candidate:
            return candidate
    return None


def extract_ticket_identity(message: discord.Message) -> dict:
    combined_text = build_message_search_text(message)
    user_id = extract_labeled_value(combined_text, "User ID")
    user_name = extract_labeled_value(combined_text, "User Name")
    roblox_username = extract_labeled_value(combined_text, "Username Roblox")

    for embed in message.embeds:
        for field in embed.fields:
            normalized_field_name = normalize_label_text(field.name)
            field_value = field.value.strip()
            if not user_id and normalized_field_name == normalize_label_text("User ID"):
                user_id = field_value
            if not user_name and normalized_field_name == normalize_label_text("User Name"):
                user_name = field_value
            if not roblox_username and normalized_field_name == normalize_label_text("Username Roblox"):
                roblox_username = field_value

    if not user_id:
        import re
        match = re.search(r"\b\d{17,20}\b", combined_text)
        if match:
            user_id = match.group(0)

    normalized_user_id = None
    if user_id:
        cleaned_user_id = "".join(char for char in user_id if char.isdigit())
        if cleaned_user_id:
            normalized_user_id = int(cleaned_user_id)

    return {
        "discord_user_id": normalized_user_id,
        "discord_username": user_name.strip() if user_name else None,
        "roblox_username": sanitize_roblox_username(roblox_username),
    }


async def find_member_in_guild(bot, guild_id: int, user_id: int):
    guild = bot.get_guild(guild_id)
    if guild is None:
        return "unknown", None

    if ENABLE_MEMBERS_INTENT:
        member = guild.get_member(user_id)
        if member:
            return "found", member

    try:
        member = await guild.fetch_member(user_id)
        return "found", member
    except discord.NotFound:
        return "not_found", None
    except discord.Forbidden:
        return "unknown", None
    except discord.HTTPException:
        return "unknown", None


async def lookup_roblox_user(session: aiohttp.ClientSession, username: str) -> Optional[dict]:
    payload = {
        "usernames": [username],
        "excludeBannedUsers": True,
    }
    log_debug("lookup_roblox_user.request", username=username, url=ROBLOX_USER_LOOKUP_API)
    try:
        async with session.post(ROBLOX_USER_LOOKUP_API, json=payload) as resp:
            log_debug("lookup_roblox_user.response", username=username, status=resp.status)
            if resp.status != 200:
                return None
            data = await resp.json()
    except asyncio.TimeoutError:
        log_debug("lookup_roblox_user.timeout", username=username, timeout=HTTP_TIMEOUT_SECONDS)
        raise RuntimeError(f"Request lookup Roblox timeout setelah {HTTP_TIMEOUT_SECONDS} detik")
    except aiohttp.ClientError as e:
        log_debug("lookup_roblox_user.client_error", username=username, error=str(e))
        raise RuntimeError(f"Gagal request lookup Roblox: {e}")

    users = data.get("data") or []
    if not users:
        return None
    return users[0]


async def get_group_membership(session: aiohttp.ClientSession, group_id: str, user_id: int) -> Optional[dict]:
    headers = {"x-api-key": ROBLOX_API_KEY}
    params = {"filter": f"user == 'users/{user_id}'"}
    url = f"https://apis.roblox.com/cloud/v2/groups/{group_id}/memberships"

    log_debug("get_group_membership.request", group_id=group_id, user_id=user_id, url=url)
    try:
        async with session.get(url, headers=headers, params=params) as resp:
            log_debug("get_group_membership.response", group_id=group_id, user_id=user_id, status=resp.status)
            if resp.status != 200:
                return None
            data = await resp.json()
    except asyncio.TimeoutError:
        log_debug("get_group_membership.timeout", group_id=group_id, user_id=user_id, timeout=HTTP_TIMEOUT_SECONDS)
        raise RuntimeError(f"Request group membership timeout setelah {HTTP_TIMEOUT_SECONDS} detik")
    except aiohttp.ClientError as e:
        log_debug("get_group_membership.client_error", group_id=group_id, user_id=user_id, error=str(e))
        raise RuntimeError(f"Gagal request group membership: {e}")

    memberships = data.get("groupMemberships") or []
    if not memberships:
        return None
    return memberships[0]


async def place_external_order(session: aiohttp.ClientSession, username: str, amount: int) -> Optional[dict]:
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    payload = {
        "username": username,
        "amount": amount,
    }
    log_debug("place_external_order.request", username=username, amount=amount, url=ROBLOX_EXTERNAL_ORDER_API)
    async with session.post(ROBLOX_EXTERNAL_ORDER_API, headers=headers, json=payload) as resp:
        log_debug("place_external_order.response", username=username, amount=amount, status=resp.status)
        try:
            data = await resp.json()
        except Exception:
            text = await resp.text()
            return {
                "success": False,
                "message": f"HTTP {resp.status}: {text[:300]}",
            }

    if resp.status >= 400:
        data.setdefault("success", False)
        data.setdefault("message", f"HTTP {resp.status}")
    return data


async def upload_payment_proof(session: aiohttp.ClientSession, order_number: str, image_url: str) -> Optional[dict]:
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    payload = {
        "order_number": order_number,
        "image_url": image_url,
    }
    log_debug("upload_payment_proof.request", order_number=order_number, url=ROBLOX_EXTERNAL_UPLOAD_PAYMENT_API, image_url=image_url)
    async with session.post(ROBLOX_EXTERNAL_UPLOAD_PAYMENT_API, headers=headers, json=payload) as resp:
        log_debug("upload_payment_proof.response", order_number=order_number, status=resp.status)
        try:
            data = await resp.json()
        except Exception:
            text = await resp.text()
            return {
                "success": False,
                "message": f"HTTP {resp.status}: {text[:300]}",
            }

    if resp.status >= 400:
        data.setdefault("success", False)
        data.setdefault("message", f"HTTP {resp.status}")
    return data


def _load_font(size, bold=False):
    candidates = (
        ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"]
        if bold else
        ["/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]
    )
    for path in candidates:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                pass
    return ImageFont.load_default()


def generate_qris_image(payload: str, amount: int, merchant_name: str, original_amount: int = None) -> io.BytesIO:
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=10, border=2)
    qr.add_data(payload)
    qr.make(fit=True)
    qr_img = qr.make_image(fill_color="black", back_color="white").convert("RGBA")
    qr_size = qr_img.size[0]

    has_fee = original_amount is not None and original_amount != amount
    padding, header_h = 40, 90
    footer_h = 80 if has_fee else 60
    card_w = qr_size + padding * 2
    card_h = qr_size + padding * 2 + header_h + footer_h

    card = Image.new("RGBA", (card_w, card_h), (255, 255, 255, 255))
    draw = ImageDraw.Draw(card)

    def center_text(text, font, y, color=(15, 23, 58)):
        bbox = draw.textbbox((0, 0), text, font=font)
        x = (card_w - (bbox[2] - bbox[0])) // 2
        draw.text((x, y), text, fill=color, font=font)

    center_text(merchant_name, _load_font(22, True), 18)
    center_text(f"Payment of IDR {amount:,}".replace(",", "."), _load_font(20, True), 50, (30, 30, 30))

    qr_x, qr_y = padding, header_h + padding
    draw.rectangle([qr_x - 8, qr_y - 8, qr_x + qr_size + 8, qr_y + qr_size + 8], outline=(200, 200, 200), width=1)
    card.paste(qr_img, (qr_x, qr_y))

    footer_y = qr_y + qr_size + 14
    if has_fee:
        fee_amount = amount - original_amount
        center_text(f"Subtotal: {format_rupiah(original_amount)}  |  Biaya admin (0.5%): {format_rupiah(fee_amount)}", _load_font(12), footer_y, (100, 100, 100))
        center_text("E-Wallet transaction cannot be refunded", _load_font(13), footer_y + 18, (120, 120, 120))
        center_text("Code by MedusaBlox", _load_font(13), footer_y + 36, (120, 120, 120))
    else:
        center_text("E-Wallet transaction cannot be refunded", _load_font(14), footer_y, (120, 120, 120))
        center_text("Code by MedusaBlox", _load_font(14), footer_y + 20, (120, 120, 120))

    buf = io.BytesIO()
    card.convert("RGB").save(buf, format="PNG")
    buf.seek(0)
    return buf


def _rounded_rect(draw, xy, radius, fill, outline=None, width=1):
    x1, y1, x2, y2 = xy
    draw.rectangle([x1 + radius, y1, x2 - radius, y2], fill=fill)
    draw.rectangle([x1, y1 + radius, x2, y2 - radius], fill=fill)
    for cx, cy in [(x1, y1), (x2 - 2 * radius, y1), (x1, y2 - 2 * radius), (x2 - 2 * radius, y2 - 2 * radius)]:
        draw.ellipse([cx, cy, cx + 2 * radius, cy + 2 * radius], fill=fill)
    if outline:
        draw.arc([x1, y1, x1 + 2 * radius, y1 + 2 * radius], 180, 270, fill=outline, width=width)
        draw.arc([x2 - 2 * radius, y1, x2, y1 + 2 * radius], 270, 360, fill=outline, width=width)
        draw.arc([x1, y2 - 2 * radius, x1 + 2 * radius, y2], 90, 180, fill=outline, width=width)
        draw.arc([x2 - 2 * radius, y2 - 2 * radius, x2, y2], 0, 90, fill=outline, width=width)
        draw.line([x1 + radius, y1, x2 - radius, y1], fill=outline, width=width)
        draw.line([x1 + radius, y2, x2 - radius, y2], fill=outline, width=width)
        draw.line([x1, y1 + radius, x1, y2 - radius], fill=outline, width=width)
        draw.line([x2, y1 + radius, x2, y2 - radius], fill=outline, width=width)


def generate_leaderboard_image(players: list) -> io.BytesIO:
    rank_colors = [(212, 175, 55), (180, 180, 195), (180, 120, 60)]
    rank_labels = ["Top 1", "Top 2", "Top 3"]
    width, header, row_h, pad, gap = 480, 110, 90, 20, 10
    height = header + len(players) * (row_h + gap) + pad
    bg, card_bg, card_border = (28, 22, 17), (45, 35, 25), (80, 60, 35)
    white, grey, accent = (255, 255, 255), (160, 150, 140), (160, 100, 220)

    img = Image.new("RGB", (width, height), bg)
    draw = ImageDraw.Draw(img)

    def cx(text, font, y, color):
        bbox = draw.textbbox((0, 0), text, font=font)
        draw.text(((width - (bbox[2] - bbox[0])) // 2, y), text, fill=color, font=font)

    cx("L E A D E R B O A R D", _load_font(13), 18, grey)
    cx("Top 3 Buyer Robux", _load_font(28, True), 40, white)
    cx("Ranking dihitung dari total akumulasi Robux", _load_font(13), 78, grey)
    cx("per username yang telah dibeli.", _load_font(13), 96, grey)

    for i, player in enumerate(players[:3]):
        rc = rank_colors[i]
        cy, cx1, cx2 = header + i * (row_h + gap), pad, width - pad
        _rounded_rect(draw, [cx1, cy, cx2, cy + row_h], 12, card_bg, outline=card_border, width=1)
        draw.rectangle([cx1, cy + 12, cx1 + 4, cy + row_h - 12], fill=rc)
        ax, ay = cx1 + 52, cy + row_h // 2
        draw.ellipse([ax - 24, ay - 24, ax + 24, ay + 24], fill=(60, 50, 40), outline=rc, width=2)
        ini = player["username"][0].upper()
        f20b = _load_font(20, True)
        bb = draw.textbbox((0, 0), ini, font=f20b)
        draw.text((ax - (bb[2] - bb[0]) // 2, ay - (bb[3] - bb[1]) // 2 - 2), ini, fill=rc, font=f20b)
        draw.text((cx1 + 90, cy + 18), player["username"], fill=white, font=_load_font(20, True))
        badge, bx, by = rank_labels[i], cx1 + 90, cy + 48
        bb = draw.textbbox((0, 0), badge, font=_load_font(11))
        _rounded_rect(draw, [bx, by, bx + bb[2] - bb[0] + 14, by + bb[3] - bb[1] + 6], 6, fill=(60, 45, 20))
        draw.text((bx + 7, by + 3), badge, fill=rc, font=_load_font(11))
        val = f"{player['total_robux']:,}".replace(",", ".")
        draw.text((cx2 - 120, cy + 14), "TOTAL", fill=grey, font=_load_font(11))
        f24b = _load_font(24, True)
        bb = draw.textbbox((0, 0), val, font=f24b)
        draw.text((cx2 - 10 - (bb[2] - bb[0]), cy + 28), val, fill=accent, font=f24b)
        draw.text((cx2 - 52, cy + 58), "Robux", fill=grey, font=_load_font(12))

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf


async def fetch_and_render_leaderboard(session) -> Optional[io.BytesIO]:
    try:
        async with session.get(LEADERBOARD_API) as resp:
            if resp.status != 200:
                return None
            data = await resp.json()
        if not data.get("success") or not data.get("data"):
            return None
        return generate_leaderboard_image(data["data"])
    except Exception as e:
        print(f"[Leaderboard] Fetch error: {e}")
        return None


async def post_or_edit_leaderboard(bot, guild_id: int, get_leaderboard_config, set_leaderboard_config):
    lb_cfg = get_leaderboard_config(guild_id)
    if not lb_cfg:
        return
    channel = bot.get_channel(lb_cfg["channel_id"])
    if not channel:
        return
    image_bytes = await fetch_and_render_leaderboard(bot.http_session)
    if not image_bytes:
        return
    if lb_cfg.get("message_id"):
        try:
            msg = await channel.fetch_message(lb_cfg["message_id"])
            image_bytes2 = await fetch_and_render_leaderboard(bot.http_session)
            await msg.edit(attachments=[discord.File(image_bytes2, filename="leaderboard.png")])
            print(f"[Leaderboard] Edited — guild {guild_id}")
            return
        except discord.NotFound:
            pass
        except Exception as e:
            print(f"[Leaderboard] Edit error: {e}")
    file = discord.File(image_bytes, filename="leaderboard.png")
    msg = await channel.send(file=file)
    set_leaderboard_config(guild_id, lb_cfg["channel_id"], msg.id)
    print(f"[Leaderboard] Sent new — guild {guild_id}")


def resolve_text_channel(channel: app_commands.AppCommandChannel):
    resolved = channel.resolve()
    if isinstance(resolved, discord.TextChannel):
        return resolved
    return None
