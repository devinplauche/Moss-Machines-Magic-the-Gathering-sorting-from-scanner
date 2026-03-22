#!/usr/bin/env python3
"""Test phash computation and matching for Hisoka's Defiance from URL"""
import sqlite3
import requests
import io
from PIL import Image
import imagehash
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent))
from optimized_scanner import OptimizedCardScanner

DB_PATH = "unified_card_database.db"

print("=" * 80)
print("HISOKA'S DEFIANCE - PHASH URL TEST")
print("=" * 80)

# Get the image URL from database
print("\n[1] Fetching card data from database...")
conn = sqlite3.connect(DB_PATH)
cursor = conn.cursor()

cursor.execute("SELECT id, product_id, image_url FROM cards_1 WHERE name = 'Hisoka''s Defiance' LIMIT 1")
result = cursor.fetchone()

if not result:
    print("  ✗ Card not found in database")
    sys.exit(1)

card_id, product_id, image_url = result
print(f"  ✓ Card found:")
print(f"    ID: {card_id}")
print(f"    Product ID: {product_id}")
print(f"    Image URL: {image_url}")

conn.close()

# Download and hash the image
print("\n[2] Downloading image from URL...")
try:
    response = requests.get(image_url, timeout=10)
    response.raise_for_status()
    print(f"  ✓ Downloaded: {len(response.content)} bytes")
    
    # Load as PIL image
    pil_img = Image.open(io.BytesIO(response.content))
    print(f"  ✓ Image loaded: {pil_img.size} {pil_img.mode}")
except Exception as e:
    print(f"  ✗ Failed to download image: {e}")
    sys.exit(1)

# Compute phash using scanner's method
print("\n[3] Computing phash using scanner method...")
scanner = OptimizedCardScanner(db_path=DB_PATH)

r_hash, g_hash, b_hash = scanner.compute_phash(pil_img)
print(f"  R-channel: {r_hash}")
print(f"  G-channel: {g_hash}")
print(f"  B-channel: {b_hash}")

# Also compute with simple PIL method for comparison
print("\n[4] Computing phash using simple PIL method...")
if pil_img.mode != 'RGB':
    rgb_img = pil_img.convert('RGB')
else:
    rgb_img = pil_img

r, g, b = rgb_img.split()
r_simple = imagehash.phash(r, hash_size=16)
g_simple = imagehash.phash(g, hash_size=16)
b_simple = imagehash.phash(b, hash_size=16)

print(f"  R-channel: {r_simple}")
print(f"  G-channel: {g_simple}")
print(f"  B-channel: {b_simple}")

# Try to scan the downloaded image
print("\n[5] Scanning with recognition system...")
try:
    import cv2
    import numpy as np
    
    # Convert PIL to OpenCV
    cv_img = cv2.imread(image_url)
    if cv_img is None:
        # Try getting from memory
        img_array = np.array(rgb_img)
        cv_img = cv2.cvtColor(img_array, cv2.COLOR_RGB2BGR)
    
    print(f"  Image shape: {cv_img.shape if cv_img is not None else 'None'}")
    
    matches, elapsed = scanner.scan_card(cv_img, threshold=40, top_n=5)
    
    print(f"  Scan completed in {elapsed:.3f}s")
    print(f"  Found {len(matches)} matches")
    
    if matches:
        print("\n  Top matches:")
        for i, m in enumerate(matches[:3], 1):
            print(f"    {i}. {m.get('name', '?'):45s} dist={m.get('distance', '?'):6.1f} conf={m.get('confidence', 0):.1f}%")
            if m.get('name') == "Hisoka's Defiance":
                print(f"       ✓✓✓ FOUND! ✓✓✓")
    else:
        print("  ✗ No matches found")
        
except Exception as e:
    print(f"  ✗ Scan failed: {e}")
    import traceback
    traceback.print_exc()

# Manual distance calculation
print("\n[6] Manual distance testing...")
print("  Testing if Magic database has the card hash data...")

try:
    import sqlite3
    from pathlib import Path
    
    # Try to load the phash database if it exists
    phash_db = Path(__file__).parent / 'recognition_data' / 'phash_cards_1.db'
    
    if phash_db.exists():
        print(f"  ✓ Found phash database: {phash_db}")
        phash_conn = sqlite3.connect(str(phash_db))
        phash_cursor = phash_conn.cursor()
        
        # Look up the card
        phash_cursor.execute("SELECT r_phash, g_phash, b_phash FROM cards WHERE product_id = ?", (str(product_id),))
        hash_result = phash_cursor.fetchone()
        
        if hash_result:
            db_r, db_g, db_b = hash_result
            print(f"  ✓ Found card hashes in database:")
            print(f"    R: {db_r[:32]}...")
            print(f"    G: {db_g[:32]}...")
            print(f"    B: {db_b[:32]}...")
            
            # Compute distances
            if r_hash and db_r:
                from optimized_scanner import OptimizedCardScanner
                dist_r = scanner.hamming_distance(r_hash, db_r)
                dist_g = scanner.hamming_distance(g_hash, db_g)
                dist_b = scanner.hamming_distance(b_hash, db_b)
                avg_dist = (dist_r + dist_g + dist_b) / 3.0
                
                print(f"\n  Distance measurements:")
                print(f"    R distance: {dist_r}")
                print(f"    G distance: {dist_g}")
                print(f"    B distance: {dist_b}")
                print(f"    Average:   {avg_dist:.1f}")
                print(f"    Confidence: {max(0, 100 - (avg_dist/256*100)):.1f}%")
        else:
            print(f"  ✗ Card not found in phash database (product_id={product_id})")
        
        phash_conn.close()
    else:
        print(f"  ✗ Phash database not found: {phash_db}")
        print("  → This is why the scanner can't find cards!")
        
except Exception as e:
    print(f"  Error checking phash database: {e}")

print("\n" + "=" * 80)
