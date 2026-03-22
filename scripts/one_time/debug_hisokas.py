#!/usr/bin/env python3
"""
Debug: Check if Hisokas card is in database and test with relaxed thresholds
"""
import sys
import os
from PIL import Image

sys.path.insert(0, r'Current-Version')
from optimized_scanner import OptimizedCardScanner

print("\n" + "=" * 80)
print("DEBUG: Hisokas Card Database Check")
print("=" * 80)

db_path = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), 
    'Current-Version', 
    'unified_card_database.db'
)

scanner = OptimizedCardScanner(db_path=db_path, max_workers=4, cache_enabled=True)

# Check what games we have
print(f"\nAvailable games ({len(scanner.games)}):")
for game in list(scanner.games.keys())[:10]:
    print(f"  - {game}")

# Search for Hisokas in database
print("\nSearching for 'Hisokas' in database...")
import sqlite3
conn = sqlite3.connect(db_path)
c = conn.cursor()

# Search all card tables for Hisokas
found = False
for game_name, table_name in scanner.games.items():
    try:
        c.execute(f"SELECT COUNT(*) FROM {table_name} WHERE name LIKE ?", ('%Hisokas%',))
        count = c.fetchone()[0]
        if count > 0:
            print(f"  ✅ Found {count} card(s) in {game_name}")
            c.execute(f"SELECT name, set FROM {table_name} WHERE name LIKE ? LIMIT 5", ('%Hisokas%',))
            for row in c.fetchall():
                print(f"     - {row[0]} ({row[1]})")
            found = True
    except Exception as e:
        pass

if not found:
    print("  ❌ Hisokas not found in any game!")

conn.close()

# Now test image recognition with various thresholds
print("\n" + "=" * 80)
print("Testing image recognition with different thresholds:")
print("=" * 80)

image_path = r'Current-Version\hisokas-defiance.jpg'
img = Image.open(image_path)
print(f"\nImage loaded: {img.size}")

# Compute hashes to verify image is readable
r_hash, g_hash, b_hash = scanner.compute_phash(img)
print(f"pHash computed: R={r_hash}, G={g_hash}, B={b_hash}")

# Test with increasing thresholds
for threshold in [10, 20, 30, 40, 50, 100]:
    print(f"\n  Threshold {threshold}...", end=" ", flush=True)
    results, elapsed = scanner.scan_card(img, threshold=threshold, top_n=5)
    print(f"({len(results)} matches, {elapsed:.2f}s)")
    if results:
        for i, r in enumerate(results[:3], 1):
            print(f"    {i}. {r['name']} (dist={r.get('distance', 'N/A')}, conf={r.get('confidence', 'N/A'):.1f}%)")

scanner.close()
print("\n" + "=" * 80)
