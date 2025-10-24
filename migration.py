import sqlite3
import os
from dotenv import load_dotenv

load_dotenv()
DATABASE_PATH = os.getenv("DATABASE_PATH", "child_monitoring.db")

def migrate_database():
    """Migrate database - add new table without losing old data"""
    print(f"🔄 Melakukan migrasi database: {DATABASE_PATH}")
    
    with sqlite3.connect(DATABASE_PATH) as conn:
        cursor = conn.cursor()
        
        # Cek apakah tabel sudah ada
        cursor.execute("""
            SELECT name FROM sqlite_master 
            WHERE type='table' AND name='user_code_mapping'
        """)
        
        if cursor.fetchone():
            print("✅ Tabel user_code_mapping sudah ada, skip migrasi")
            return
        
        # Tambahkan tabel baru
        print("📝 Menambahkan tabel user_code_mapping...")
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS user_code_mapping (
                user_code TEXT PRIMARY KEY,
                chat_id INTEGER UNIQUE NOT NULL,
                role TEXT NOT NULL,
                registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        conn.commit()
        print("✅ Migrasi berhasil!")
        
        # Tampilkan summary data
        cursor.execute("SELECT COUNT(*) FROM children")
        total_children = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM parent_child_mapping WHERE role='parent'")
        total_parents = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM parent_child_mapping WHERE role='teacher'")
        total_teachers = cursor.fetchone()[0]
        
        print("\n📊 Summary Data:")
        print(f"   👶 Total Anak: {total_children}")
        print(f"   👨‍👩‍👧‍👦 Total Orang Tua: {total_parents}")
        print(f"   🏫 Total Guru: {total_teachers}")
        print("\n💡 Data lama Anda tetap aman!")

if __name__ == "__main__":
    migrate_database()