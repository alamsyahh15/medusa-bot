import discord
from discord.ext import commands, tasks
from discord import app_commands
import qrcode
from PIL import Image, ImageDraw, ImageFont
import io
import os
import json
import math
import aiohttp
from datetime import datetime, timedelta, timezone
from typing import Optional

# ─────────────────────────────────────────
#  KONFIGURASI
# ─────────────────────────────────────────
DISCORD_TOKEN     = os.getenv("DISCORD_TOKEN", "")
CONFIG_FILE       = "config.json"
ADMIN_FEE_RATE    = 0.005
LEADERBOARD_API   = "https://medusablox.com/api/roblox/external/leaderboard"
ROBLOX_GROUP_IDS  = os.getenv("ROBLOX_GROUP_IDS", "")
ROBLOX_API_KEY    = os.getenv("ROBLOX_API_KEY", "")
ROBLOX_USER_LOOKUP_API = "https://users.roblox.com/v1/usernames/users"
ROBLOX_EXTERNAL_ORDER_API = os.getenv("ROBLOX_EXTERNAL_ORDER_API", "http://localhost:8000/api/roblox/external/order")
ROBLOX_EXTERNAL_UPLOAD_PAYMENT_API = os.getenv("ROBLOX_EXTERNAL_UPLOAD_PAYMENT_API", "http://localhost:8000/api/roblox/external/order/upload-payment")
CALC_RATES = {
    "group": 138000,
    "gamepass": 128000,
    "gig": 115000,
}
CALC_MIN_ROBUX = 125
CALC_TYPE_LABELS = {
    "group": "Group Funds",
    "gamepass": "Gamepass",
    "gig": "Gig",
}
# ─────────────────────────────────────────


# ── Config helpers ─────────────────────────────────────────────────────────────

def load_config() -> dict:
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r") as f:
            return json.load(f)
    return {}

def save_config(config: dict):
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=2)

def get_guild_config(guild_id: int) -> Optional[dict]:
    return load_config().get(str(guild_id))

def set_guild_config(guild_id: int, qris: str, merchant: str):
    config = load_config()
    config[str(guild_id)] = {
        "static_qris": qris,
        "merchant_name": merchant,
    }
    save_config(config)

def delete_guild_config(guild_id: int):
    config = load_config()
    if str(guild_id) in config:
        del config[str(guild_id)]
        save_config(config)

def set_leaderboard_config(guild_id: int, channel_id: int, message_id: int = None):
    config = load_config()
    if str(guild_id) not in config:
        config[str(guild_id)] = {}
    config[str(guild_id)]["lb_channel_id"] = channel_id
    if message_id:
        config[str(guild_id)]["lb_message_id"] = message_id
    save_config(config)

def get_leaderboard_config(guild_id: int) -> Optional[dict]:
    cfg = load_config().get(str(guild_id), {})
    channel_id = cfg.get("lb_channel_id")
    if not channel_id:
        return None
    return {"channel_id": channel_id, "message_id": cfg.get("lb_message_id")}


# ── QRIS helpers ───────────────────────────────────────────────────────────────

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
        payload.startswith("000201") and
        "5802ID" in payload and
        len(payload) > 50 and
        ("010211" in payload or "010212" in payload)
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
    embed = discord.Embed(title="🧮 Robux Calc", color=0x1A1F5E)
    embed.description = (
        "**Robux -> IDR**\n"
        "`!calc <robux> [type]`\n\n"
        "**IDR -> Robux**\n"
        "`!calc <amount>rb [type]`"
    )
    embed.add_field(
        name="Tipe yang didukung",
        value="`group/groupfund/gf`, `gamepass/gp`, `gig`",
        inline=False,
    )
    embed.add_field(
        name="Rate",
        value=(
            "`group` = Rp 138.000 / 1.000 Robux\n"
            "`gamepass` = Rp 128.000 / 1.000 Robux\n"
            "`gig` = Rp 115.000 / 1.000 Robux"
        ),
        inline=False,
    )
    embed.add_field(
        name="Contoh",
        value="`!calc 15k groupfunds`\n`!calc 100rb gig`\n`!calc 50rb gamepass`",
        inline=False,
    )
    embed.set_footer(text=f"Tipe default: group • Minimum: {CALC_MIN_ROBUX} Robux")
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

async def lookup_roblox_user(session: aiohttp.ClientSession, username: str) -> Optional[dict]:
    payload = {
        "usernames": [username],
        "excludeBannedUsers": True,
    }
    async with session.post(ROBLOX_USER_LOOKUP_API, json=payload) as resp:
        log_debug("lookup_roblox_user.response", username=username, status=resp.status)
        if resp.status != 200:
            return None
        data = await resp.json()

    users = data.get("data") or []
    if not users:
        return None
    return users[0]

async def get_group_membership(session: aiohttp.ClientSession, group_id: str, user_id: int) -> Optional[dict]:
    headers = {"x-api-key": ROBLOX_API_KEY}
    params = {"filter": f"user == 'users/{user_id}'"}
    url = f"https://apis.roblox.com/cloud/v2/groups/{group_id}/memberships"

    async with session.get(url, headers=headers, params=params) as resp:
        log_debug("get_group_membership.response", group_id=group_id, user_id=user_id, status=resp.status)
        if resp.status != 200:
            return None
        data = await resp.json()

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


# ── Image helpers ──────────────────────────────────────────────────────────────

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
    draw.rectangle([qr_x-8, qr_y-8, qr_x+qr_size+8, qr_y+qr_size+8], outline=(200, 200, 200), width=1)
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
    draw.rectangle([x1+radius, y1, x2-radius, y2], fill=fill)
    draw.rectangle([x1, y1+radius, x2, y2-radius], fill=fill)
    for cx, cy in [(x1,y1),(x2-2*radius,y1),(x1,y2-2*radius),(x2-2*radius,y2-2*radius)]:
        draw.ellipse([cx, cy, cx+2*radius, cy+2*radius], fill=fill)
    if outline:
        draw.arc([x1, y1, x1+2*radius, y1+2*radius], 180, 270, fill=outline, width=width)
        draw.arc([x2-2*radius, y1, x2, y1+2*radius], 270, 360, fill=outline, width=width)
        draw.arc([x1, y2-2*radius, x1+2*radius, y2], 90, 180, fill=outline, width=width)
        draw.arc([x2-2*radius, y2-2*radius, x2, y2], 0, 90, fill=outline, width=width)
        draw.line([x1+radius, y1, x2-radius, y1], fill=outline, width=width)
        draw.line([x1+radius, y2, x2-radius, y2], fill=outline, width=width)
        draw.line([x1, y1+radius, x1, y2-radius], fill=outline, width=width)
        draw.line([x2, y1+radius, x2, y2-radius], fill=outline, width=width)

def generate_leaderboard_image(players: list) -> io.BytesIO:
    RANK_COLORS = [(212,175,55), (180,180,195), (180,120,60)]
    RANK_LABELS = ["Top 1", "Top 2", "Top 3"]
    W, HEADER, ROW_H, PAD, GAP = 480, 110, 90, 20, 10
    H = HEADER + len(players) * (ROW_H + GAP) + PAD
    BG, CARD_BG, CARD_BORDER = (28,22,17), (45,35,25), (80,60,35)
    WHITE, GREY, ACCENT = (255,255,255), (160,150,140), (160,100,220)

    img  = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)

    def cx(text, font, y, color):
        bbox = draw.textbbox((0,0), text, font=font)
        draw.text(((W-(bbox[2]-bbox[0]))//2, y), text, fill=color, font=font)

    cx("L E A D E R B O A R D", _load_font(13), 18, GREY)
    cx("Top 3 Buyer Robux", _load_font(28, True), 40, WHITE)
    cx("Ranking dihitung dari total akumulasi Robux", _load_font(13), 78, GREY)
    cx("per username yang telah dibeli.", _load_font(13), 96, GREY)

    for i, p in enumerate(players[:3]):
        rc = RANK_COLORS[i]
        cy, cx1, cx2 = HEADER + i*(ROW_H+GAP), PAD, W-PAD
        _rounded_rect(draw, [cx1, cy, cx2, cy+ROW_H], 12, CARD_BG, outline=CARD_BORDER, width=1)
        draw.rectangle([cx1, cy+12, cx1+4, cy+ROW_H-12], fill=rc)
        ax, ay = cx1+52, cy+ROW_H//2
        draw.ellipse([ax-24, ay-24, ax+24, ay+24], fill=(60,50,40), outline=rc, width=2)
        ini = p["username"][0].upper()
        f20b = _load_font(20, True)
        bb = draw.textbbox((0,0), ini, font=f20b)
        draw.text((ax-(bb[2]-bb[0])//2, ay-(bb[3]-bb[1])//2-2), ini, fill=rc, font=f20b)
        draw.text((cx1+90, cy+18), p["username"], fill=WHITE, font=_load_font(20, True))
        badge, bx, by = RANK_LABELS[i], cx1+90, cy+48
        bb = draw.textbbox((0,0), badge, font=_load_font(11))
        _rounded_rect(draw, [bx, by, bx+bb[2]-bb[0]+14, by+bb[3]-bb[1]+6], 6, fill=(60,45,20))
        draw.text((bx+7, by+3), badge, fill=rc, font=_load_font(11))
        val = f"{p['total_robux']:,}".replace(",", ".")
        draw.text((cx2-120, cy+14), "TOTAL", fill=GREY, font=_load_font(11))
        f24b = _load_font(24, True)
        bb = draw.textbbox((0,0), val, font=f24b)
        draw.text((cx2-10-(bb[2]-bb[0]), cy+28), val, fill=ACCENT, font=f24b)
        draw.text((cx2-52, cy+58), "Robux", fill=GREY, font=_load_font(12))

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf


# ── Leaderboard fetch ──────────────────────────────────────────────────────────

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

async def post_or_edit_leaderboard(bot, guild_id: int):
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


# ── Bot ────────────────────────────────────────────────────────────────────────

intents = discord.Intents.default()
intents.message_content = True

class QRISBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=intents)
        self.http_session: aiohttp.ClientSession = None

    async def setup_hook(self):
        self.http_session = aiohttp.ClientSession()
        try:
            synced = await self.tree.sync()
            print(f"✅ Slash commands synced: {len(synced)} commands")
        except Exception as e:
            print(f"❌ Sync error: {e}")

    async def close(self):
        if self.http_session:
            await self.http_session.close()
        await super().close()

bot = QRISBot()


# ── Scheduler (didefinisikan SETELAH bot) ──────────────────────────────────────

@tasks.loop(minutes=5)
async def leaderboard_scheduler():
    config = load_config()
    for guild_id_str, cfg in config.items():
        if cfg.get("lb_channel_id"):
            await post_or_edit_leaderboard(bot, int(guild_id_str))

@leaderboard_scheduler.before_loop
async def before_scheduler():
    await bot.wait_until_ready()
    print("✅ Leaderboard scheduler aktif (setiap 5 menit)")


@bot.event
async def on_ready():
    print(f"✅ Bot aktif sebagai {bot.user} (ID: {bot.user.id})")
    print(f"   Terhubung ke {len(bot.guilds)} server")
    if not leaderboard_scheduler.is_running():
        leaderboard_scheduler.start()
        print("✅ Leaderboard scheduler dimulai")


# ── !qris ──────────────────────────────────────────────────────────────────────

@bot.command(name="qris")
async def qris_prefix(ctx: commands.Context, amount_raw: str = None):
    guild_config = get_guild_config(ctx.guild.id)
    if not guild_config:
        await ctx.send(embed=discord.Embed(title="⚙️ QRIS belum dikonfigurasi", description="Admin perlu setup dulu dengan `/qrissetup`", color=0xE67E22))
        return
    if amount_raw is None:
        await ctx.send("❌ Format salah. Contoh: `!qris 26000`"); return
    cleaned = amount_raw.replace(".", "").replace(",", "").replace(" ", "")
    if not cleaned.isdigit():
        await ctx.send("❌ Nominal tidak valid."); return
    amount = int(cleaned)
    if amount <= 0:
        await ctx.send("❌ Nominal harus lebih dari 0."); return
    if amount > 50_000_000:
        await ctx.send("❌ Melebihi batas maksimum Rp 50.000.000."); return

    original_amount = amount
    final_amount = apply_admin_fee(amount)

    async with ctx.typing():
        try:
            payload = make_dynamic_qris(guild_config["static_qris"], final_amount)
            image_bytes = generate_qris_image(payload, final_amount, guild_config["merchant_name"],
                                              original_amount=original_amount)
        except Exception as e:
            await ctx.send(f"❌ Gagal generate QR: {e}"); return

    fee_amount = final_amount - original_amount
    desc = f"Subtotal: **{format_rupiah(original_amount)}**\nBiaya admin (0.5%): **{format_rupiah(fee_amount)}**\nTotal bayar: **{format_rupiah(final_amount)}**"

    file = discord.File(image_bytes, filename=f"qris_{final_amount}.png")
    embed = discord.Embed(title="💳 QRIS Payment", description=desc, color=0x1A1F5E)
    embed.set_image(url=f"attachment://qris_{final_amount}.png")
    embed.set_footer(text=f"E-Wallet transaction cannot be refunded • {guild_config['merchant_name']}")
    await ctx.send(file=file, embed=embed)


@bot.command(name="calc")
async def calc_prefix(ctx: commands.Context, value_raw: str = None, type_raw: str = None):
    if value_raw is None:
        await ctx.send(embed=build_calc_usage_embed())
        return

    calc_type = normalize_calc_type(type_raw)
    if not calc_type:
        await ctx.send("❌ Type tidak valid. Gunakan `group/groupfund/gf`, `gamepass/gp`, atau `gig`.")
        return

    parsed = parse_calc_value(value_raw)
    if not parsed:
        await ctx.send("❌ Format tidak valid. Contoh: `!calc 15k groupfunds`, `!calc 100rb gig`, `!calc 50rb gamepass`")
        return

    calc_mode, amount = parsed
    if amount <= 0:
        await ctx.send("❌ Nilai harus lebih dari 0.")
        return

    rate = CALC_RATES[calc_type]
    type_label = CALC_TYPE_LABELS[calc_type]
    rate_label = f"{format_rupiah(rate)} / 1.000 Robux"

    if calc_mode == "robux":
        if amount < CALC_MIN_ROBUX:
            await ctx.send(f"❌ Minimum kalkulasi adalah **{CALC_MIN_ROBUX} Robux**.")
            return
        total_idr = math.ceil(amount * rate / 1000)
        embed = discord.Embed(title="💰 Robux Calc", color=0x2ECC71)
        embed.add_field(name="Input", value=f"**{amount:,} Robux**".replace(",", "."), inline=False)
        embed.add_field(name="Type", value=type_label, inline=True)
        embed.add_field(name="Rate", value=rate_label, inline=True)
        embed.add_field(name="Hasil", value=f"**{format_rupiah(total_idr)}**", inline=False)
        embed.set_footer(text="Perhitungan IDR dibulatkan ke atas.")
        await ctx.send(embed=embed)
        return

    total_robux = math.floor(amount * 1000 / rate)
    if total_robux < CALC_MIN_ROBUX:
        min_idr = math.ceil(CALC_MIN_ROBUX * rate / 1000)
        await ctx.send(
            f"❌ Minimum kalkulasi adalah **{CALC_MIN_ROBUX} Robux**. "
            f"Untuk type **{type_label}**, minimal input adalah **{format_rupiah(min_idr)}**."
        )
        return
    embed = discord.Embed(title="💵 IDR -> Robux Calc", color=0x3498DB)
    embed.add_field(name="Input", value=f"**{format_rupiah(amount)}**", inline=False)
    embed.add_field(name="Type", value=type_label, inline=True)
    embed.add_field(name="Rate", value=rate_label, inline=True)
    embed.add_field(name="Hasil", value=f"**{total_robux:,} Robux**".replace(",", "."), inline=False)
    embed.set_footer(text="Estimasi Robux dibulatkan ke bawah.")
    await ctx.send(embed=embed)


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

    embed = discord.Embed(title="🔎 Cek Membership Roblox", color=0x1A1F5E)
    embed.add_field(name="Username", value=user_data["name"], inline=True)
    embed.add_field(name="Display Name", value=user_data.get("displayName", "-"), inline=True)
    embed.add_field(name="User ID", value=str(user_data["id"]), inline=True)

    group_lines = []
    all_groups_ready = True
    latest_available_at = None
    for index, result in enumerate(group_results, start=1):
        group_id = result["group_id"]
        if not result["membership_found"]:
            all_groups_ready = False
            group_lines.append(f"Group {index} (`{group_id}`): ❌ Belum join group")
            continue

        create_time = result["create_time"]
        available_at = result["available_at"]
        if latest_available_at is None or available_at > latest_available_at:
            latest_available_at = available_at

        if result["is_ready"]:
            group_lines.append(
                f"Group {index} (`{group_id}`): ✅ Join {format_datetime_gmt7(create_time)}"
            )
        else:
            all_groups_ready = False
            group_lines.append(
                f"Group {index} (`{group_id}`): ⏳ Join {format_datetime_gmt7(create_time)} • Eligible {format_datetime_gmt7(available_at)}"
            )

    embed.add_field(name="Cek Group", value="\n".join(group_lines), inline=False)

    if all_groups_ready and group_results:
        log_debug("check.eligible", username=user_data["name"], user_id=user_data["id"], group_count=len(group_results))
        embed.add_field(name="Status", value="✅ Available to order robux instant group", inline=False)
        embed.color = 0x2ECC71
    else:
        log_debug(
            "check.not_eligible",
            username=user_data["name"],
            user_id=user_data["id"],
            group_count=len(group_results),
            latest_available_at=format_datetime_gmt7(latest_available_at) if latest_available_at else "unknown",
        )
        status_value = "⏳ Belum eligible di semua group. Semua group harus join minimal 3 hari."
        if latest_available_at:
            status_value += f"\nBisa order setelah semua group siap, estimasi **{format_datetime_gmt7(latest_available_at)}**."
        embed.add_field(
            name="Status",
            value=status_value,
            inline=False,
        )
        embed.color = 0xF1C40F

    await ctx.send(embed=embed)


@bot.command(name="order")
async def order_prefix(ctx: commands.Context, amount_raw: str = None):
    log_debug("order.invoked", author=getattr(ctx.author, "id", None), guild=getattr(ctx.guild, "id", None), amount_raw=amount_raw, has_reply=bool(getattr(ctx.message, "reference", None)))
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
    if check_embed.title != "🔎 Cek Membership Roblox":
        log_debug("order.invalid_reply", reason="wrong_embed_title", title=check_embed.title)
        await ctx.send("❌ Reply harus ke message hasil `!check`.")
        return

    username = get_embed_field_value(check_embed, "Username")
    status = get_embed_field_value(check_embed, "Status") or ""
    if not username:
        log_debug("order.invalid_reply", reason="missing_username_field")
        await ctx.send("❌ Username Roblox tidak ditemukan di message `!check`.")
        return
    if "Available to order robux instant group" not in status:
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
    embed = discord.Embed(
        title="✅ Order berhasil dibuat",
        description=order_response.get("message", "External order placed successfully."),
        color=0x2ECC71,
    )
    embed.add_field(name="Order Number", value=order_data.get("order_number", "-"), inline=True)
    embed.add_field(name="Username", value=order_data.get("username", username), inline=True)
    embed.add_field(name="Amount", value=f"{order_data.get('amount', amount)} Robux", inline=True)
    embed.add_field(name="Method", value=order_data.get("method", "-"), inline=True)
    embed.add_field(name="Total Price", value=format_rupiah(int(order_data.get("total_price", 0))), inline=True)
    embed.add_field(name="Status", value=order_data.get("status", "-"), inline=True)

    order_url = (order_data.get("order_url") or "").strip().strip("`").strip()
    if order_url:
        embed.add_field(name="Order URL", value=f"[Klik untuk buka order]({order_url})", inline=False)

    avatar_url = (order_data.get("avatar") or "").strip()
    if avatar_url:
        embed.set_thumbnail(url=avatar_url)

    guild_config = get_guild_config(ctx.guild.id) if ctx.guild else None
    total_price = int(order_data.get("total_price", 0) or 0)
    if guild_config and total_price > 0:
        try:
            qris_total = apply_admin_fee(total_price)
            qris_payload = make_dynamic_qris(guild_config["static_qris"], qris_total)
            qris_image = generate_qris_image(
                qris_payload,
                qris_total,
                guild_config["merchant_name"],
                original_amount=total_price,
            )
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
    embed = discord.Embed(
        title="✅ Payment berhasil diupload",
        description=upload_response.get("message", "Payment uploaded successfully."),
        color=0x2ECC71,
    )
    embed.add_field(name="Order Number", value=upload_data.get("order_number", order_number), inline=True)
    embed.add_field(name="Status", value=upload_data.get("status", "done"), inline=True)
    embed.add_field(name="Image URL", value=f"[Klik untuk buka bukti bayar]({image_url})", inline=False)
    await ctx.send(embed=embed)


# ── !leaderboard ───────────────────────────────────────────────────────────────

@bot.command(name="leaderboard", aliases=["lb", "top"])
async def leaderboard_prefix(ctx: commands.Context):
    async with ctx.typing():
        image_bytes = await fetch_and_render_leaderboard(bot.http_session)
        if not image_bytes:
            await ctx.send("❌ Gagal fetch data leaderboard."); return
    await ctx.send(file=discord.File(image_bytes, filename="leaderboard.png"))


# ── /qrissetup ─────────────────────────────────────────────────────────────────

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
    embed.add_field(name="Test sekarang", value="`!qris 10000`", inline=False)
    await interaction.followup.send(embed=embed, ephemeral=True)


# ── /qrisinfo ──────────────────────────────────────────────────────────────────

@bot.tree.command(name="qrisinfo", description="Lihat konfigurasi QRIS server ini")
async def qris_info(interaction: discord.Interaction):
    cfg = get_guild_config(interaction.guild.id)
    if not cfg:
        await interaction.response.send_message(embed=discord.Embed(title="⚙️ Belum ada konfigurasi QRIS", description="Admin gunakan `/qrissetup`.", color=0xE67E22), ephemeral=True)
        return
    fee_status = "✅ Aktif untuk semua nominal (+0.5%)"
    embed = discord.Embed(title="📋 Konfigurasi QRIS Server", color=0x1A1F5E)
    embed.add_field(name="Merchant", value=cfg["merchant_name"], inline=False)
    embed.add_field(name="Payload (preview)", value=f"`{cfg['static_qris'][:50]}...`", inline=False)
    embed.add_field(name="Biaya Admin", value=fee_status, inline=False)
    embed.add_field(name="Status", value="✅ Aktif", inline=True)
    await interaction.response.send_message(embed=embed, ephemeral=True)


# ── /qrisreset ─────────────────────────────────────────────────────────────────

@bot.tree.command(name="qrisreset", description="Hapus konfigurasi QRIS server ini (Admin only)")
@app_commands.default_permissions(administrator=True)
async def qris_reset(interaction: discord.Interaction):
    if not get_guild_config(interaction.guild.id):
        await interaction.response.send_message("⚠️ Server ini belum memiliki konfigurasi QRIS.", ephemeral=True); return
    delete_guild_config(interaction.guild.id)
    await interaction.response.send_message(embed=discord.Embed(title="🗑️ Konfigurasi QRIS dihapus", description="Gunakan `/qrissetup` untuk mengatur ulang.", color=0xE74C3C), ephemeral=True)


# ── /qrishelp ──────────────────────────────────────────────────────────────────

@bot.tree.command(name="qrishelp", description="Tampilkan semua perintah QRIS Bot")
async def qris_help(interaction: discord.Interaction):
    embed = discord.Embed(title="📖 QRIS Bot — Bantuan", color=0x1A1F5E)
    embed.add_field(name="!qris <nominal>", value="Generate QRIS. Contoh: `!qris 26000`", inline=False)
    embed.add_field(name="!calc <nilai> [type]", value="Kalkulasi Robux/IDR. Contoh: `!calc 15k groupfunds` atau `!calc 100rb gig`", inline=False)
    embed.add_field(name="!check <username>", value="Cek apakah user Roblox sudah 3 hari di group.", inline=False)
    embed.add_field(name="!order <robux>", value="Reply ke hasil `!check` yang eligible untuk buat order. Minimum `125` Robux.", inline=False)
    embed.add_field(name="!payment <order_number>", value="Reply ke message customer yang berisi bukti bayar untuk upload payment.", inline=False)
    embed.add_field(name="!leaderboard", value="Tampilkan leaderboard Top 3.", inline=False)
    embed.add_field(name="/qrissetup 🔒", value="Setup QRIS server ini. (Admin only)", inline=False)
    embed.add_field(name="/qrisinfo", value="Lihat konfigurasi QRIS.", inline=False)
    embed.add_field(name="/qrisreset 🔒", value="Hapus konfigurasi QRIS. (Admin only)", inline=False)
    embed.add_field(name="/leaderboardset 🔒", value="Set channel leaderboard. (Admin only)", inline=False)
    embed.add_field(name="/leaderboard-update 🔒", value="Update leaderboard sekarang. (Admin only)", inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)


# ── /leaderboardset ────────────────────────────────────────────────────────────

@bot.tree.command(name="leaderboardset", description="Set channel untuk auto-update leaderboard (Admin only)")
@app_commands.describe(channel="Channel tujuan leaderboard")
@app_commands.default_permissions(administrator=True)
async def leaderboard_set(interaction: discord.Interaction, channel: discord.TextChannel):
    await interaction.response.defer(ephemeral=True)
    set_leaderboard_config(interaction.guild.id, channel.id, None)
    image_bytes = await fetch_and_render_leaderboard(bot.http_session)
    if not image_bytes:
        await interaction.followup.send("❌ Gagal fetch data. Channel disimpan, akan dicoba lagi 5 menit.", ephemeral=True)
        return
    msg = await channel.send(file=discord.File(image_bytes, filename="leaderboard.png"))
    set_leaderboard_config(interaction.guild.id, channel.id, msg.id)
    embed = discord.Embed(title="✅ Leaderboard channel berhasil diset!", color=0x2ECC71)
    embed.add_field(name="Channel", value=channel.mention, inline=False)
    embed.add_field(name="Auto-update", value="Setiap 5 menit", inline=False)
    embed.add_field(name="Manual update", value="`/leaderboard-update`", inline=False)
    await interaction.followup.send(embed=embed, ephemeral=True)


# ── /leaderboard-update ────────────────────────────────────────────────────────

@bot.tree.command(name="leaderboard-update", description="Update leaderboard sekarang (Admin only)")
@app_commands.default_permissions(administrator=True)
async def leaderboard_update(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    lb_cfg = get_leaderboard_config(interaction.guild.id)
    if not lb_cfg:
        await interaction.followup.send("❌ Channel leaderboard belum diset. Gunakan `/leaderboardset` dulu.", ephemeral=True); return
    await post_or_edit_leaderboard(bot, interaction.guild.id)
    channel = bot.get_channel(lb_cfg["channel_id"])
    embed = discord.Embed(title="✅ Leaderboard berhasil diupdate!", color=0x2ECC71)
    embed.add_field(name="Channel", value=channel.mention if channel else str(lb_cfg["channel_id"]), inline=False)
    await interaction.followup.send(embed=embed, ephemeral=True)


# ── Error handler ──────────────────────────────────────────────────────────────

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("❌ Kamu tidak punya izin untuk perintah ini. (Admin only)")
    elif isinstance(error, commands.CommandNotFound):
        pass


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if not DISCORD_TOKEN:
        print("❌ DISCORD_TOKEN belum diset!")
    else:
        bot.run(DISCORD_TOKEN)
