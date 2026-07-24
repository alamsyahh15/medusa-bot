# Medusa Helper

Bot Discord untuk QRIS, kalkulasi Robux, cek eligibility Roblox, order/payment flow, giveaway verification, leaderboard, dan rating.

Install bot:
`https://discord.com/oauth2/authorize?client_id=1511463323881701517&permissions=51200&integration_type=0&scope=bot+applications.commands`

Privacy Policy:
`https://alamsyahh15.github.io/medusa-bot/privacy.html`

Terms of Service:
`https://alamsyahh15.github.io/medusa-bot/terms.html`

## Command

### Semua member
| Command | Contoh | Keterangan |
|---|---|---|
| `/qris amount` | `/qris amount:26000` | Generate QRIS dinamis |
| `/calc value` | `/calc value:500` | Kalkulasi Robux/IDR untuk semua metode |
| `/check username_roblox` | `/check username_roblox:Sebas57chan` | Cek eligibility instant group |
| `/giveaway` | `/giveaway discord_user_id:533316628528627802 roblox_username:Sebas57chan` | Cek giveaway via input manual |
| `/leaderboard` | `/leaderboard` | Tampilkan leaderboard Top 3 |
| `/qrisinfo` | `/qrisinfo` | Lihat konfigurasi QRIS server |
| `/qrishelp` | `/qrishelp` | Tampilkan bantuan command |

### Context Menu
| Menu | Target | Keterangan |
|---|---|---|
| `Apps > Giveaway Check` | Message pendaftaran | Cek member Discord + join group Roblox |
| `Apps > Upload Payment` | Message bukti bayar | Buka popup input `order_number`, lalu upload payment proof |

### Admin only
| Command | Keterangan |
|---|---|
| `/qrissetup` | Setup QRIS untuk server ini |
| `/qrisreset` | Hapus konfigurasi QRIS |
| `/setrole` | Atur role yang boleh memakai `/order`, `/payment`, dan `Apps > Upload Payment` |
| `/leaderboardset` | Set atau hapus channel auto leaderboard |
| `/leaderboard-update` | Update leaderboard sekarang |
| `/ratingsetup` | Set atau hapus channel log rating |
| `/rating` | Kirim panel tombol rating |

### Order dan payment
| Command | Contoh | Keterangan |
|---|---|---|
| `/order username amount` | `/order username:Sebas57chan amount:125` | Buat external order |
| `/payment order_number image` | `/payment order_number:EXT-XXXX image:[attachment]` | Upload bukti bayar via slash |

> Bot sekarang memakai slash command dan context menu sebagai flow utama. Prefix command lama tidak lagi dipakai.

## Cara setup QRIS

1. Admin ketik `/qrissetup`
2. Isi:
   - `static_payload`
   - `merchant_name`
3. Test dengan `/qris amount:26000`

### Cara dapat static QRIS payload
Scan QR statis merchant menggunakan QR scanner yang menampilkan teks hasil scan, lalu salin payload-nya.

## Admin Fee

Semua nominal dikenakan biaya admin **0,5%**.

Formula:
`total = nominal + ceil(nominal * 0.005)`

Contoh:

```text
Subtotal:         Rp 26.000
Biaya admin 0,5%: Rp 130
Total bayar:      Rp 26.130
```

## Environment VPS

Contoh `qris-bot.service`:

```ini
[Unit]
Description=Medusa Helper Bot
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/home/Medusablox/qris-bot
Environment=DISCORD_TOKEN=TOKEN_KAMU
Environment=ROBLOX_GROUP_IDS=704572305,198769103
Environment=ROBLOX_API_KEY=API_KEY_ROBLOX_KAMU
Environment=ROBLOX_EXTERNAL_ORDER_API=http://localhost:8000/api/roblox/external/order
Environment=ROBLOX_EXTERNAL_UPLOAD_PAYMENT_API=http://localhost:8000/api/roblox/external/order/upload-payment
Environment=ENABLE_MEMBERS_INTENT=0
Environment=FORCE_SLASH_SYNC=0
ExecStart=/home/Medusablox/qris-bot/venv/bin/python -u bot.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

### Deploy

```bash
ssh root@IP_VPS
apt update && apt install -y python3 python3-pip python3-venv git
cd /home/Medusablox
git clone https://github.com/USERNAME/qris-bot.git
cd qris-bot
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### Start service

```bash
systemctl daemon-reload
systemctl enable qris-bot
systemctl start qris-bot
systemctl status qris-bot
```

### Update code

```bash
cd /home/Medusablox/qris-bot
git pull
systemctl restart qris-bot
```

### Sync slash command sekali saat ada command baru

Set sementara:

```ini
Environment=FORCE_SLASH_SYNC=1
```

Lalu:

```bash
systemctl daemon-reload
systemctl restart qris-bot
journalctl -u qris-bot -f
```

Setelah command muncul, kembalikan lagi ke:

```ini
Environment=FORCE_SLASH_SYNC=0
```

## Struktur repo

```text
qris-bot/
├── bot.py
├── medusa_bot/
│   ├── app.py
│   ├── config.py
│   ├── helpers.py
│   ├── lifecycle.py
│   ├── rating.py
│   └── slash_commands.py
├── privacy.html
├── terms.html
├── requirements.txt
└── config.json
```

## Catatan

- `config.json` dibuat otomatis dan jangan di-commit
- Jangan pernah commit `DISCORD_TOKEN`
- Jika `Server Members Intent` masih review, set `ENABLE_MEMBERS_INTENT=0`
