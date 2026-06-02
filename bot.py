import discord
from discord.ext import commands
import qrcode
from PIL import Image, ImageDraw, ImageFont
import io
import os

# ─────────────────────────────────────────
#  KONFIGURASI
# ─────────────────────────────────────────
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "")

STATIC_QRIS = (
    "00020101021126610014COM.GO-JEK.WWW01189360091437766441850210G7766441850303UMI"
    "51440014ID.CO.QRIS.WWW0215ID10264927394570303UMI5204573253033605802ID5922"
    "MEDUSABLOX, Elektronik6007CIREBON61054515562070703A016304B3A1"
)

MERCHANT_NAME = "MEDUSABLOX, Elektronik"
# ─────────────────────────────────────────


def crc16(data: str) -> str:
    """Hitung CRC16/CCITT-FALSE untuk QRIS."""
    crc = 0xFFFF
    for char in data:
        crc ^= ord(char) << 8
        for _ in range(8):
            crc = (crc << 1) ^ 0x1021 if crc & 0x8000 else crc << 1
            crc &= 0xFFFF
    return format(crc, "04X")


def make_dynamic_qris(static: str, amount: int) -> str:
    """Konversi payload QRIS statis ke dinamis dengan nominal tertentu."""
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


def generate_qris_image(payload: str, amount: int) -> io.BytesIO:
    """Generate gambar QRIS card."""
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

    center_text(draw, MERCHANT_NAME, load_font(22, True), 18)
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


# ── Bot ────────────────────────────────────────────────────────────────────────

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)


@bot.event
async def on_ready():
    print(f"✅ Bot aktif sebagai {bot.user} (ID: {bot.user.id})")


@bot.command(name="qris")
async def pay(ctx: commands.Context, amount_raw: str = None):
    """Generate QRIS dinamis. Contoh: !qris 26000"""
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
            dynamic_payload = make_dynamic_qris(STATIC_QRIS, amount)
            image_bytes = generate_qris_image(dynamic_payload, amount)
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
    embed.set_footer(text="E-Wallet transaction cannot be refunded • MEDUSABLOX, Elektronik")
    await ctx.send(file=file, embed=embed)


@bot.command(name="qrishelp")
async def qris_help(ctx: commands.Context):
    """Tampilkan bantuan."""
    embed = discord.Embed(title="📖 QRIS Bot — Bantuan", color=0x1A1F5E)
    embed.add_field(
        name="!qris <nominal>",
        value="Generate QRIS dinamis dengan nominal tertentu.\n"
              "Contoh:\n`!qris 26000`\n`!qris 150000`\n`!qris 1.500.000`",
        inline=False,
    )
    embed.add_field(name="!qrishelp", value="Tampilkan pesan bantuan ini.", inline=False)
    await ctx.send(embed=embed)


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if not DISCORD_TOKEN:
        print("❌ DISCORD_TOKEN belum diset!")
        print("   Jalankan dengan: DISCORD_TOKEN=xxx python bot.py")
    else:
        bot.run(DISCORD_TOKEN)