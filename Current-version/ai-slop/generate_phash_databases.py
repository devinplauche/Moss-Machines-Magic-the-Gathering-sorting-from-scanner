#!/usr/bin/env python3
"""
Generate phash recognition databases from unified_card_database.db
Creates precomputed hash databases in recognition_data/ folder for each game
"""
import sqlite3
import os
from pathlib import Path
import imagehash
from PIL import Image
import io
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
import time
from urllib.parse import urljoin

DB_PATH = "unified_card_database.db"
OUTPUT_DIR = Path(__file__).parent / "recognition_data"
OUTPUT_DIR.mkdir(exist_ok=True)

# Map game table numbers to game names
GAME_TABLE_MAPPING = {}

def load_game_mapping():
    """Load game table mapping from database"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Get all card tables
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'cards_%' ORDER BY name")
    tables = cursor.fetchall()
    
    for i, table_row in enumerate(tables, 1):
        table_name = table_row[0]
        # Get sample record to find game name
        cursor.execute(f"SELECT game FROM {table_name} LIMIT 1")
        result = cursor.fetchone()
        if result:
            game_name = result[0]
            GAME_TABLE_MAPPING[i] = {
                'game': game_name,
                'table': table_name,
                'id': i
            }
    
    conn.close()
    print(f"[+] Loaded {len(GAME_TABLE_MAPPING)} games")
    for gid, info in GAME_TABLE_MAPPING.items():
        print(f"    Game {gid}: {info['game']}")

def compute_phash_from_url(card_id, product_id, image_url, use_grayscale=False):
    """Download image from URL and compute phash"""
    if not image_url:
        return None
    
    try:
        # Try to download image
        response = requests.get(image_url, timeout=5)
        response.raise_for_status()
        
        # Load as PIL image
        img = Image.open(io.BytesIO(response.content))
        
        # Convert to RGB if needed
        if img.mode != 'RGB':
            img = img.convert('RGB')
        
        # Resize to standard size
        img = img.resize((64, 64), Image.LANCZOS)
        
        if use_grayscale:
            gray = img.convert('L')
            phash = imagehash.phash(gray, hash_size=16)
            return str(phash)
        else:
            r, g, b = img.split()
            r_hash = imagehash.phash(r, hash_size=16)
            g_hash = imagehash.phash(g, hash_size=16)
            b_hash = imagehash.phash(b, hash_size=16)
            return {
                'r': str(r_hash),
                'g': str(g_hash),
                'b': str(b_hash)
            }
    except Exception as e:
        return None

def generate_phash_db_for_game(game_id, game_info, max_cards=None):
    """Generate phash database for a single game"""
    table = game_info['table']
    game_name = game_info['game']
    
    # Read all cards from main database
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    query = f"SELECT id, product_id, image_url FROM {table}"
    if max_cards:
        query += f" LIMIT {max_cards}"
    
    cursor.execute(query)
    cards = cursor.fetchall()
    conn.close()
    
    print(f"\n[*] Processing {game_name} ({len(cards)} cards)")
    
    # Create output database
    phash_db_path = OUTPUT_DIR / f"phash_cards_{game_id}.db"
    phash_conn = sqlite3.connect(str(phash_db_path))
    phash_cursor = phash_conn.cursor()
    
    # Create table
    phash_cursor.execute("""
    CREATE TABLE IF NOT EXISTS cards (
        id INTEGER PRIMARY KEY,
        product_id TEXT,
        r_phash TEXT,
        g_phash TEXT,
        b_phash TEXT,
        grayscale_phash TEXT
    )
    """)
    
    # Process cards
    success_count = 0
    fail_count = 0
    
    for card_id, product_id, image_url in cards:
        try:
            hashes = compute_phash_from_url(card_id, product_id, image_url)
            
            if hashes:
                if isinstance(hashes, dict):
                    phash_cursor.execute("""
                    INSERT OR REPLACE INTO cards (id, product_id, r_phash, g_phash, b_phash, grayscale_phash)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """, (card_id, product_id, hashes['r'], hashes['g'], hashes['b'], None))
                    success_count += 1
                else:
                    phash_cursor.execute("""
                    INSERT OR REPLACE INTO cards (id, product_id, r_phash, g_phash, b_phash, grayscale_phash)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """, (card_id, product_id, None, None, None, hashes))
                    success_count += 1
            else:
                fail_count += 1
            
            # Progress indicator
            if (success_count + fail_count) % 100 == 0:
                print(f"  [{success_count + fail_count}/{len(cards)}] Success: {success_count}, Failed: {fail_count}")
        
        except Exception as e:
            fail_count += 1
    
    phash_conn.commit()
    phash_conn.execute("CREATE INDEX idx_product_id ON cards(product_id)")
    phash_conn.commit()
    phash_conn.close()
    
    print(f"  [✓] Complete: {success_count} hashes generated, {fail_count} failed")
    print(f"  Saved to: {phash_db_path}")
    
    return success_count, fail_count

def main():
    print("=" * 80)
    print("PHASH DATABASE GENERATOR")
    print("=" * 80)
    
    # Load game mapping
    load_game_mapping()
    
    # Warn about time
    total_cards = 0
    conn = sqlite3.connect(DB_PATH)
    for game_id, game_info in GAME_TABLE_MAPPING.items():
        cursor = conn.cursor()
        cursor.execute(f"SELECT COUNT(*) FROM {game_info['table']}")
        count = cursor.fetchone()[0]
        total_cards += count
    conn.close()
    
    print(f"\n[!] Total cards to process: {total_cards:,}")
    print(f"[!] This may take several HOURS and use significant bandwidth")
    print(f"[!] Requires downloading card images from tcgtraders.app")
    
    response = input("\nProceed? (yes/no): ").strip().lower()
    if response != 'yes':
        print("Cancelled.")
        return
    
    # Generate databases
    start_time = time.time()
    total_success = 0
    total_fail = 0
    
    for game_id in sorted(GAME_TABLE_MAPPING.keys()):
        game_info = GAME_TABLE_MAPPING[game_id]
        try:
            success, fail = generate_phash_db_for_game(game_id, game_info)
            total_success += success
            total_fail += fail
        except Exception as e:
            print(f"  [✗] Error: {e}")
    
    elapsed = time.time() - start_time
    
    print("\n" + "=" * 80)
    print("SUMMARY")
    print("=" * 80)
    print(f"Total successful hashes: {total_success:,}")
    print(f"Total failed: {total_fail:,}")
    print(f"Total time: {elapsed/3600:.1f} hours")
    print(f"Output directory: {OUTPUT_DIR}")
    
    # List generated files
    phash_files = list(OUTPUT_DIR.glob("phash_cards_*.db"))
    print(f"\nGenerated {len(phash_files)} phash database files:")
    for f in sorted(phash_files):
        size_mb = f.stat().st_size / (1024 * 1024)
        print(f"  - {f.name} ({size_mb:.1f} MB)")

if __name__ == "__main__":
    main()
