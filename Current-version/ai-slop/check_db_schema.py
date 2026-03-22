#!/usr/bin/env python3
"""Check if card hashes are precomputed in database"""
import sqlite3
import os

db_path = "unified_card_database.db"

print("=" * 80)
print("DATABASE SCHEMA CHECK")
print("=" * 80)

conn = sqlite3.connect(db_path)
cursor = conn.cursor()

# Get the Magic table schema
print("\n[1] cards_1 (Magic) table columns:")
cursor.execute("PRAGMA table_info(cards_1)")
columns = cursor.fetchall()
for col in columns:
    print(f"  {col[1]:30s} ({col[2]})")

# Look for hash-related columns
print("\n[2] Searching for hash columns:")
has_hash = False
for col in columns:
    col_name = col[1].lower()
    if 'hash' in col_name or 'phash' in col_name:
        print(f"  ✓ Found: {col[1]}")
        has_hash = True

if not has_hash:
    print("  ✗ No hash columns found")
    print("\n  Note: Database stores card IMAGES, not precomputed hashes")
    print("  Hashes are computed on-demand during scanning")

# Get one card to see structure
print("\n[3] Sample card data (Hisoka's Defiance):")
cursor.execute("SELECT * FROM cards_1 WHERE name = 'Hisoka''s Defiance' LIMIT 1")
card = cursor.fetchone()
if card:
    cursor.execute("PRAGMA table_info(cards_1)")
    cols = [row[1] for row in cursor.fetchall()]
    for col, val in zip(cols[:15], card[:15]):
        print(f"  {col:30s}: {val}")

# Check if there's an images table
print("\n[4] Looking for image data:")
cursor.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
tables = [row[0] for row in cursor.fetchall()]
print(f"  Total tables: {len(tables)}")

image_tables = [t for t in tables if 'image' in t.lower()]
if image_tables:
    print(f"  Image-related tables: {image_tables}")
    for table in image_tables:
        cursor.execute(f"SELECT COUNT(*) FROM {table}")
        count = cursor.fetchone()[0]
        print(f"    - {table}: {count} entries")

conn.close()
