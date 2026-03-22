#!/usr/bin/env python3
"""Create minimal phash database for Magic game to test scanner"""
import sqlite3
import requests
import io
from PIL import Image
import imagehash
from pathlib import Path
import sys
import time

DB_PATH = "unified_card_database.db"
OUTPUT_DIR = Path(__file__).parent / "recognition_data"
OUTPUT_DIR.mkdir(exist_ok=True)

def compute_phash(image_url):
    """Download and compute phash for a card image"""
    try:
        response = requests.get(image_url, timeout=10)
        response.raise_for_status()
        
        img = Image.open(io.BytesIO(response.content))
        if img.mode != 'RGB':
            img = img.convert('RGB')
        
        # Resize to standard size
        img = img.resize((64, 64), Image.LANCZOS)
        
        r, g, b = img.split()
        r_hash = str(imagehash.phash(r, hash_size=16))
        g_hash = str(imagehash.phash(g, hash_size=16))
        b_hash = str(imagehash.phash(b, hash_size=16))
        
        return r_hash, g_hash, b_hash
    except Exception as e:
        print(f"  ✗ Error: {e}")
        return None, None, None

print("=" * 80)
print("MINIMAL MAGIC PHASH DATABASE GENERATOR")
print("=" * 80)

# Get sample Magic cards from database
print("\n[1] Fetching cards from database...")
conn = sqlite3.connect(DB_PATH)
cursor = conn.cursor()

# Get Hisoka's Defiance + some other common Magic cards
cursor.execute("""
SELECT id, product_id, image_url, name FROM cards_1 
WHERE image_url IS NOT NULL AND image_url != '' 
  AND rarity IN ('Common', 'Rare', 'Uncommon')
ORDER BY RANDOM()
LIMIT 50
""")
cards = cursor.fetchall()
conn.close()

print(f"  Found {len(cards)} cards to process")

# Create phash database for Magic (game_id = 1)
phash_db_path = OUTPUT_DIR / "phash_cards_1.db"
print(f"\n[2] Creating phash database: {phash_db_path}")

phash_conn = sqlite3.connect(str(phash_db_path))
phash_cursor = phash_conn.cursor()

phash_cursor.execute("""
DROP TABLE IF EXISTS cards
""")

phash_cursor.execute("""
CREATE TABLE cards (
    id INTEGER PRIMARY KEY,
    product_id TEXT,
    r_phash TEXT,
    g_phash TEXT,
    b_phash TEXT,
    grayscale_phash TEXT
)
""")

# Process cards
print("\n[3] Processing cards...")
success_count = 0
fail_count = 0

for card_id, product_id, image_url, name in cards:
    if not image_url:
        continue
    
    print(f"  Processing {name:45s}...", end=" ", flush=True)
    
    r_hash, g_hash, b_hash = compute_phash(image_url)
    
    if r_hash and g_hash and b_hash:
        phash_cursor.execute("""
        INSERT INTO cards (id, product_id, r_phash, g_phash, b_phash)
        VALUES (?, ?, ?, ?, ?)
        """, (card_id, product_id, r_hash, g_hash, b_hash))
        success_count += 1
        print("✓")
    else:
        fail_count += 1
        print("✗")

phash_conn.commit()
phash_conn.execute("CREATE INDEX idx_product_id ON cards(product_id)")
phash_conn.commit()
phash_conn.close()

print(f"\n[4] Summary:")
print(f"  ✓ Successful: {success_count}")
print(f"  ✗ Failed: {fail_count}")
print(f"  Database: {phash_db_path} ({phash_db_path.stat().st_size / 1024:.1f} KB)")

print("\n" + "=" * 80)
print("Test with: python optimized_scanner.py ../Collection/ScanImages/hisokas-defiance.jpg")
print("=" * 80)
