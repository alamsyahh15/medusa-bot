# QRIS Discord Bot — Multi Server

Bot Discord untuk generate QR Code QRIS dinamis, dengan konfigurasi QRIS per server Discord.

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
2. Isi 3 field yang muncul:
   - **static_payload** — payload QRIS statis dari QR merchant
   - **merchant_name** — nama yang tampil di QR (contoh: `Toko Budi`)
   - **activate_admin_fee** — `True` / `False` (default: `False`)
3. Test: `!qris 26000`

### Cara dapat static QRIS payload
Scan QR statis merchant menggunakan QR scanner yang menampilkan teks hasil scan (misal ZXing, QR & Barcode Scanner). Salin teks yang muncul — itulah static payload-nya.

---

## Admin Fee

Jika `activate_admin_fee` diset `True`, bot akan menambahkan fee **0.3%** untuk transaksi dengan nominal **> Rp 500.000**.

**Formula:** `total = floor(nominal / (1 - 0.003))`

| Nominal | Admin Fee | Total Bayar |
|---|---|---|
| Rp 26.000 | ❌ (di bawah threshold) | Rp 26.000 |
| Rp 500.000 | ❌ (tepat di threshold) | Rp 500.000 |
| Rp 500.001 | ✅ +0.3% | Rp 501.502 |
| Rp 1.000.000 | ✅ +0.3% | Rp 1.003.010 |

Jika fee aktif, embed dan gambar QR akan menampilkan breakdown:
```
Subtotal:         Rp 500.001
Admin fee (0.3%): Rp 1.501
Total bayar:      Rp 501.502
```

---

## Struktur repo

```
qris-bot/
├── bot.py              # Kode utama bot
├── requirements.txt    # Library Python
├── qris-bot.service    # File systemd untuk VPS
├── .gitignore
├── config.json         # Konfigurasi QRIS per server (auto-generated, tidak di-commit)
└── README.md
```

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
Environment=DISCORD_TOKEN=TOKEN_KAMU
Environment=ROBLOX_GROUP_ID=704572305
Environment=ROBLOX_API_KEY=API_KEY_ROBLOX_KAMU
ExecStart=/home/Medusablox/qris-bot/venv/bin/python -u bot.py
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

> Setelah restart, tunggu 1-2 menit lalu refresh Discord agar slash commands ter-sync.

---

## Perintah systemd berguna

```bash
systemctl status qris-bot       # Cek status
systemctl restart qris-bot      # Restart bot
systemctl stop qris-bot         # Stop bot
journalctl -u qris-bot -f       # Lihat log real-time
```

---

> ⚠️ Jangan pernah commit `DISCORD_TOKEN` ke GitHub.
> `config.json` dibuat otomatis dan sudah di-exclude via `.gitignore`.
