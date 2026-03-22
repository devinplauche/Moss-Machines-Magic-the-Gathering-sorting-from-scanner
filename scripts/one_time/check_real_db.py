#!/usr/bin/env python3
import sqlite3
import os

db_path = r"Current-Version\unified_card_database.db"
print(f"Checking database at: {db_path}")
print(f"Database exists: {os.path.exists(db_path)}")
file_size = os.path.getsize(db_path) if os.path.exists(db_path) else 'N/A'
print(f"File size: {file_size / 1024 / 1024:.2f} MB" if file_size != 'N/A' else f"File size: {file_size}")

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
    c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'cards_%'")
    card_tables = [row[0] for row in c.fetchall()]
    print(f"Card tables: {len(card_tables)}")
    
    if card_tables:
        c.execute(f"SELECT COUNT(*) FROM {card_tables[0]}")
        count = c.fetchone()[0]
        print(f"Sample: {card_tables[0]} has {count} cards")
    
    conn.close()
    print("\n✅ Database is valid and readable!")
except Exception as e:
    print(f"\n❌ Error reading database: {e}")
    import traceback
    traceback.print_exc()
