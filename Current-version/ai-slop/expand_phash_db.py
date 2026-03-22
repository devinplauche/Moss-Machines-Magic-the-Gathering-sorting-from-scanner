#!/usr/bin/env python3
"""Expand the Magic phash database with more cards for better coverage"""
import sqlite3
import requests
import io
from PIL import Image
import imagehash
from pathlib import Path
import time

DB_PATH = "unified_card_database.db"
PHASH_DB = Path(__file__).parent / "recognition_data" / "phash_cards_1.db"

def compute_phash(image_url):
    """Download and compute phash for a card image"""
    try:
        response = requests.get(image_url, timeout=10)
        response.raise_for_status()
        
        img = Image.open(io.BytesIO(response.content))
        if img.mode != 'RGB':
            img = img.convert('RGB')
        
        img = img.resize((64, 64), Image.LANCZOS)
        
        r, g, b = img.split()
        r_hash = str(imagehash.phash(r, hash_size=16))
        g_hash = str(imagehash.phash(g, hash_size=16))
        b_hash = str(imagehash.phash(b, hash_size=16))
        
        return r_hash, g_hash, b_hash
    except Exception as e:
        return None, None, None

print("=" * 80)
print("EXPAND MAGIC PHASH DATABASE")
print("=" * 80)

# Get more Magic cards from database
print("\n[1] Fetching Magic cards from database...")
conn = sqlite3.connect(DB_PATH)
cursor = conn.cursor()

# Get cards with images, prioritizing Hisoka's Defiance and common cards
cursor.execute("""
SELECT id, product_id, image_url, name FROM cards_1 
WHERE image_url IS NOT NULL 
  AND image_url != '' 
  AND (name = 'Hisoka''s Defiance' 
       OR rarity IN ('Common', 'Uncommon', 'Rare')
       OR market_price > 0.5)
GROUP BY product_id
ORDER BY 
  CASE WHEN name = 'Hisoka''s Defiance' THEN 0 ELSE 1 END,
  name
LIMIT 500
""")
cards = cursor.fetchall()
conn.close()

print(f"  Found {len(cards)} cards to process")

# Load existing database
print(f"\n[2] Loading existing database: {PHASH_DB}")
phash_conn = sqlite3.connect(str(PHASH_DB))
phash_cursor = phash_conn.cursor()

# Get existing product IDs
phash_cursor.execute("SELECT product_id FROM cards")
existing_ids = set(str(row[0]) for row in phash_cursor.fetchall())
print(f"  Already has {len(existing_ids)} cards")

phash_conn.close()

# Add new cards
print(f"\n[3] Adding new cards to database...")
phash_conn = sqlite3.connect(str(PHASH_DB))
phash_cursor = phash_conn.cursor()

success_count = 0
skip_count = 0
fail_count = 0
hisoka_found = False

for i, (card_id, product_id, image_url, name) in enumerate(cards, 1):
    product_id_str = str(product_id)
    
    if product_id_str in existing_ids:
        skip_count += 1
        continue
    
    print(f"  [{i}/{len(cards)}] {name:50s}...", end=" ", flush=True)
    
    r_hash, g_hash, b_hash = compute_phash(image_url)
    
    if r_hash and g_hash and b_hash:
        phash_cursor.execute("""
        INSERT OR IGNORE INTO cards (id, product_id, r_phash, g_phash, b_phash)
        VALUES (?, ?, ?, ?, ?)
        """, (card_id, product_id_str, r_hash, g_hash, b_hash))
        success_count += 1
        
        if name == "Hisoka's Defiance":
            hisoka_found = True
            print("✓ (HISOKA)")
        else:
            print("✓")
    else:
        fail_count += 1
        print("✗")
    
    # Commit every 50 cards
    if (success_count + fail_count) % 50 == 0:
        phash_conn.commit()

phash_conn.commit()

# Verify
phash_cursor.execute("SELECT COUNT(*) FROM cards")
total = phash_cursor.fetchone()[0]

# Check if Hisoka is still there
phash_cursor.execute("SELECT COUNT(*) FROM cards WHERE product_id = '12021'")
hisoka_count = phash_cursor.fetchone()[0]

phash_conn.close()

print(f"\n[4] Summary:")
print(f"  ✓ Added: {success_count}")
print(f"  ⊘ Skipped: {skip_count}")
print(f"  ✗ Failed: {fail_count}")
print(f"  Total in database: {total}")
print(f"  Hisoka's Defiance in database: {hisoka_count}")

db_size_mb = PHASH_DB.stat().st_size / (1024 * 1024)
print(f"  Database size: {db_size_mb:.1f} MB")

print("\n" + "=" * 80)
print("Ready to test! Run:")
print("  python optimized_scanner.py debug_crops/1772936169_eb106b42_eb406b42_eb406b42.png -g Magic")
print("=" * 80)
