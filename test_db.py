# test_db.py
import sqlite3
import os
from dotenv import load_dotenv

load_dotenv()
DATABASE_PATH = os.getenv("DATABASE_PATH", "child_monitoring.db")

def test_database():
    print(f"Testing database: {DATABASE_PATH}")
    
    with sqlite3.connect(DATABASE_PATH) as conn:
        cursor = conn.cursor()
        
        # Test children
        cursor.execute("SELECT COUNT(*) FROM children")
        print(f"Total children: {cursor.fetchone()[0]}")
        
        # Test parent_child_mapping
        cursor.execute("SELECT COUNT(*) FROM parent_child_mapping")
        print(f"Total mappings: {cursor.fetchone()[0]}")
        
        # Test user_code_mapping
        cursor.execute("SELECT COUNT(*) FROM user_code_mapping")
        print(f"Total user codes: {cursor.fetchone()[0]}")
        
        # Show all data
        cursor.execute("SELECT * FROM children")
        print("\nChildren:")
        for row in cursor.fetchall():
            print(f"  {row}")
        
        cursor.execute("SELECT * FROM parent_child_mapping")
        print("\nMappings:")
        for row in cursor.fetchall():
            print(f"  {row}")
        
        print("\n✅ Database OK!")

if __name__ == "__main__":
    test_database()