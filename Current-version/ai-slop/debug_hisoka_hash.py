#!/usr/bin/env python3
"""Debug script to diagnose Hisoka's Defiance recognition issue"""
import os
import cv2
import numpy as np
from PIL import Image
import imagehash
import sqlite3

# Image path
img_path = "../Collection/ScanImages/hisokas-defiance.jpg"
db_path = "unified_card_database.db"

print("=" * 80)
print("HISOKA'S DEFIANCE DIAGNOSTIC")
print("=" * 80)

# 1. Check image exists and load
print(f"\n[1] Image Check:")
if not os.path.exists(img_path):
    print(f"  ✗ Image not found: {img_path}")
    print(f"  Absolute path would be: {os.path.abspath(img_path)}")
else:
    print(f"  ✓ Image found: {img_path}")
    print(f"    Size: {os.path.getsize(img_path)} bytes")

# 2. Try to load with PIL and OpenCV
print(f"\n[2] Image Loading:")
try:
    pil_img = Image.open(img_path)
    print(f"  ✓ PIL loaded: {pil_img.size} {pil_img.mode}")
except Exception as e:
    print(f"  ✗ PIL failed: {e}")
    pil_img = None

try:
    cv_img = cv2.imread(img_path)
    if cv_img is not None:
        print(f"  ✓ OpenCV loaded: {cv_img.shape}")
    else:
        print(f"  ✗ OpenCV returned None")
except Exception as e:
    print(f"  ✗ OpenCV failed: {e}")
    cv_img = None

# 3. Compute hashes using PIL
print(f"\n[3] PIL Perceptual Hashes:")
if pil_img:
    try:
        phash = imagehash.phash(pil_img)
        print(f"  Perceptual Hash: {phash}")
        print(f"  Hash hex: {phash.hash}")
    except Exception as e:
        print(f"  ✗ pHash failed: {e}")

# 4. Compute RGB channel hashes (method used by scanner)
print(f"\n[4] RGB Channel Hashes (Scanner Method):")
if pil_img:
    try:
        # Convert to RGB if needed
        if pil_img.mode != 'RGB':
            rgb_img = pil_img.convert('RGB')
        else:
            rgb_img = pil_img
        
        # Resize to standard size
        rgb_img = rgb_img.resize((8, 8))
        
        # Split into channels and hash each
        r, g, b = rgb_img.split()
        
        r_hash = imagehash.phash(r)
        g_hash = imagehash.phash(g)
        b_hash = imagehash.phash(b)
        
        print(f"  R channel: {r_hash}")
        print(f"  G channel: {g_hash}")
        print(f"  B channel: {b_hash}")
        
        avg_hash = r_hash + g_hash + b_hash  # Average
        print(f"  Average: {avg_hash}")
    except Exception as e:
        print(f"  ✗ RGB hashing failed: {e}")

# 5. Check database and find the card
print(f"\n[5] Database Check:")
if not os.path.exists(db_path):
    print(f"  ✗ Database not found: {db_path}")
else:
    print(f"  ✓ Database found: {db_path}")
    
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # Query for exact match
        cursor.execute("SELECT * FROM cards_1 WHERE name = 'Hisoka''s Defiance' LIMIT 1")
        result = cursor.fetchone()
        
        if result:
            print(f"  ✓ Card found in Magic database!")
            print(f"    ID: {result[0]}")
            # Try to get column names
            cursor.execute("PRAGMA table_info(cards_1)")
            columns = cursor.fetchall()
            col_names = [col[1] for col in columns]
            print(f"    Columns: {col_names[:10]}...")
            # Print first few values
            for i, (col, val) in enumerate(zip(col_names[:5], result[:5])):
                print(f"    {col}: {val}")
        else:
            print(f"  ✗ Card NOT found in cards_1 (Magic)")
            
            # Check all card tables
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'cards_%'")
            tables = cursor.fetchall()
            print(f"  Available card tables: {len(tables)}")
            
            for table in tables[:5]:
                table_name = table[0]
                cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
                count = cursor.fetchone()[0]
                print(f"    - {table_name}: {count} cards")
        
        conn.close()
    except Exception as e:
        print(f"  ✗ Database query failed: {e}")

print("\n" + "=" * 80)
print("END DIAGNOSTIC")
print("=" * 80)
