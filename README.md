# QRIS Discord Bot — Multi Server

Bot Discord untuk generate QR Code QRIS dinamis, dengan konfigurasi QRIS per server Discord.

---

## Install bot

Klik link berikut untuk invite bot ke server Discord:

https://discord.com/oauth2/authorize?client_id=1511463323881701517&permissions=51200&integration_type=0&scope=bot+applications.commands

---

## Perintah

### Generate QR (semua member)
| Perintah | Contoh | Keterangan |
|---|---|---|
| `!qris <nominal>` | `!qris 26000` | Generate QRIS QR code |

### Slash Commands (semua member)
| Perintah | Keterangan |
|---|---|
| `/qrisinfo` | Lihat konfigurasi QRIS server |
| `/qrishelp` | Tampilkan semua perintah |

### Slash Commands (Admin only 🔒)
| Perintah | Keterangan |
|---|---|
| `/qrissetup` | Setup QRIS untuk server ini |
| `/qrisreset` | Hapus konfigurasi QRIS server |

> Semua slash command responsenya hanya terlihat oleh pengirim (ephemeral), tidak spam di channel.

---

## Cara setup QRIS di server baru

1. Admin ketik `/qrissetup` di Discord
2. Isi 2 field yang muncul:
   - **static_payload** — payload QRIS statis dari QR merchant
   - **merchant_name** — nama yang tampil di QR (contoh: `Toko Budi`)
3. Test: `!qris 26000`

### Cara dapat static QRIS payload
Scan QR statis merchant menggunakan QR scanner yang menampilkan teks hasil scan (misal ZXing, QR & Barcode Scanner). Salin teks yang muncul — itulah static payload-nya.

---

## Struktur repo

```
qris-bot/
├── bot.py              # Kode utama bot
├── requirements.txt    # Library Python
├── qris-bot.service    # File systemd untuk VPS
├── config.json         # Konfigurasi QRIS per server (auto-generated)
└── README.md
```

> `config.json` dibuat otomatis saat pertama kali `/qrissetup` dijalankan. Jangan di-commit ke GitHub karena berisi data server.

---

## Deploy ke VPS Ubuntu

### Setup awal (sekali saja)

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

### Setup systemd

```bash
nano /etc/systemd/system/qris-bot.service
```

Isi:
```ini
[Unit]
Description=QRIS Discord Bot
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/home/Medusablox/qris-bot
Environment=DISCORD_TOKEN=TOKEN_KAMU_DISINI
ExecStart=/home/Medusablox/qris-bot/venv/bin/python bot.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
systemctl daemon-reload
systemctl enable qris-bot
systemctl start qris-bot
systemctl status qris-bot
```

### Update kode

```bash
cd /home/Medusablox/qris-bot
git pull
systemctl restart qris-bot
```

> Setelah restart, slash commands butuh beberapa menit untuk muncul di Discord karena perlu sync.

---

## Perintah systemd berguna

```bash
systemctl status qris-bot       # Cek status
systemctl restart qris-bot      # Restart bot
systemctl stop qris-bot         # Stop bot
journalctl -u qris-bot -f       # Lihat log real-time
```

---

## .gitignore (recommended)

Buat file `.gitignore` di repo agar token dan config tidak ter-commit:

```
config.json
.env
__pycache__/
*.pyc
venv/
```

---

> ⚠️ Jangan pernah commit `DISCORD_TOKEN` ke GitHub.
> Simpan token hanya di file systemd service di VPS.
