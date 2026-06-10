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

# ─────────────────────────────────────────
#  KONFIGURASI
# ─────────────────────────────────────────
DISCORD_TOKEN     = os.getenv("DISCORD_TOKEN", "")
CONFIG_FILE       = "config.json"
ADMIN_FEE_RATE    = 0.003
ADMIN_FEE_THRESHOLD = 500000
LEADERBOARD_API   = "https://medusablox.com/api/roblox/external/leaderboard"
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

def get_guild_config(guild_id: int) -> dict | None:
    return load_config().get(str(guild_id))

def set_guild_config(guild_id: int, qris: str, merchant: str, admin_fee: bool):
    config = load_config()
    config[str(guild_id)] = {
        "static_qris": qris,
        "merchant_name": merchant,
        "activate_admin_fee": admin_fee,
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

def get_leaderboard_config(guild_id: int) -> dict | None:
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
    if amount > ADMIN_FEE_THRESHOLD:
        return math.floor(amount / (1 - ADMIN_FEE_RATE))
    return amount

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
        center_text(f"Subtotal: {format_rupiah(original_amount)}  |  Admin fee (0.3%): {format_rupiah(fee_amount)}", _load_font(12), footer_y, (100, 100, 100))
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

async def fetch_and_render_leaderboard(session) -> io.BytesIO | None:
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
        await self.tree.sync()
        print("✅ Slash commands synced")

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
    use_fee = guild_config.get("activate_admin_fee", False)
    final_amount = apply_admin_fee(amount) if use_fee else amount

    async with ctx.typing():
        try:
            payload = make_dynamic_qris(guild_config["static_qris"], final_amount)
            image_bytes = generate_qris_image(payload, final_amount, guild_config["merchant_name"],
                                              original_amount=original_amount if use_fee else None)
        except Exception as e:
            await ctx.send(f"❌ Gagal generate QR: {e}"); return

    if use_fee and final_amount != original_amount:
        fee_amount = final_amount - original_amount
        desc = f"Subtotal: **{format_rupiah(original_amount)}**\nAdmin fee (0.3%): **{format_rupiah(fee_amount)}**\nTotal bayar: **{format_rupiah(final_amount)}**"
    else:
        desc = f"Scan QR di bawah untuk membayar **{format_rupiah(final_amount)}**"

    file = discord.File(image_bytes, filename=f"qris_{final_amount}.png")
    embed = discord.Embed(title="💳 QRIS Payment", description=desc, color=0x1A1F5E)
    embed.set_image(url=f"attachment://qris_{final_amount}.png")
    embed.set_footer(text=f"E-Wallet transaction cannot be refunded • {guild_config['merchant_name']}")
    await ctx.send(file=file, embed=embed)


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
    activate_admin_fee="Aktifkan admin fee 0.3% untuk nominal > Rp 500.000 (default: False)",
)
@app_commands.default_permissions(administrator=True)
async def qris_setup(interaction: discord.Interaction, static_payload: str, merchant_name: str, activate_admin_fee: bool = False):
    await interaction.response.defer(ephemeral=True)
    if not validate_qris(static_payload):
        await interaction.followup.send(embed=discord.Embed(title="❌ Payload tidak valid", description="Pastikan payload dimulai `000201`, mengandung `5802ID`, dan merupakan QRIS statis (`010211`).", color=0xE74C3C), ephemeral=True)
        return
    set_guild_config(interaction.guild.id, static_payload, merchant_name, activate_admin_fee)
    fee_status = "✅ Aktif (nominal > Rp 500.000 dikenakan +0.3%)" if activate_admin_fee else "❌ Tidak aktif"
    embed = discord.Embed(title="✅ QRIS berhasil dikonfigurasi!", color=0x2ECC71)
    embed.add_field(name="Merchant", value=merchant_name, inline=False)
    embed.add_field(name="Payload (preview)", value=f"`{static_payload[:50]}...`", inline=False)
    embed.add_field(name="Admin Fee", value=fee_status, inline=False)
    embed.add_field(name="Test sekarang", value="`!qris 10000`", inline=False)
    await interaction.followup.send(embed=embed, ephemeral=True)


# ── /qrisinfo ──────────────────────────────────────────────────────────────────

@bot.tree.command(name="qrisinfo", description="Lihat konfigurasi QRIS server ini")
async def qris_info(interaction: discord.Interaction):
    cfg = get_guild_config(interaction.guild.id)
    if not cfg:
        await interaction.response.send_message(embed=discord.Embed(title="⚙️ Belum ada konfigurasi QRIS", description="Admin gunakan `/qrissetup`.", color=0xE67E22), ephemeral=True)
        return
    fee_status = "✅ Aktif (nominal > Rp 500.000 dikenakan +0.3%)" if cfg.get("activate_admin_fee") else "❌ Tidak aktif"
    embed = discord.Embed(title="📋 Konfigurasi QRIS Server", color=0x1A1F5E)
    embed.add_field(name="Merchant", value=cfg["merchant_name"], inline=False)
    embed.add_field(name="Payload (preview)", value=f"`{cfg['static_qris'][:50]}...`", inline=False)
    embed.add_field(name="Admin Fee", value=fee_status, inline=False)
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