import os
import json
import asyncio
from datetime import datetime
from typing import Optional, Dict, Any, List
from dotenv import load_dotenv
from telegram import Update, KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, ConversationHandler
from geopy.distance import geodesic
from aiohttp import web
import logging
import aiohttp
import sqlite3
from dataclasses import dataclass

# Setup logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Configuration
load_dotenv()

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
SCHOOL_COORDS = tuple(map(float, os.getenv("SCHOOL_COORDS", "0,0").split(",")))
RADIUS_KM = float(os.getenv("RADIUS_KM", "1.0"))
ARRIVAL_RADIUS_KM = float(os.getenv("ARRIVAL_RADIUS_KM", "0.1"))
WEBHOOK_PORT = int(os.getenv("WEBHOOK_PORT", "5000"))
ANTARES_URL_POST = os.getenv("ANTARES_URL_POST")
ANTARES_ACCESS_KEY = os.getenv("ANTARES_ACCESS_KEY")
DATABASE_PATH = os.getenv("DATABASE_PATH", "child_monitoring.db")
ADMIN_CHAT_IDS = list(map(int, os.getenv("ADMIN_CHAT_IDS", "").split(",")) if os.getenv("ADMIN_CHAT_IDS") else [])

@dataclass
class ChildData:
    child_id: str
    child_name: str
    parent_chat_id: int
    device_id: str  # ID unik untuk device IoT
    is_active: bool = True

@dataclass
class ParentMonitoringData:
    chat_id: int
    child_id: str
    user_near_school: bool = False
    user_arrived: bool = False
    monitoring_active: bool = False
    start_time: Optional[datetime] = None

class DatabaseManager:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.init_database()
    
    def init_database(self):
        """Initialize database tables"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            # Tabel untuk data anak
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS children (
                    child_id TEXT PRIMARY KEY,
                    child_name TEXT NOT NULL,
                    device_id TEXT UNIQUE NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Tabel untuk mapping parent-child
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS parent_child_mapping (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    parent_chat_id INTEGER NOT NULL,
                    child_id TEXT NOT NULL,
                    role TEXT DEFAULT 'parent',
                    phone_number TEXT,
                    is_active BOOLEAN DEFAULT 1,
                    registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (child_id) REFERENCES children (child_id),
                    UNIQUE(parent_chat_id, child_id)
                )
            ''')
            
            # Tabel untuk monitoring session
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS monitoring_sessions (
                    session_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    parent_chat_id INTEGER NOT NULL,
                    child_id TEXT NOT NULL,
                    start_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    end_time TIMESTAMP NULL,
                    is_active BOOLEAN DEFAULT 1,
                    FOREIGN KEY (child_id) REFERENCES children (child_id)
                )
            ''')
            
            # TAMBAHAN: Tabel untuk mapping kode user ke chat_id
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS user_code_mapping (
                    user_code TEXT PRIMARY KEY,
                    chat_id INTEGER UNIQUE NOT NULL,
                    role TEXT NOT NULL,
                    registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            conn.commit()
            logger.info("Database initialized successfully")
    
    def register_child(self, child_id: str, child_name: str, device_id: str) -> bool:
        """Register a new child"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "INSERT INTO children (child_id, child_name, device_id) VALUES (?, ?, ?)",
                    (child_id, child_name, device_id)
                )
                conn.commit()
                logger.info(f"Child registered: {child_name} ({child_id})")
                return True
        except sqlite3.IntegrityError as e:
            logger.error(f"Failed to register child: {e}")
            return False
    
    def register_parent_child(self, parent_chat_id: int, child_id: str, role: str = 'parent', phone_number: str = None) -> bool:
        """Register parent-child mapping"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "INSERT INTO parent_child_mapping (parent_chat_id, child_id, role, phone_number) VALUES (?, ?, ?, ?)",
                    (parent_chat_id, child_id, role, phone_number)
                )
                conn.commit()
                logger.info(f"Parent {parent_chat_id} registered for child {child_id} as {role}")
                return True
        except sqlite3.IntegrityError as e:
            logger.error(f"Failed to register parent-child mapping: {e}")
            return False
    
    def register_user_code(self, user_code: str, chat_id: int, role: str) -> bool:
        """Register mapping antara kode user dan chat_id"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "INSERT OR REPLACE INTO user_code_mapping (user_code, chat_id, role) VALUES (?, ?, ?)",
                    (user_code, chat_id, role)
                )
                conn.commit()
                logger.info(f"User code {user_code} mapped to chat_id {chat_id} as {role}")
                return True
        except Exception as e:
            logger.error(f"Failed to register user code mapping: {e}")
            return False
    
    def get_chat_id_by_code(self, user_code: str) -> Optional[int]:
        """Dapatkan chat_id dari kode user"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT chat_id FROM user_code_mapping WHERE user_code = ?", (user_code,))
            result = cursor.fetchone()
            return result[0] if result else None
    
    def get_children_by_parent_and_role(self, parent_chat_id: int, role: str) -> List[ChildData]:
        """Get all children for a parent with specific role"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT c.child_id, c.child_name, pcm.parent_chat_id, c.device_id, pcm.is_active
                FROM children c
                JOIN parent_child_mapping pcm ON c.child_id = pcm.child_id
                WHERE pcm.parent_chat_id = ? AND pcm.role = ? AND pcm.is_active = 1
            ''', (parent_chat_id, role))
            
            results = cursor.fetchall()
            return [ChildData(
                child_id=row[0],
                child_name=row[1], 
                parent_chat_id=row[2],
                device_id=row[3],
                is_active=bool(row[4])
            ) for row in results]
    
    def get_parents_by_child_and_role(self, child_id: str, role: str) -> List[int]:
        """Get all parent chat IDs for a specific child with specific role"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT parent_chat_id 
                FROM parent_child_mapping 
                WHERE child_id = ? AND role = ? AND is_active = 1
            ''', (child_id, role))
            
            results = cursor.fetchall()
            return [row[0] for row in results]
    
    def get_user_role(self, chat_id: int) -> str:
        """Get user role based on chat_id"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT role FROM parent_child_mapping 
                WHERE parent_chat_id = ? AND is_active = 1
                LIMIT 1
            ''', (chat_id,))
            
            result = cursor.fetchone()
            return result[0] if result else 'unknown'
    
    def is_admin(self, chat_id: int) -> bool:
        """Check if user is admin"""
        return chat_id in ADMIN_CHAT_IDS
    
    def get_child_by_device_id(self, device_id: str) -> Optional[ChildData]:
        """Get child data by device ID"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT child_id, child_name, device_id
                FROM children 
                WHERE device_id = ?
            ''', (device_id,))
            
            result = cursor.fetchone()
            if result:
                return ChildData(
                    child_id=result[0],
                    child_name=result[1],
                    parent_chat_id=0,
                    device_id=result[2]
                )
            return None
    
    def start_monitoring_session(self, parent_chat_id: int, child_id: str) -> bool:
        """Start a monitoring session"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "INSERT INTO monitoring_sessions (parent_chat_id, child_id) VALUES (?, ?)",
                    (parent_chat_id, child_id)
                )
                conn.commit()
                return True
        except Exception as e:
            logger.error(f"Failed to start monitoring session: {e}")
            return False
    
    def end_monitoring_session(self, parent_chat_id: int, child_id: str) -> bool:
        """End a monitoring session"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    UPDATE monitoring_sessions 
                    SET end_time = CURRENT_TIMESTAMP, is_active = 0
                    WHERE parent_chat_id = ? AND child_id = ? AND is_active = 1
                ''', (parent_chat_id, child_id))
                conn.commit()
                return True
        except Exception as e:
            logger.error(f"Failed to end monitoring session: {e}")
            return False


# --- Mapping kode ↔ nama (bisa diubah sesuai kebutuhan) ---
GURU_CODES = {
    "teacher_1a": "Pak Andi", "teacher_1b": "Bu Siti",
    "teacher_2a": "Pak Budi", "teacher_2b": "Bu Rina",
    "teacher_3a": "Pak Cipto", "teacher_3b": "Bu Dewi",
    "teacher_4a": "Pak Dedi", "teacher_4b": "Bu Lilis",
    "teacher_5a": "Pak Eko", "teacher_5b": "Bu Sari",
    "teacher_6a": "Pak Fajar", "teacher_6b": "Bu Tini"
}
ORTU_CODES = {
    f"user{j}": f"Orang Tua {j}" for j in range(1, 16)
}
NINO_CODES = {
    f"nino_{str(k).zfill(3)}": f"Anak {k}" for k in range(1, 16)
}

# Helper untuk dapatkan nama dari kode
def get_guru_name(kode):
    return GURU_CODES.get(kode, kode)
def get_ortu_name(kode):
    return ORTU_CODES.get(kode, kode)
def get_nino_name(kode):
    return NINO_CODES.get(kode, kode)

db_manager = DatabaseManager(DATABASE_PATH)
MONITORING_DATA: Dict[str, ParentMonitoringData] = {}  # key: f"{parent_chat_id}_{child_id}"

def get_monitoring_key(parent_chat_id: int, child_id: str) -> str:
    """Generate unique key for monitoring data"""
    return f"{parent_chat_id}_{child_id}"

# --- Command /register_user khusus admin ---
async def register_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    
    # Cek admin
    if not db_manager.is_admin(chat_id):
        user_role = db_manager.get_user_role(chat_id)
        if user_role == 'teacher':
            await update.message.reply_text(
                "❌ Command ini hanya untuk admin.\n\n"
                "Sebagai guru, silakan gunakan /register_guru untuk registrasi."
            )
        elif user_role == 'parent':
            await update.message.reply_text(
                "❌ Command ini hanya untuk admin.\n\n"
                "Sebagai orang tua, silakan gunakan /register_ortu untuk registrasi."
            )
        else:
            await update.message.reply_text(
                "❌ Command ini hanya untuk admin.\n\n"
                "Jika Anda guru, gunakan /register_guru. Jika Anda orang tua, gunakan /register_ortu."
            )
        return
    
    if len(context.args) != 6:
        await update.message.reply_text(
            'Format: /register_user nino_id "nama_anak" teacher_id "nama_guru" user_id "nama_ortu"\n\n'
            'Contoh: /register_user nino_001 "Budi Santoso" teacher_1a "Pak Andi" user1 "Bu Sari"'
        )
        return
    
    nino_id, nama_anak, teacher_id, nama_guru, user_id, nama_ortu = context.args
    
    # Hilangkan underscore pada nama
    nama_anak_bersih = nama_anak.replace('_', ' ')
    nama_guru_bersih = nama_guru.replace('_', ' ')
    nama_ortu_bersih = nama_ortu.replace('_', ' ')
    
    # Validasi kode
    if nino_id not in NINO_CODES:
        await update.message.reply_text(f"❌ Kode alat tidak valid: {nino_id}")
        return
    if teacher_id not in GURU_CODES:
        await update.message.reply_text(f"❌ Kode guru tidak valid: {teacher_id}")
        return
    if user_id not in ORTU_CODES:
        await update.message.reply_text(f"❌ Kode ortu tidak valid: {user_id}")
        return
    
    # Register anak
    child_ok = db_manager.register_child(nino_id, nama_anak_bersih, nino_id)
    
    # Register guru - gunakan KODE sebagai parent_chat_id (untuk kompatibilitas)
    # Nantinya akan di-resolve ke real chat_id saat user menjalankan /start
    guru_ok = db_manager.register_parent_child(teacher_id, nino_id, 'teacher', nama_guru_bersih)
    
    # Register ortu - gunakan KODE sebagai parent_chat_id
    ortu_ok = db_manager.register_parent_child(user_id, nino_id, 'parent', nama_ortu_bersih)
    
    status = []
    if child_ok:
        status.append("✅ Anak terdaftar")
    else:
        status.append("⚠️ Anak sudah ada (update data)")
    
    if guru_ok:
        status.append("✅ Guru di-assign")
    else:
        status.append("⚠️ Guru sudah di-assign")
    
    if ortu_ok:
        status.append("✅ Ortu di-assign")
    else:
        status.append("⚠️ Ortu sudah di-assign")
    
    # PERBAIKAN: Indentasi diperbaiki dan backslash di-handle dengan benar
    status_text = "\n".join(status)  # Buat variable terpisah, jangan langsung di f-string
    
    await update.message.reply_text(
        f"📋 **Hasil Registrasi:**\n\n"
        f"👶 Anak: {nama_anak_bersih} ({nino_id})\n"
        f"🏫 Guru: {nama_guru_bersih} ({teacher_id})\n"
        f"👨‍👩‍👧‍👦 Ortu: {nama_ortu_bersih} ({user_id})\n\n"
        f"{status_text}\n\n"
        f"ℹ️ Guru dan orang tua perlu menjalankan /start untuk aktivasi.",
        parse_mode='Markdown'
    )

# --- Command /register_guru dan /register_ortu untuk registrasi mandiri ---
# --- Conversation states - PISAHKAN UNTUK SETIAP HANDLER ---
GURU_CHOOSE = 100
ORTU_CHOOSE = 200

# --- Mapping kode ↔ nama (bisa diubah sesuai kebutuhan) ---
GURU_CODES = {
    "teacher_1a": "Latifatuz Zuhriyah, S.Pd (1A)",
    "teacher_2a": "Ella Firliani, S.Ag (2A)",
    "teacher_3a": "Ayunda Umirotus Sakinah, S.Pd (3A)",
    "teacher_4a": "Ana Maulidia Ningrum, S.Pd (4A)",
    "teacher_5a": "Arif Nur Hidayat, S.Pd (5A)",
    "teacher_6a": "Nia Mufida, S.E (6A)"
}

ORTU_CODES = {
    "user1": "Iin Indarti",
    "user2": "Yeni Isfatul Achmad",
    "user3": "Lu'luil Maknun",
    "user4": "Fitri Wahyuningsih",
    "user5": "Siti Maisaroh",
    "user6": "Thoriqul Mukhoffifah",
    "user7": "Vela Sindy Oktavianti"
}

NINO_CODES = {
    f"nino_{str(k).zfill(3)}": f"Anak {k}" for k in range(1, 16)
}

# Helper untuk dapatkan nama dari kode
def get_guru_name(kode):
    return GURU_CODES.get(kode, kode)
def get_ortu_name(kode):
    return ORTU_CODES.get(kode, kode)
def get_nino_name(kode):
    return NINO_CODES.get(kode, kode)

# Inisialisasi database manager
db_manager = DatabaseManager(DATABASE_PATH)
MONITORING_DATA: Dict[str, ParentMonitoringData] = {}

async def register_guru(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Entry point untuk registrasi guru - Step 1: Pilih kode"""
    chat_id = update.message.chat_id
    
    # Cek apakah sudah terdaftar
    with sqlite3.connect(DATABASE_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT user_code FROM user_code_mapping WHERE chat_id = ? AND role = 'teacher'", (chat_id,))
        existing = cursor.fetchone()
    
    if existing:
        await update.message.reply_text(
            f"ℹ️ Anda sudah terdaftar sebagai guru dengan kode: **{existing[0]}**\n\n"
            f"Nama: {GURU_CODES.get(existing[0], 'Unknown')}\n\n"
            f"Ketik /start untuk aktivasi atau /reset_guru untuk reset.",
            parse_mode='Markdown'
        )
        return ConversationHandler.END
    
    # Tampilkan list kode guru
    text = "🏫 **REGISTRASI GURU**\n\n"
    text += "Pilih nama Anda (balas dengan angka):\n\n"
    
    for idx, (kode, nama) in enumerate(GURU_CODES.items(), 1):
        text += f"{idx}. {nama}\n"
    
    text += "\n💡 *Balas dengan angka 1-6*"
    
    await update.message.reply_text(text, parse_mode='Markdown')
    
    # Simpan list kode ke context
    context.user_data['guru_kode_list'] = list(GURU_CODES.keys())
    
    return GURU_CHOOSE

async def guru_choose(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler untuk pilihan kode guru - Step 2: Proses pilihan"""
    try:
        idx = int(update.message.text.strip()) - 1
        kode_list = context.user_data.get('guru_kode_list', [])
        
        if idx < 0 or idx >= len(kode_list):
            raise IndexError
        
        kode = kode_list[idx]
    except (ValueError, IndexError):
        await update.message.reply_text(
            "❌ Input tidak valid. Balas dengan angka 1-6.\n\n"
            "Atau ketik /register_guru untuk mulai lagi."
        )
        return GURU_CHOOSE
    
    chat_id = update.message.chat_id
    
    # Cek apakah kode sudah digunakan guru lain
    with sqlite3.connect(DATABASE_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT chat_id FROM user_code_mapping WHERE user_code = ? AND role = 'teacher'", (kode,))
        existing = cursor.fetchone()
    
    if existing and existing[0] != chat_id:
        await update.message.reply_text(
            f"❌ Kode **{kode}** sudah digunakan oleh guru lain.\n\n"
            f"Silakan pilih kode yang berbeda atau hubungi admin.",
            parse_mode='Markdown'
        )
        return ConversationHandler.END
    
    # SIMPAN MAPPING KODE → CHAT_ID
    if not db_manager.register_user_code(kode, chat_id, 'teacher'):
        await update.message.reply_text(
            "❌ Terjadi error saat menyimpan data.\n"
            "Silakan coba lagi atau hubungi admin."
        )
        return ConversationHandler.END
    
    await update.message.reply_text(
        f"✅ **Registrasi Berhasil!**\n\n"
        f"👨‍🏫 Nama: **{GURU_CODES[kode]}**\n"
        f"🔑 Kode: `{kode}`\n"
        f"💬 Chat ID: `{chat_id}`\n\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📝 **Langkah Selanjutnya:**\n\n"
        f"1️⃣ Tunggu admin meng-assign murid ke Anda\n"
        f"2️⃣ Setelah di-assign, ketik /start untuk aktivasi\n"
        f"3️⃣ Anda akan menerima alert jika murid terjatuh\n\n"
        f"💡 Format untuk admin:\n"
        f"`/register_user [nino_id] [nama_anak] {kode} [nama_guru] [user_id] [nama_ortu]`",
        parse_mode='Markdown'
    )
    
    # Cek apakah sudah ada murid yang di-assign (dari registrasi sebelumnya oleh admin)
    try:
        with sqlite3.connect(DATABASE_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT c.child_id, c.child_name 
                FROM parent_child_mapping pcm 
                JOIN children c ON pcm.child_id = c.child_id 
                WHERE pcm.parent_chat_id = ? AND pcm.role = 'teacher'
            """, (kode,))
            murid_list = cursor.fetchall()
        
        if murid_list:
            msg = "\n📚 **Murid yang sudah di-assign:**\n\n"
            for cid, nama in murid_list:
                msg += f"  • {nama} ({cid})\n"
            msg += f"\n✨ Ketik /start untuk aktivasi alert!"
            await update.message.reply_text(msg, parse_mode='Markdown')
    except Exception as e:
        logger.error(f"Error checking assigned children: {e}")
    
    return ConversationHandler.END

async def register_ortu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Entry point untuk registrasi orang tua - Step 1: Pilih kode"""
    chat_id = update.message.chat_id
    
    # Cek apakah sudah terdaftar
    with sqlite3.connect(DATABASE_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT user_code FROM user_code_mapping WHERE chat_id = ? AND role = 'parent'", (chat_id,))
        existing = cursor.fetchone()
    
    if existing:
        await update.message.reply_text(
            f"ℹ️ Anda sudah terdaftar sebagai orang tua dengan kode: **{existing[0]}**\n\n"
            f"Nama: {ORTU_CODES.get(existing[0], 'Unknown')}\n\n"
            f"Ketik /start untuk aktivasi atau /reset_ortu untuk reset.",
            parse_mode='Markdown'
        )
        return ConversationHandler.END
    
    # Tampilkan list kode orang tua
    text = "👨‍👩‍👧‍👦 **REGISTRASI ORANG TUA**\n\n"
    text += "Pilih nama Anda (balas dengan angka):\n\n"
    
    for idx, (kode, nama) in enumerate(ORTU_CODES.items(), 1):
        text += f"{idx}. {nama}\n"
    
    text += "\n💡 *Balas dengan angka 1-7*"
    
    await update.message.reply_text(text, parse_mode='Markdown')
    
    # Simpan list kode ke context
    context.user_data['ortu_kode_list'] = list(ORTU_CODES.keys())
    
    return ORTU_CHOOSE

async def ortu_choose(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler untuk pilihan kode orang tua - Step 2: Proses pilihan"""
    try:
        idx = int(update.message.text.strip()) - 1
        kode_list = context.user_data.get('ortu_kode_list', [])
        
        if idx < 0 or idx >= len(kode_list):
            raise IndexError
        
        kode = kode_list[idx]
    except (ValueError, IndexError):
        await update.message.reply_text(
            "❌ Input tidak valid. Balas dengan angka 1-7.\n\n"
            "Atau ketik /register_ortu untuk mulai lagi."
        )
        return ORTU_CHOOSE
    
    chat_id = update.message.chat_id
    
    # Cek apakah kode sudah digunakan orang tua lain
    with sqlite3.connect(DATABASE_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT chat_id FROM user_code_mapping WHERE user_code = ? AND role = 'parent'", (kode,))
        existing = cursor.fetchone()
    
    if existing and existing[0] != chat_id:
        await update.message.reply_text(
            f"❌ Kode **{kode}** sudah digunakan oleh orang tua lain.\n\n"
            f"Silakan pilih kode yang berbeda atau hubungi admin.",
            parse_mode='Markdown'
        )
        return ConversationHandler.END
    
    # SIMPAN MAPPING KODE → CHAT_ID
    if not db_manager.register_user_code(kode, chat_id, 'parent'):
        await update.message.reply_text(
            "❌ Terjadi error saat menyimpan data.\n"
            "Silakan coba lagi atau hubungi admin."
        )
        return ConversationHandler.END
    
    await update.message.reply_text(
        f"✅ **Registrasi Berhasil!**\n\n"
        f"👨‍👩‍👧‍👦 Nama: **{ORTU_CODES[kode]}**\n"
        f"🔑 Kode: `{kode}`\n"
        f"💬 Chat ID: `{chat_id}`\n\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📝 **Langkah Selanjutnya:**\n\n"
        f"1️⃣ Tunggu admin meng-assign anak ke Anda\n"
        f"2️⃣ Setelah di-assign, ketik /start untuk aktivasi\n"
        f"3️⃣ Share lokasi saat menjemput anak\n\n"
        f"💡 Format untuk admin:\n"
        f"`/register_user [nino_id] [nama_anak] [teacher_id] [nama_guru] {kode} [nama_ortu]`",
        parse_mode='Markdown'
    )
    
    # Cek apakah sudah ada anak yang di-assign (dari registrasi sebelumnya oleh admin)
    try:
        with sqlite3.connect(DATABASE_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT c.child_id, c.child_name 
                FROM parent_child_mapping pcm 
                JOIN children c ON pcm.child_id = c.child_id 
                WHERE pcm.parent_chat_id = ? AND pcm.role = 'parent'
            """, (kode,))
            anak_list = cursor.fetchall()
        
        if anak_list:
            msg = "\n👶 **Anak yang sudah di-assign:**\n\n"
            for cid, nama in anak_list:
                msg += f"  • {nama} ({cid})\n"
            msg += f"\n✨ Ketik /start untuk aktivasi monitoring!"
            await update.message.reply_text(msg, parse_mode='Markdown')
    except Exception as e:
        logger.error(f"Error checking assigned children: {e}")
    
    return ConversationHandler.END

class TelegramMessageSender:
    def __init__(self, bot_app: Application):
        self.bot = bot_app.bot
    
    async def send_fall_alert(self, chat_ids: List[int], child_name: str):
        """Send fall alert to multiple parents"""
        timestamp = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
        
        message = (
            "🚨 **ALERT DARURAT** 🚨\n\n"
            f"⚠️ {child_name.upper()} TERJATUH!\n"
            f"🕐 Waktu: {timestamp}\n"
            "📍 Segera cek lokasi anak dan hubungi sekolah!\n\n"
            "🚨 Mohon segera ambil tindakan!"
        )
        
        for chat_id in chat_ids:
            try:
                await self.bot.send_message(chat_id=chat_id, text=message, parse_mode='Markdown')
                logger.info(f"Fall alert sent to {chat_id} for {child_name} at {timestamp}")
            except Exception as e:
                logger.error(f"Failed to send fall alert to {chat_id}: {e}")
    
    async def send_location_near_school(self, chat_id: int, child_name: str, distance: float):
        message = f"✅ Anda sudah berada dekat dengan sekolah untuk menjemput {child_name}\n📏 Jarak: {distance:.2f} km"
        
        try:
            await self.bot.send_message(chat_id=chat_id, text=message)
            logger.info(f"Near school notification sent to {chat_id} for {child_name}")
        except Exception as e:
            logger.error(f"Failed to send location update to {chat_id}: {e}")
    
    async def send_pickup_prompt(self, chat_id: int, child_name: str):
        keyboard = [["Ya", "Tidak"]]
        reply_markup = ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True)
        
        try:
            await self.bot.send_message(
                chat_id=chat_id, 
                text=f"🚸 Apakah Anda sudah menjemput {child_name}?", 
                reply_markup=reply_markup
            )
            logger.info(f"Pickup prompt sent to {chat_id} for {child_name}")
        except Exception as e:
            logger.error(f"Failed to send pickup prompt to {chat_id}: {e}")
    
    async def send_monitoring_stopped(self, chat_id: int, child_name: str):
        message = (
            f"🔕 Monitoring untuk {child_name} dihentikan.\n\n"
            "🛡️ Hati-hati di jalan dan semoga sampai tujuan dengan selamat!\n\n"
            "💡 Jangan lupa untuk mengetik /start lagi di esok hari agar "
            "bot ChildMonitoring berjalan kembali."
        )
        
        try:
            await self.bot.send_message(
                chat_id=chat_id, 
                text=message, 
                reply_markup=ReplyKeyboardRemove()
            )
            logger.info(f"Monitoring stopped message sent to {chat_id} for {child_name}")
        except Exception as e:
            logger.error(f"Failed to send monitoring stopped message to {chat_id}: {e}")
    
    async def send_monitoring_continued(self, chat_id: int, child_name: str):
        keyboard = [["Ya", "Tidak"]]
        reply_markup = ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True)
        
        try:
            await self.bot.send_message(
                chat_id=chat_id,
                text=f"🔔 Monitoring untuk {child_name} dilanjutkan!\n\n🚸 Apakah Anda sudah menjemput {child_name}?",
                reply_markup=reply_markup
            )
            logger.info(f"Monitoring continued message sent to {chat_id} for {child_name}")
        except Exception as e:
            logger.error(f"Failed to send monitoring continued message to {chat_id}: {e}")
    
    async def send_to_antares(self, device_id: str):
        """Send data to Antares for specific device"""
        url = f"{ANTARES_URL_POST}/{device_id}" 
        
        payload = {
            "m2m:cin": {
                "xmlns:m2m": "http://www.onem2m.org/xml/protocols",  
                "cnf": "application/json",
                "con": "{\"posisi_ortu_dekat\":\"ya\"}"
            }
        }
        
        headers = {
            "X-M2M-Origin": ANTARES_ACCESS_KEY,
            "Content-Type": "application/json;ty=4",
            "Accept": "application/json"
        }

        logger.info(f"Sending to URL: {url} for device {device_id}")
        logger.info(f"Payload: {json.dumps(payload, indent=2)}")
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload, headers=headers) as response:
                    if response.status == 201:
                        logger.info(f"Data berhasil dikirim ke Antares untuk device {device_id}")
                    else:
                        logger.error(f"Gagal kirim ke Antares untuk device {device_id}: {response.status}")
        except Exception as e:
            logger.error(f"Error kirim ke Antares untuk device {device_id}: {e}")

# Command handlers
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start command - resolve user code to chat_id and activate monitoring"""
    chat_id = update.message.chat_id
    
    # PESAN WELCOME/PENDAHULUAN
    welcome_message = (
        "🤖 **Selamat datang di Nino - Child Monitoring Bot!**\n\n"
        "Silahkan jalankan command berikut untuk register:\n\n"
        "👉 /register_guru untuk guru\n" 
        "👉 /register_ortu untuk orang tua\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
    )
    
    # Cek apakah user sudah punya mapping kode
    user_code = None
    user_role_from_mapping = None
    
    with sqlite3.connect(DATABASE_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT user_code, role FROM user_code_mapping WHERE chat_id = ?", (chat_id,))
        result = cursor.fetchone()
        
        if result:
            user_code, user_role_from_mapping = result
            
            # UPDATE parent_chat_id dari kode ke real chat_id (RESOLVE)
            cursor.execute("""
                UPDATE parent_child_mapping 
                SET parent_chat_id = ? 
                WHERE parent_chat_id = ? AND role = ?
            """, (chat_id, user_code, user_role_from_mapping))
            conn.commit()
            
            logger.info(f"✅ Resolved {user_code} → chat_id {chat_id} for role {user_role_from_mapping}")
    
    # Dapatkan role user dari database
    user_role = db_manager.get_user_role(chat_id)
    
    # Cek status registrasi dan assignment
    if user_role == 'parent':
        children = db_manager.get_children_by_parent_and_role(chat_id, 'parent')
        
        if not children:
            # Sudah register tapi belum di-assign
            status_message = (
                f"📊 **Status saat ini:**\n"
                f"✅ Terdaftar sebagai: **Orang Tua** ({user_code if user_code else 'Unknown'})\n"
                f"❌ Belum di-assign ke anak manapun\n\n"
                f"📞 Hubungi admin untuk assignment anak."
            )
            await update.message.reply_text(welcome_message + status_message, parse_mode='Markdown')
            return
        
        # Sudah register dan sudah di-assign - AKTIVASI MONITORING
        for child in children:
            monitoring_key = get_monitoring_key(chat_id, child.child_id)
            MONITORING_DATA[monitoring_key] = ParentMonitoringData(
                chat_id=chat_id,
                child_id=child.child_id,
                monitoring_active=True,
                start_time=datetime.now()
            )
            db_manager.start_monitoring_session(chat_id, child.child_id)
        
        child_names = ", ".join([child.child_name for child in children])
        
        status_message = (
            f"📊 **Status saat ini:**\n"
            f"✅ Terdaftar sebagai: **{ORTU_CODES.get(user_code, 'Orang Tua')}**\n"
            f"✅ Monitoring aktif untuk: **{child_names}**\n\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📍 **Untuk menjemput anak:**\n"
            f"1. Bagikan Live Location saat menuju sekolah\n"
            f"2. Bot akan memberi tahu ketika Anda dekat sekolah\n"
            f"3. Bot akan tanya apakah anak sudah dijemput\n\n"
            f"📋 Ketik /status untuk cek status monitoring"
        )
        
        await update.message.reply_text(welcome_message + status_message, parse_mode='Markdown')
        logger.info(f"✅ Parent monitoring started for {chat_id} ({user_code}) with {len(children)} children")
        
    elif user_role == 'teacher':
        children = db_manager.get_children_by_parent_and_role(chat_id, 'teacher')
        
        if not children:
            # Sudah register tapi belum di-assign
            status_message = (
                f"📊 **Status saat ini:**\n"
                f"✅ Terdaftar sebagai: **Guru** ({user_code if user_code else 'Unknown'})\n"
                f"❌ Belum di-assign ke murid manapun\n\n"
                f"📞 Hubungi admin untuk assignment murid."
            )
            await update.message.reply_text(welcome_message + status_message, parse_mode='Markdown')
            return
        
        # Sudah register dan sudah di-assign - AKTIVASI MODE ALERT
        child_names = ", ".join([child.child_name for child in children])
        
        status_message = (
            f"📊 **Status saat ini:**\n"
            f"✅ Terdaftar sebagai: **{GURU_CODES.get(user_code, 'Guru')}**\n"
            f"✅ Mode alert aktif untuk: **{child_names}**\n\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
            f"🚨 Anda akan menerima alert jika ada murid yang terjatuh\n"
            f"📱 Alert akan dikirim otomatis dari sistem IoT\n\n"
            f"ℹ️ Mode guru hanya menerima alert, tidak ada fitur lain.\n"
            f"📋 Ketik /status untuk cek murid yang di-assign"
        )
        
        await update.message.reply_text(welcome_message + status_message, parse_mode='Markdown')
        logger.info(f"✅ Teacher alert mode activated for {chat_id} ({user_code}) with {len(children)} children")
    
    else:
        # Belum terdaftar sama sekali
        status_message = (
            f"📊 **Status saat ini:**\n"
            f"❌ Anda tidak terdaftar sebagai guru atau orang tua\n\n"
            f"💡 Silakan pilih peran Anda dan lakukan registrasi terlebih dahulu."
        )
        await update.message.reply_text(welcome_message + status_message, parse_mode='Markdown')

async def register_as_teacher(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Register current user as teacher for a child - used by teachers"""
    chat_id = update.message.chat_id
    
    if len(context.args) != 1:
        await update.message.reply_text(
            "Format: `/register_as_teacher [child_id]`\n\n"
            "Contoh: `/register_as_teacher ANAK001`\n\n"
            "ℹ️ Command ini untuk guru mendaftarkan diri untuk murid mereka.\n"
            "⚠️ Pastikan child_id sudah terdaftar oleh orangtua terlebih dahulu."
        )
        return
    
    child_id = context.args[0]
    phone = update.message.from_user.phone_number if update.message.from_user else None
    
    # Check if child exists
    child_data = db_manager.get_child_by_device_id(f"DEV{child_id[-3:]}")  # Assuming device_id pattern
    if not child_data:
        # Let's check by child_id directly
        with sqlite3.connect(DATABASE_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT child_name FROM children WHERE child_id = ?", (child_id,))
            result = cursor.fetchone()
            
            if not result:
                await update.message.reply_text(
                    f"❌ Child ID {child_id} tidak ditemukan.\n"
                    "Pastikan orangtua sudah registrasi terlebih dahulu."
                )
                return
    
    # Register as teacher
    if db_manager.register_parent_child(chat_id, child_id, 'teacher', phone):
        with sqlite3.connect(DATABASE_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT child_name FROM children WHERE child_id = ?", (child_id,))
            child_name = cursor.fetchone()[0]
        
        await update.message.reply_text(
            f"✅ **Registrasi Guru Berhasil!**\n\n"
            f"👶 Murid: {child_name} ({child_id})\n"
            f"🏫 Role: Guru\n\n"
            f"🚨 Anda akan menerima alert jika {child_name} terjatuh.\n"
            f"🚀 Ketik /start untuk aktivasi mode guru!"
        )
        logger.info(f"Teacher {chat_id} registered for child {child_id}")
    else:
        await update.message.reply_text("❌ Registrasi gagal. Mungkin Anda sudah terdaftar untuk murid ini.")

async def admin_register_child(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin command to register child with parent and teacher - admin only"""
    chat_id = update.message.chat_id
    
    if not db_manager.is_admin(chat_id):
        await update.message.reply_text("❌ Command ini hanya untuk admin.")
        return
    
    if len(context.args) != 5:
        await update.message.reply_text(
            "Format Admin: `/admin_register [child_id] [nama_anak] [device_id] [parent_chat_id] [teacher_chat_id]`\n\n"
            "Contoh: `/admin_register ANAK001 \"Budi\" DEV001 123456789 987654321`\n\n"
            "ℹ️ Command ini untuk admin registrasi lengkap anak + parent + teacher."
        )
        return
    
    child_id, child_name, device_id, parent_chat_id, teacher_chat_id = context.args
    
    try:
        parent_chat_id = int(parent_chat_id)
        teacher_chat_id = int(teacher_chat_id)
    except ValueError:
        await update.message.reply_text("❌ Chat ID harus berupa angka.")
        return
    
    # Register child
    if db_manager.register_child(child_id, child_name, device_id):
        # Register parent
        parent_success = db_manager.register_parent_child(parent_chat_id, child_id, 'parent')
        # Register teacher  
        teacher_success = db_manager.register_parent_child(teacher_chat_id, child_id, 'teacher')
        
        status_msg = f"✅ **Admin Registrasi:**\n\n"
        status_msg += f"👶 Anak: {child_name} ({child_id})\n"
        status_msg += f"📱 Device: {device_id}\n"
        status_msg += f"👨‍👩‍👧‍👦 Parent: {'✅' if parent_success else '❌'} {parent_chat_id}\n"
        status_msg += f"🏫 Teacher: {'✅' if teacher_success else '❌'} {teacher_chat_id}\n\n"
        
        if parent_success and teacher_success:
            status_msg += "🎉 Semua registrasi berhasil!"
        else:
            status_msg += "⚠️ Beberapa registrasi gagal (mungkin sudah ada)."
        
        await update.message.reply_text(status_msg)
    else:
        await update.message.reply_text("❌ Gagal registrasi anak. Mungkin child_id sudah digunakan.")

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show monitoring status"""
    chat_id = update.message.chat_id
    user_role = db_manager.get_user_role(chat_id)
    
    if user_role == 'parent':
        children = db_manager.get_children_by_parent_and_role(chat_id, 'parent')
        
        if not children:
            await update.message.reply_text("❌ Tidak ada anak yang terdaftar.")
            return
        
        status_messages = []
        
        for child in children:
            monitoring_key = get_monitoring_key(chat_id, child.child_id)
            monitoring_data = MONITORING_DATA.get(monitoring_key)
            
            if monitoring_data and monitoring_data.monitoring_active:
                start_time = monitoring_data.start_time.strftime("%H:%M:%S") if monitoring_data.start_time else "N/A"
                near_school = "✅ Ya" if monitoring_data.user_near_school else "❌ Tidak"
                arrived = "✅ Ya" if monitoring_data.user_arrived else "❌ Tidak"
                status = "🟢 Aktif"
            else:
                start_time = "N/A"
                near_school = "❌ Tidak"
                arrived = "❌ Tidak"
                status = "🔴 Tidak Aktif"
            
            status_messages.append(
                f"👶 **{child.child_name}** ({child.child_id})\n"
                f"🕐 Dimulai: {start_time}\n"
                f"📍 Dekat sekolah: {near_school}\n"
                f"🚗 Sudah tiba: {arrived}\n"
                f"🤖 Status: {status}\n"
            )
        
        final_message = "📊 **Status Monitoring Orangtua**\n\n" + "\n".join(status_messages)
        await update.message.reply_text(final_message, parse_mode='Markdown')
    
    elif user_role == 'teacher':
        children = db_manager.get_children_by_parent_and_role(chat_id, 'teacher')
        
        if not children:
            await update.message.reply_text("❌ Tidak ada murid yang terdaftar.")
            return
        
        child_names = ", ".join([child.child_name for child in children])
        message = (
            f"📊 **Status Guru**\n\n"
            f"🏫 Mode: Alert Receiver\n"
            f"👶 Murid: {child_names}\n"
            f"🚨 Status: Siap menerima alert jatuh\n"
            f"📱 Total murid: {len(children)}"
        )
        await update.message.reply_text(message, parse_mode='Markdown')
    
    else:
        await update.message.reply_text("❌ Anda belum terdaftar dalam sistem.")

async def children_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show list of registered children"""
    chat_id = update.message.chat_id
    user_role = db_manager.get_user_role(chat_id)
    
    if user_role == 'unknown':
        await update.message.reply_text("❌ Anda belum terdaftar dalam sistem.")
        return
    
    children = db_manager.get_children_by_parent_and_role(chat_id, user_role)
    
    if not children:
        role_name = "anak" if user_role == 'parent' else "murid"
        await update.message.reply_text(f"❌ Tidak ada {role_name} yang terdaftar.")
        return
    
    child_list = []
    for i, child in enumerate(children, 1):
        child_list.append(f"{i}. **{child.child_name}**\n   ID: `{child.child_id}`\n   Device: `{child.device_id}`")
    
    role_name = "Anak" if user_role == 'parent' else "Murid"
    message = f"👶 **Daftar {role_name} Terdaftar:**\n\n" + "\n\n".join(child_list)
    await update.message.reply_text(message, parse_mode='Markdown')

# Keep the old register_child for backward compatibility, but mark as admin only
async def register_child(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Legacy admin register child command"""
    await admin_register_child(update, context)

async def handle_location(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle location updates from parents - ONLY FOR PARENTS"""
    if not update.message or not update.message.location:
        return
    
    chat_id = update.message.chat_id
    user_role = db_manager.get_user_role(chat_id)
    
    # HANYA PARENT YANG BOLEH KIRIM LOKASI
    if user_role != 'parent':
        await update.message.reply_text("⚠️ Fitur lokasi hanya untuk orangtua.")
        return
    
    user_location = (update.message.location.latitude, update.message.location.longitude)
    distance = geodesic(user_location, SCHOOL_COORDS).km
    
    # Get all children for this parent
    children = db_manager.get_children_by_parent_and_role(chat_id, 'parent')
    message_sender = TelegramMessageSender(context.application)
    
    for child in children:
        monitoring_key = get_monitoring_key(chat_id, child.child_id)
        monitoring_data = MONITORING_DATA.get(monitoring_key)
        
        if not monitoring_data or not monitoring_data.monitoring_active:
            continue
        
        # Check if near school (within radius)
        if distance <= RADIUS_KM:
            if not monitoring_data.user_near_school:
                await message_sender.send_location_near_school(chat_id, child.child_name, distance)
                await message_sender.send_to_antares(child.device_id)
                monitoring_data.user_near_school = True
            
            # Check if arrived at school (within arrival radius)
            if distance <= ARRIVAL_RADIUS_KM and not monitoring_data.user_arrived:
                await message_sender.send_pickup_prompt(chat_id, child.child_name)
                monitoring_data.user_arrived = True
        else:
            monitoring_data.user_near_school = False

async def handle_response(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle yes/no responses from parents - ONLY FOR PARENTS"""
    chat_id = update.message.chat_id
    text = update.message.text.lower()
    user_role = db_manager.get_user_role(chat_id)
    
    # HANYA PARENT YANG BISA RESPOND
    if user_role != 'parent':
        return
    
    children = db_manager.get_children_by_parent_and_role(chat_id, 'parent')
    message_sender = TelegramMessageSender(context.application)
    
    if text == "ya":
        # Stop monitoring for all children
        for child in children:
            monitoring_key = get_monitoring_key(chat_id, child.child_id)
            if monitoring_key in MONITORING_DATA:
                MONITORING_DATA[monitoring_key].monitoring_active = False
                MONITORING_DATA[monitoring_key].user_arrived = False
                db_manager.end_monitoring_session(chat_id, child.child_id)
            
            await message_sender.send_monitoring_stopped(chat_id, child.child_name)
        
        logger.info(f"Parent {chat_id} completed pickup for all children")
    
    elif text == "tidak":
        # Continue monitoring
        for child in children:
            await message_sender.send_monitoring_continued(chat_id, child.child_name)

# Webhook handler for Antares data (fall detection)
async def handle_antares_webhook(request):
    """Handle incoming data from Antares IoT platform"""
    try:
        data = await request.json()
        logger.info(f"📡 Data dari Antares: {json.dumps(data, indent=2)}")
        
        bot_app: Application = request.app["bot_app"]
        message_sender = TelegramMessageSender(bot_app)
        
        device_id = None
        kondisi = None
        
        # Extract device_id from request path or data
        path_parts = request.path.split('/')
        if len(path_parts) >= 3:  # /monitor/device_id
            device_id = path_parts[2]
        
        # Parse data for condition
        if "m2m:sgn" in data:
            sgn_data = data["m2m:sgn"]
            if sgn_data.get("m2m:vrq") is True:
                return web.json_response({"status": "subscription_verified"})
            
            if "m2m:nev" in sgn_data and "m2m:rep" in sgn_data["m2m:nev"]:
                cin_data = sgn_data["m2m:nev"]["m2m:rep"].get("m2m:cin")
                if cin_data and "con" in cin_data:
                    try:
                        content = json.loads(cin_data["con"])
                        kondisi = content.get("kondisi")
                        if not device_id:
                            device_id = content.get("device_id")
                    except json.JSONDecodeError:
                        logger.error("Failed to parse content from m2m:nev")
        
        elif "m2m:cin" in data:
            try:
                content = json.loads(data["m2m:cin"]["con"])
                kondisi = content.get("kondisi")
                if not device_id:
                    device_id = content.get("device_id")
            except json.JSONDecodeError:
                logger.error("Failed to parse Antares content")
                return web.json_response({"status": "invalid_json"})
        
        elif "kondisi" in data:
            kondisi = data.get("kondisi")
            if not device_id:
                device_id = data.get("device_id")
        
        # If no device_id found, try to extract from headers or query params
        if not device_id:
            device_id = request.headers.get('X-Device-ID') or request.query.get('device_id')
        
        if not device_id:
            logger.warning("No device_id found in request")
            return web.json_response({"status": "no_device_id"})
        
        if kondisi == "terjatuh":
            # Find child by device_id
            child_data = db_manager.get_child_by_device_id(device_id)
            if child_data:
                # Get ONLY TEACHERS for this child
                teacher_chat_ids = db_manager.get_parents_by_child_and_role(child_data.child_id, 'teacher')
                if teacher_chat_ids:
                    await message_sender.send_fall_alert(teacher_chat_ids, child_data.child_name)
                    logger.info(f"Fall alert sent to {len(teacher_chat_ids)} teachers for {child_data.child_name}")
                    return web.json_response({"status": "alert_sent", "teachers_notified": len(teacher_chat_ids)})
                else:
                    logger.warning(f"No teachers found for child {child_data.child_id}")
                    return web.json_response({"status": "no_teachers_found"})
            else:
                logger.warning(f"Child not found for device_id: {device_id}")
                return web.json_response({"status": "child_not_found"})
        
        return web.json_response({"status": "condition_ignored", "condition": kondisi, "device_id": device_id})
    
    except Exception as e:
        logger.error(f"❌ Error webhook: {e}")
        return web.json_response({"status": "error", "message": str(e)}, status=500)



# Health check
async def health_check(request):
    """Health check endpoint"""
    active_monitoring = len([k for k, v in MONITORING_DATA.items() if v.monitoring_active])
    
    return web.json_response({
        "status": "healthy",
        "active_monitoring_sessions": active_monitoring,
        "total_registered_sessions": len(MONITORING_DATA),
        "timestamp": datetime.now().isoformat()
    })

async def reset_ortu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    # Hapus mapping parent_child_mapping dan monitoring_sessions untuk parent ini
    with sqlite3.connect(DATABASE_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM parent_child_mapping WHERE parent_chat_id = ? AND role = 'parent'", (chat_id,))
        cursor.execute("DELETE FROM monitoring_sessions WHERE parent_chat_id = ?", (chat_id,))
        conn.commit()
    # Hapus dari MONITORING_DATA
    keys_to_remove = [k for k in MONITORING_DATA if k.startswith(f"{chat_id}_")]
    for k in keys_to_remove:
        del MONITORING_DATA[k]
    await update.message.reply_text("✅ Semua data orang tua Anda telah direset.")

async def reset_guru(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    # Hapus mapping parent_child_mapping dan monitoring_sessions untuk guru ini
    with sqlite3.connect(DATABASE_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM parent_child_mapping WHERE parent_chat_id = ? AND role = 'teacher'", (chat_id,))
        cursor.execute("DELETE FROM monitoring_sessions WHERE parent_chat_id = ?", (chat_id,))
        conn.commit()
    # Hapus dari MONITORING_DATA
    keys_to_remove = [k for k in MONITORING_DATA if k.startswith(f"{chat_id}_")]
    for k in keys_to_remove:
        del MONITORING_DATA[k]
    await update.message.reply_text("✅ Semua data guru Anda telah direset.")

async def init_app():
    """Initialize the application"""
    if not TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN tidak ditemukan!")
        return None
    
    if not SCHOOL_COORDS or SCHOOL_COORDS == (0, 0):
        logger.error("SCHOOL_COORDS tidak valid!")
        return None
    
    logger.info(f"Initializing bot with token: {TOKEN[:10]}...")
    
    # Setup bot dengan timeout yang lebih besar
    app = Application.builder()\
        .token(TOKEN)\
        .read_timeout(30)\
        .write_timeout(30)\
        .connect_timeout(30)\
        .pool_timeout(30)\
        .build()
    
    # ConversationHandler untuk /register_guru
    guru_conv = ConversationHandler(
        entry_points=[CommandHandler("register_guru", register_guru)],
        states={
            GURU_CHOOSE: [MessageHandler(filters.TEXT & ~filters.COMMAND, guru_choose)],
        },
        fallbacks=[],
        name="guru_registration",
        persistent=False
    )
    app.add_handler(guru_conv)

    # ConversationHandler untuk /register_ortu
    ortu_conv = ConversationHandler(
        entry_points=[CommandHandler("register_ortu", register_ortu)],
        states={
            ORTU_CHOOSE: [MessageHandler(filters.TEXT & ~filters.COMMAND, ortu_choose)],
        },
        fallbacks=[],
        name="ortu_registration",
        persistent=False
    )
    app.add_handler(ortu_conv)
    
    # Add other handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("children", children_list))
    app.add_handler(CommandHandler("register_user", register_user))
    app.add_handler(CommandHandler("reset_ortu", reset_ortu))
    app.add_handler(CommandHandler("reset_guru", reset_guru))
    app.add_handler(MessageHandler(filters.LOCATION, handle_location))
    app.add_handler(MessageHandler(filters.Regex("^(Ya|Tidak|ya|tidak)$"), handle_response))
    
    # Initialize bot
    await app.initialize()
    
    # Setup web application
    web_app = web.Application()
    web_app["bot_app"] = app
    
    web_app.add_routes([
        web.post("/monitor", handle_antares_webhook),
        web.post("/monitor/{device_id}", handle_antares_webhook),
        web.get("/health", health_check)
    ])
    
    logger.info(f"🤖 Bot ready on port {WEBHOOK_PORT}")
    logger.info(f"👨‍🏫 Guru: {len(GURU_CODES)} kode tersedia")
    logger.info(f"👨‍👩‍👧‍👦 Orang Tua: {len(ORTU_CODES)} kode tersedia")
    
    return app, web_app

def main():
    """Main function to run the server"""
    async def run_server():
        result = await init_app()
        if result is None:
            return
        
        bot_app, web_app = result
        
        # Start bot polling
        await bot_app.start()
        await bot_app.updater.start_polling(drop_pending_updates=True)
        logger.info("🤖 Bot polling started")
        
        # Run web server
        runner = web.AppRunner(web_app)
        await runner.setup()
        site = web.TCPSite(runner, host="0.0.0.0", port=WEBHOOK_PORT)
        await site.start()
        
        logger.info(f"🚀 Multi-Parent Server running on http://0.0.0.0:{WEBHOOK_PORT}")
        logger.info("✅ Bot and webhook ready! Press Ctrl+C to stop")
        logger.info("📋 Available endpoints:")
        logger.info("   POST /monitor - General webhook for IoT data")
        logger.info("   POST /monitor/{device_id} - Device-specific webhook")
        logger.info("   GET /health - Health check")
        
        # Keep running both services
        try:
            while True:
                await asyncio.sleep(1)
        except KeyboardInterrupt:
            logger.info("🛑 Shutting down...")
        finally:
            await bot_app.updater.stop()
            await bot_app.stop()
            await runner.cleanup()
            logger.info("✅ Cleanup complete")
    
    # Run the server
    try:    
        asyncio.run(run_server())
    except KeyboardInterrupt:
        logger.info("Program terminated by user")
    except Exception as e:
        logger.error(f"Error: {e}")


if __name__ == "__main__":
    main()