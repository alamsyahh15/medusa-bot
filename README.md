# QRIS Discord Bot

Bot Discord untuk generate QR Code QRIS dinamis langsung dari chat.

## Perintah

```
!pay 26000        → Generate QRIS Rp 26.000
!pay 150.000      → Format titik juga diterima
!pay 1500000      → Generate QRIS Rp 1.500.000
!qrishelp         → Tampilkan bantuan
```

---

## Isi repo ini

```
qris-bot/
├── bot.py            # Kode utama bot
├── requirements.txt  # Library Python
├── qris-bot.service  # File systemd (untuk VPS)
└── README.md
```

---

## Deploy ke VPS Ubuntu (via GitHub)

### 1. Clone repo di VPS

```bash
ssh ubuntu@IP_VPS_KAMU

sudo apt update && sudo apt install -y python3 python3-pip python3-venv git

cd /home/ubuntu
git clone https://github.com/USERNAME/qris-bot.git
cd qris-bot

python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Setup systemd

```bash
sudo nano /etc/systemd/system/qris-bot.service
```

Isi dengan konten file `qris-bot.service`, ganti `ISI_TOKEN_KAMU_DISINI` dengan token bot Discord kamu.

```bash
sudo systemctl daemon-reload
sudo systemctl enable qris-bot
sudo systemctl start qris-bot
sudo systemctl status qris-bot
```

### 3. Update kode (setiap ada perubahan)

```bash
cd /home/ubuntu/qris-bot
git pull
sudo systemctl restart qris-bot
```

---

## Perintah systemd berguna

```bash
sudo systemctl status qris-bot      # Cek status
sudo systemctl restart qris-bot     # Restart
sudo systemctl stop qris-bot        # Stop
sudo journalctl -u qris-bot -f      # Lihat log real-time
```

---

## Cara kerja konversi QRIS statis → dinamis

| Langkah | Penjelasan |
|---------|-----------|
| `010211` → `010212` | Ganti mode static ke dynamic |
| Sisipkan field `54` | Tag amount EMV sebelum field `5802ID` |
| Hitung ulang CRC16 | CRC baru dari seluruh payload |

> ⚠️ Jangan pernah commit token Discord ke GitHub. Simpan token hanya di file systemd service di VPS.