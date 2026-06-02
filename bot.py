import discord
from discord.ext import commands
from discord import app_commands
import qrcode
from PIL import Image, ImageDraw, ImageFont
import io
import os
import json

# ─────────────────────────────────────────
#  KONFIGURASI
# ─────────────────────────────────────────
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "")
CONFIG_FILE = "config.json"
# ─────────────────────────────────────────


# ── Config manager ─────────────────────────────────────────────────────────────

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

def set_guild_config(guild_id: int, qris: str, merchant: str):
    config = load_config()
    config[str(guild_id)] = {"static_qris": qris, "merchant_name": merchant}
    save_config(config)

def delete_guild_config(guild_id: int):
    config = load_config()
    if str(guild_id) in config:
        del config[str(guild_id)]
        save_config(config)


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

def generate_qris_image(payload: str, amount: int, merchant_name: str) -> io.BytesIO:
    qr = qrcode.QRCode(
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=2,
    )
    qr.add_data(payload)
    qr.make(fit=True)
    qr_img = qr.make_image(fill_color="black", back_color="white").convert("RGBA")
    qr_size = qr_img.size[0]

    padding, header_h, footer_h = 40, 90, 60
    card_w = qr_size + padding * 2
    card_h = qr_size + padding * 2 + header_h + footer_h

    card = Image.new("RGBA", (card_w, card_h), (255, 255, 255, 255))
    draw = ImageDraw.Draw(card)

    def load_font(size, bold=False):
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

    def center_text(draw, text, font, y, color=(15, 23, 58)):
        bbox = draw.textbbox((0, 0), text, font=font)
        x = (card_w - (bbox[2] - bbox[0])) // 2
        draw.text((x, y), text, fill=color, font=font)

    center_text(draw, merchant_name, load_font(22, True), 18)
    center_text(draw, f"Payment of IDR {amount:,}".replace(",", "."), load_font(20, True), 50, (30, 30, 30))

    qr_x, qr_y = padding, header_h + padding
    draw.rectangle([qr_x - 8, qr_y - 8, qr_x + qr_size + 8, qr_y + qr_size + 8], outline=(200, 200, 200), width=1)
    card.paste(qr_img, (qr_x, qr_y))

    footer_y = qr_y + qr_size + 16
    center_text(draw, "E-Wallet transaction cannot be refunded", load_font(14), footer_y, (120, 120, 120))
    center_text(draw, "Code by MedusaBlox", load_font(14), footer_y + 20, (120, 120, 120))

    buf = io.BytesIO()
    card.convert("RGB").save(buf, format="PNG")
    buf.seek(0)
    return buf


# ── Bot setup ──────────────────────────────────────────────────────────────────

intents = discord.Intents.default()
intents.message_content = True

class QRISBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        # Sync slash commands ke semua server
        await self.tree.sync()
        print("✅ Slash commands synced")

    async def on_ready(self):
        print(f"✅ Bot aktif sebagai {self.user} (ID: {self.user.id})")
        print(f"   Terhubung ke {len(self.guilds)} server")

bot = QRISBot()


# ── Prefix command: !qris ──────────────────────────────────────────────────────

@bot.command(name="qris")
async def qris_prefix(ctx: commands.Context, amount_raw: str = None):
    """Generate QRIS dinamis. Contoh: !qris 26000"""
    guild_config = get_guild_config(ctx.guild.id)
    if not guild_config:
        embed = discord.Embed(
            title="⚙️ QRIS belum dikonfigurasi",
            description="Admin perlu setup dulu dengan `/qrissetup`",
            color=0xE67E22,
        )
        await ctx.send(embed=embed)
        return

    if amount_raw is None:
        await ctx.send("❌ Format salah. Contoh: `!qris 26000`")
        return

    cleaned = amount_raw.replace(".", "").replace(",", "").replace(" ", "")
    if not cleaned.isdigit():
        await ctx.send("❌ Nominal tidak valid. Gunakan angka, misal: `!qris 26000`")
        return

    amount = int(cleaned)
    if amount <= 0:
        await ctx.send("❌ Nominal harus lebih dari 0.")
        return
    if amount > 50_000_000:
        await ctx.send("❌ Nominal melebihi batas maksimum (Rp 50.000.000).")
        return

    async with ctx.typing():
        try:
            dynamic_payload = make_dynamic_qris(guild_config["static_qris"], amount)
            image_bytes = generate_qris_image(dynamic_payload, amount, guild_config["merchant_name"])
        except Exception as e:
            await ctx.send(f"❌ Gagal generate QR: {e}")
            return

    file = discord.File(image_bytes, filename=f"qris_{amount}.png")
    embed = discord.Embed(
        title="💳 QRIS Payment",
        description=f"Scan QR di bawah untuk membayar **{format_rupiah(amount)}**",
        color=0x1A1F5E,
    )
    embed.set_image(url=f"attachment://qris_{amount}.png")
    embed.set_footer(text=f"E-Wallet transaction cannot be refunded • {guild_config['merchant_name']}")
    await ctx.send(file=file, embed=embed)


# ── Slash command: /qrissetup ──────────────────────────────────────────────────

@bot.tree.command(name="qrissetup", description="Setup QRIS untuk server ini (Admin only)")
@app_commands.describe(
    static_payload="Payload QRIS statis dari QR merchant kamu",
    merchant_name="Nama merchant yang tampil di QR (contoh: Toko Budi)",
)
@app_commands.default_permissions(administrator=True)
async def qris_setup(interaction: discord.Interaction, static_payload: str, merchant_name: str):
    await interaction.response.defer(ephemeral=True)

    if not validate_qris(static_payload):
        embed = discord.Embed(
            title="❌ Payload tidak valid",
            description=(
                "String yang dimasukkan bukan payload QRIS yang valid.\n\n"
                "Pastikan payload:\n"
                "• Dimulai dengan `000201`\n"
                "• Mengandung `5802ID`\n"
                "• Merupakan QRIS **statis** (mengandung `010211`)"
            ),
            color=0xE74C3C,
        )
        await interaction.followup.send(embed=embed, ephemeral=True)
        return

    set_guild_config(interaction.guild.id, static_payload, merchant_name)

    embed = discord.Embed(title="✅ QRIS berhasil dikonfigurasi!", color=0x2ECC71)
    embed.add_field(name="Merchant", value=merchant_name, inline=False)
    embed.add_field(name="Payload (preview)", value=f"`{static_payload[:50]}...`", inline=False)
    embed.add_field(name="Test sekarang", value="`!qris 10000`", inline=False)
    await interaction.followup.send(embed=embed, ephemeral=True)


# ── Slash command: /qrisinfo ───────────────────────────────────────────────────

@bot.tree.command(name="qrisinfo", description="Lihat konfigurasi QRIS server ini")
async def qris_info(interaction: discord.Interaction):
    guild_config = get_guild_config(interaction.guild.id)

    if not guild_config:
        embed = discord.Embed(
            title="⚙️ Belum ada konfigurasi QRIS",
            description="Admin gunakan `/qrissetup` untuk mengatur QRIS server ini.",
            color=0xE67E22,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    embed = discord.Embed(title="📋 Konfigurasi QRIS Server", color=0x1A1F5E)
    embed.add_field(name="Merchant", value=guild_config["merchant_name"], inline=False)
    embed.add_field(name="Payload (preview)", value=f"`{guild_config['static_qris'][:50]}...`", inline=False)
    embed.add_field(name="Status", value="✅ Aktif", inline=True)
    await interaction.response.send_message(embed=embed, ephemeral=True)


# ── Slash command: /qrisreset ──────────────────────────────────────────────────

@bot.tree.command(name="qrisreset", description="Hapus konfigurasi QRIS server ini (Admin only)")
@app_commands.default_permissions(administrator=True)
async def qris_reset(interaction: discord.Interaction):
    guild_config = get_guild_config(interaction.guild.id)
    if not guild_config:
        await interaction.response.send_message("⚠️ Server ini belum memiliki konfigurasi QRIS.", ephemeral=True)
        return

    delete_guild_config(interaction.guild.id)
    embed = discord.Embed(
        title="🗑️ Konfigurasi QRIS dihapus",
        description="Gunakan `/qrissetup` untuk mengatur ulang.",
        color=0xE74C3C,
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)


# ── Slash command: /qrishelp ───────────────────────────────────────────────────

@bot.tree.command(name="qrishelp", description="Tampilkan semua perintah QRIS Bot")
async def qris_help(interaction: discord.Interaction):
    embed = discord.Embed(title="📖 QRIS Bot — Bantuan", color=0x1A1F5E)
    embed.add_field(name="!qris <nominal>", value="Generate QRIS. Contoh: `!qris 26000`", inline=False)
    embed.add_field(name="/qrissetup 🔒", value="Setup QRIS untuk server ini. (Admin only)", inline=False)
    embed.add_field(name="/qrisinfo", value="Lihat konfigurasi QRIS server ini.", inline=False)
    embed.add_field(name="/qrisreset 🔒", value="Hapus konfigurasi QRIS. (Admin only)", inline=False)
    embed.add_field(name="/qrishelp", value="Tampilkan bantuan ini.", inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)


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
        print("   Jalankan dengan: DISCORD_TOKEN=xxx python bot.py")
    else:
        bot.run(DISCORD_TOKEN)