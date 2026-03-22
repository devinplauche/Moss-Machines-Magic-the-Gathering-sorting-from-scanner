#!/usr/bin/env python3
"""Debug hamming distances between scanned image and database hash"""
import sqlite3
from pathlib import Path
import cv2
from PIL import Image
import imagehash
import sys

sys.path.insert(0, str(Path(__file__).parent))
from optimized_scanner import OptimizedCardScanner

PHASH_DB = Path(__file__).parent / "recognition_data" / "phash_cards_1.db"
SCAN_IMAGE = "../Collection/ScanImages/hisokas-defiance.jpg"

print("=" * 80)
print("HAMMING DISTANCE DEBUG")
print("=" * 80)

# Initialize scanner
scanner = OptimizedCardScanner()

# Load the scanned image
print("\n[1] Loading scanned image...")
cv_img = cv2.imread(SCAN_IMAGE)
if cv_img is None:
    print(f"  ✗ Could not load image: {SCAN_IMAGE}")
    exit(1)

pil_img = Image.open(SCAN_IMAGE)
print(f"  ✓ Loaded: {pil_img.size} {pil_img.mode}")

# Compute phash for scanned image
print("\n[2] Computing phash for scanned image...")
r_hash, g_hash, b_hash = scanner.compute_phash(cv_img)
print(f"  R: {r_hash}")
print(f"  G: {g_hash}")
print(f"  B: {b_hash}")

# Load database and compare
print(f"\n[3] Loading hashes from database: {PHASH_DB}")
phash_conn = sqlite3.connect(str(PHASH_DB))
phash_cursor = phash_conn.cursor()

phash_cursor.execute("SELECT product_id, r_phash, g_phash, b_phash FROM cards ORDER BY product_id")
db_cards = phash_cursor.fetchall()
phash_conn.close()

print(f"  ✓ Loaded {len(db_cards)} card hashes")

# Find distances for each card
print("\n[4] Computing hamming distances...")
print(f"\n  {'Product ID':15} {'Card Name':40} {'R-Dist':8} {'G-Dist':8} {'B-Dist':8} {'Avg':8} {'Conf%':8}")
print("  " + "-" * 100)

distances = []

for product_id, db_r, db_g, db_b in db_cards:
    dist_r = scanner.hamming_distance(r_hash, db_r) if db_r else 999
    dist_g = scanner.hamming_distance(g_hash, db_g) if db_g else 999
    dist_b = scanner.hamming_distance(b_hash, db_b) if db_b else 999
    
    avg_dist = (dist_r + dist_g + dist_b) / 3.0
    confidence = max(0, 100 - (avg_dist / 256 * 100))
    
    distances.append((product_id, avg_dist, confidence, dist_r, dist_g, dist_b))

# Get card names
conn = sqlite3.connect("unified_card_database.db")
cursor = conn.cursor()

distances_sorted = sorted(distances, key=lambda x: x[1])

print("\n  Top 10 closest matches:")
for product_id, avg_dist, confidence, dist_r, dist_g, dist_b in distances_sorted[:10]:
    cursor.execute("SELECT name FROM cards_1 WHERE product_id = ?", (str(product_id),))
    result = cursor.fetchone()
    name = result[0] if result else "Unknown"
    
    print(f"  {str(product_id):15} {name:40} {dist_r:8.1f} {dist_g:8.1f} {dist_b:8.1f} {avg_dist:8.1f} {confidence:8.1f}")

conn.close()

# Show thresholds
print("\n[5] Threshold analysis:")
for threshold in [5, 10, 15, 20, 25, 30, 40, 50]:
    matches = sum(1 for _, d, _, _, _, _ in distances if d <= threshold)
    print(f"  Threshold {threshold:2d}: {matches:3d} matches")

print("\n" + "=" * 80)
