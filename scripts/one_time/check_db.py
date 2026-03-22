#!/usr/bin/env python3
import sqlite3
import os

db_path = "unified_card_database.db"
print(f"Checking database at: {db_path}")
print(f"CWD: {os.getcwd()}")
print(f"Database exists: {os.path.exists(db_path)}")
print(f"File size: {os.path.getsize(db_path) if os.path.exists(db_path) else 'N/A'} bytes")

try:
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = [row[0] for row in c.fetchall()]
    print(f"\nTotal tables: {len(tables)}")
    print(f"First 10 tables: {tables[:10]}")
    
    # Check games table
    c.execute("SELECT COUNT(*) FROM games")
    game_count = c.fetchone()[0]
    print(f"Games in database: {game_count}")
    
    # Check if Magic table exists and has cards
    c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE '%magic%'")
    magic_tables = [row[0] for row in c.fetchall()]
    print(f"Magic tables: {magic_tables}")
    
    if magic_tables:
        c.execute(f"SELECT COUNT(*) FROM {magic_tables[0]}")
        magic_count = c.fetchone()[0]
        print(f"Cards in {magic_tables[0]}: {magic_count}")
    
    conn.close()
    print("\n✅ Database is valid and readable")
except Exception as e:
    print(f"\n❌ Error reading database: {e}")
    import traceback
    traceback.print_exc()
