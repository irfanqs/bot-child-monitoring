# Getting Started

1. Clone this repo <br>
   ```bash
   git clone https://github.com/irfanqs/bot-child-monitoring.git
   cd bot-child-monitoring
   ```
2. Install all dependencies

   ```bash
   pip install -r requirements.txt
   ```

3. Run the code

   ```bash
   python webhook-antares.py
   ```

4. Expose port 5000 using ngrok
   ```bash
   ngrok http 5000
   ```
5. Set webhook on Antares Console
   ```bash
   # dont forget to add /monitor
   https://abcdef-id.ngrok-free.app/monitor
   ```

# Parent-Teacher Child Monitoring Bot Setup Guide

## Sistem Registrasi Terdistribusi

### 1. **Database Schema**

Sistem menggunakan SQLite dengan 3 tabel utama:

- **`children`**: Menyimpan data anak (child_id, nama, device_id)
- **`parent_child_mapping`**: Mapping parent-child dengan role (parent/teacher)
- **`monitoring_sessions`**: Log session monitoring untuk tracking

### ⚠️ **DATABASE YANG PERLU DIEDIT:**

**Tabel `parent_child_mapping` - Tambah kolom `role` dan `phone_number`:**

```sql
-- WAJIB: Tambah kolom role dan phone_number
ALTER TABLE parent_child_mapping ADD COLUMN role TEXT DEFAULT 'parent';
ALTER TABLE parent_child_mapping ADD COLUMN phone_number TEXT;

-- Struktur tabel setelah diupdate:
CREATE TABLE parent_child_mapping (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    parent_chat_id INTEGER NOT NULL,    -- Chat ID Telegram (parent atau teacher)
    child_id TEXT NOT NULL,             -- Reference ke children.child_id
    role TEXT DEFAULT 'parent',         -- 'parent' atau 'teacher'
    phone_number TEXT,                  -- Nomor telepon (opsional)
    is_active BOOLEAN DEFAULT 1,
    registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (child_id) REFERENCES children (child_id),
    UNIQUE(parent_chat_id, child_id)
);
```

### 2. **Environment Variables (.env)** - Tambah ADMIN_CHAT_IDS

```bash
# Bot Configuration
TELEGRAM_BOT_TOKEN=your_telegram_bot_token
SCHOOL_COORDS=-7.250445,112.768845  # Koordinat sekolah (lat,lng)
RADIUS_KM=1.0                       # Radius deteksi dekat sekolah
ARRIVAL_RADIUS_KM=0.1              # Radius deteksi sampai sekolah
WEBHOOK_PORT=5000                   # Port untuk webhook

# Antares IoT Configuration
ANTARES_URL_POST=https://platform.antares.id/~/antares-cse/antares-id/your_app/your_container
ANTARES_ACCESS_KEY=your_access_key

# Database
DATABASE_PATH=child_monitoring.db   # Path database SQLite
```

## Cara Penggunaan

### A. Setup Initial (Administrator/Guru)

1. **Registrasi Anak dengan Parent dan Teacher**

```
/register_child ANAK001 "Budi Santoso" DEV001 123456789 987654321
# Format: child_id nama_anak device_id parent_chat_id teacher_chat_id
```

### B. Penggunaan untuk Orangtua

1. **Mulai Monitoring Lokasi**

```
/start
```

Bot akan aktifkan monitoring lokasi untuk menjemput anak.

2. **Cek Status Monitoring**

```
/status
```

3. **Share Live Location** (HANYA ORANGTUA)

- Tekan ikon attachment (📎)
- Pilih "Location" → "Share Live Location"
- Bot akan kirim sinyal ke IoT ketika dekat sekolah

### C. Penggunaan untuk Guru

1. **Aktivasi Alert Mode**

```
/start
```

Bot akan aktifkan mode penerima alert jatuh.

2. **Menerima Alert Jatuh**

- Alert otomatis dikirim ketika anak terjatuh
- Hanya guru yang menerima alert ini

## 📋 **Command Summary**

### 🤖 **Bot Commands untuk User**

| Command                                             | Role           | Fungsi                                 |
| --------------------------------------------------- | -------------- | -------------------------------------- |
| `/register_as_parent [child_id] [nama] [device_id]` | Parent         | Registrasi diri sebagai orangtua       |
| `/register_as_teacher [child_id]`                   | Teacher        | Registrasi diri sebagai guru           |
| `/start`                                            | Parent/Teacher | Aktivasi monitoring (berbeda per role) |
| `/status`                                           | Parent/Teacher | Cek status monitoring                  |
| `/children`                                         | Parent/Teacher | Lihat daftar anak/murid                |

### 🔧 **Admin Commands**

| Command                                                                            | Role  | Fungsi                                      |
| ---------------------------------------------------------------------------------- | ----- | ------------------------------------------- |
| `/admin_register [child_id] [nama] [device_id] [parent_chat_id] [teacher_chat_id]` | Admin | Registrasi lengkap                          |
| `/register_child`                                                                  | Admin | Legacy command (sama dengan admin_register) |

## Perbedaan Fitur Parent vs Teacher

### 👨‍👩‍👧‍👦 **Orangtua (Parent)**

- ✅ Self-registration dengan `/register_as_parent`
- ✅ Monitoring lokasi (dekat sekolah)
- ✅ Notifikasi "sudah dekat sekolah"
- ✅ Prompt "sudah jemput anak?"
- ✅ Kirim data ke IoT (posisi_ortu_dekat)
- ❌ Tidak menerima alert jatuh

### 🏫 **Guru (Teacher)**

- ✅ Self-registration dengan `/register_as_teacher`
- ✅ Bisa registrasi multiple murid
- ✅ Menerima alert jatuh dari semua murid terdaftar
- ❌ Tidak ada monitoring lokasi
- ❌ Tidak ada prompt jemput anak

## Webhook Endpoints

### Device-Specific Webhooks

```bash
# General webhook
POST /monitor

# Device-specific webhook
POST /monitor/DEV001
POST /monitor/DEV002
```

## Format Data IoT (JSON)

### 1. **Fall Detection** (Kirim ke Guru saja)

```json
{
	"device_id": "DEV001",
	"kondisi": "terjatuh",
	"timestamp": "2024-01-20T10:30:00Z"
}
```

### 2. **Format Antares m2m**

```json
{
	"m2m:cin": {
		"con": "{\"device_id\":\"DEV001\",\"kondisi\":\"terjatuh\"}"
	}
}
```

## Database Structure

### Tabel Children (Tidak berubah)

```sql
CREATE TABLE children (
    child_id TEXT PRIMARY KEY,      -- ANAK001, ANAK002, dll
    child_name TEXT NOT NULL,       -- Nama lengkap anak
    device_id TEXT UNIQUE NOT NULL, -- DEV001, DEV002, dll
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### 🔴 **Tabel Parent-Child Mapping (DIUPDATE - Tambah role)**

```sql
CREATE TABLE parent_child_mapping (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    parent_chat_id INTEGER NOT NULL,    -- Chat ID Telegram
    child_id TEXT NOT NULL,             -- Reference ke children.child_id
    role TEXT DEFAULT 'parent',         -- 'parent' atau 'teacher'
    is_active BOOLEAN DEFAULT 1,
    registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (child_id) REFERENCES children (child_id),
    UNIQUE(parent_chat_id, child_id)
);
```

### Tabel Monitoring Sessions (Tidak berubah)

```sql
CREATE TABLE monitoring_sessions (
    session_id INTEGER PRIMARY KEY AUTOINCREMENT,
    parent_chat_id INTEGER NOT NULL,
    child_id TEXT NOT NULL,
    start_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    end_time TIMESTAMP NULL,
    is_active BOOLEAN DEFAULT 1
);
```

## Contoh Data Setup

### Setup 1 Anak dengan Parent + Teacher

```sql
-- Insert anak
INSERT INTO children VALUES ('ANAK001', 'Ahmad Budi', 'DEV001', '2024-01-20 10:00:00');

-- Insert parent (orangtua)
INSERT INTO parent_child_mapping (parent_chat_id, child_id, role)
VALUES (123456789, 'ANAK001', 'parent');

-- Insert teacher (guru)
INSERT INTO parent_child_mapping (parent_chat_id, child_id, role)
VALUES (987654321, 'ANAK001', 'teacher');
```

## Command Bot Summary

### 🤖 **Bot Commands**

```
/start          - Aktivasi monitoring (berbeda untuk parent/teacher)
/status         - Cek status monitoring
/children       - Lihat daftar anak (optional)
/register_child - Registrasi anak baru (format baru)
```

### 📝 **Format Registrasi Baru**

```
/register_child [child_id] [nama_anak] [device_id] [parent_chat_id] [teacher_chat_id]

Contoh:
/register_child ANAK001 "Ahmad Budi" DEV001 123456789 987654321
```

## Testing Scenarios

### Skenario 1: Normal Flow

```bash
1. Setup: /register_child ANAK001 "Budi" DEV001 123456789 987654321
2. Parent (123456789) sends /start → monitoring lokasi aktif
3. Teacher (987654321) sends /start → alert mode aktif
4. Parent shares live location → dekat sekolah detection
5. Test fall: POST /monitor/DEV001 {"kondisi": "terjatuh"}
   → Hanya teacher (987654321) yang dapat alert
```

### Skenario 2: Multiple Children

```bash
1. Setup multiple children untuk 1 teacher:
   /register_child ANAK001 "Budi" DEV001 111111111 987654321
   /register_child ANAK002 "Sari" DEV002 222222222 987654321

2. Teacher (987654321) gets alerts dari semua anak
3. Each parent (111111111, 222222222) monitor their own child
```

## Deployment

### 1. **Update Database** ⚠️ WAJIB

```sql
-- Jika database sudah ada, tambah kolom role:
ALTER TABLE parent_child_mapping ADD COLUMN role TEXT DEFAULT 'parent';

-- Jika database baru, struktur sudah include role
```

### 2. **Install Dependencies** (Sama)

```bash
pip install python-telegram-bot geopy aiohttp python-dotenv
```

### 3. **Environment Setup** (Tidak berubah)

```bash
cp .env.template .env
# Edit .env dengan token dan konfigurasi sebenarnya
```

### 4. **Run Application**

```bash
python multi_parent_bot.py
```

## API Endpoints (Sama)

### POST /monitor

General webhook untuk menerima data dari semua device IoT.

### POST /monitor/{device_id}

Device-specific webhook. Device ID akan diambil dari URL path.

### GET /health

Health check endpoint.

## ⚠️ **PENTING: Database Migration**

**Jika Anda sudah punya database lama:**

```sql
-- Jalankan SQL ini untuk update database:
ALTER TABLE parent_child_mapping ADD COLUMN role TEXT DEFAULT 'parent';

-- Update existing data jika perlu:
UPDATE parent_child_mapping SET role = 'teacher' WHERE parent_chat_id = [teacher_chat_id];
```

**Jika database baru:**

- Database akan otomatis dibuat dengan struktur yang benar saat pertama kali menjalankan bot.
