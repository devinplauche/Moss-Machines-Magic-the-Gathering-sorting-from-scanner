#!/usr/bin/env python3
"""
Regenerate MTG (game id 1) pHash DB using current scanner compute_phash pipeline.
Writes to recognition_data/phash_cards_1.db.new then attempts atomic replace.
Run in background; logs progress to stdout and debug_crops/regenerate_mtg_phash.log
"""
import sqlite3
import os
import sys
import time
import shutil
import requests
import io
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from PIL import Image

# Ensure imports for scanner
sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))
from optimized_scanner import OptimizedCardScanner

DB_PATH = Path(__file__).parent.parent / 'unified_card_database.db'
OUT_DIR = Path(__file__).parent / '..' / 'recognition_data'
OUT_DIR = OUT_DIR.resolve()
OUT_DIR.mkdir(parents=True, exist_ok=True)
PHASH_DB = OUT_DIR / 'phash_cards_1.db'
NEW_DB = OUT_DIR / 'phash_cards_1.db.new'
BACKUP = OUT_DIR / 'phash_cards_1.db.bak_full_regen'
LOG = Path(__file__).parent.parent / 'debug_crops' / 'regenerate_mtg_phash.log'
LOG.parent.mkdir(parents=True, exist_ok=True)

MAX_WORKERS = 8
REQUEST_TIMEOUT = 10

# Simple logger
def log(msg):
    ts = time.strftime('%Y-%m-%d %H:%M:%S')
    line = f"[{ts}] {msg}"
    print(line)
    with open(LOG, 'a', encoding='utf-8') as f:
        f.write(line + "\n")

# Read MTG cards list
conn = sqlite3.connect(str(DB_PATH))
cur = conn.cursor()
cur.execute("SELECT id, product_id, image_url FROM cards_1")
cards = cur.fetchall()
conn.close()

log(f"Loaded {len(cards)} MTG cards from {DB_PATH}")

# Backup existing phash DB
if PHASH_DB.exists() and not BACKUP.exists():
    try:
        shutil.copy2(PHASH_DB, BACKUP)
        log(f"Backup created: {BACKUP}")
    except Exception as e:
        log(f"Backup failed: {e}")
else:
    log("Backup exists or original PHASH DB missing; proceeding")

# Create new DB
if NEW_DB.exists():
    try:
        NEW_DB.unlink()
    except Exception:
        log('Could not remove existing new db file; will overwrite')

ph_conn = sqlite3.connect(str(NEW_DB))
ph_cur = ph_conn.cursor()
ph_cur.execute('''CREATE TABLE IF NOT EXISTS cards (
    id INTEGER PRIMARY KEY,
    product_id TEXT,
    r_phash TEXT,
    g_phash TEXT,
    b_phash TEXT,
    grayscale_phash TEXT
)''')
ph_conn.commit()

# Init scanner once (will be used for compute_phash)
scanner = OptimizedCardScanner(cache_enabled=False, enable_collection=False)

# Worker
import threading
lock = threading.Lock()

def process_card(row):
    cid, pid, url = row
    pid = str(pid)
    if not url:
        return (cid, pid, None, None, None)
    try:
        resp = requests.get(url, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        img = Image.open(io.BytesIO(resp.content))
        if img.mode != 'RGB':
            img = img.convert('RGB')
        # compute_phash accepts PIL.Image
        r,g,b = scanner.compute_phash(img)
        return (cid, pid, r, g, b)
    except Exception as e:
        return (cid, pid, None, None, None)

# Parallel processing
start = time.time()
success = 0
fail = 0
count = 0
futures = []
with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
    future_to_row = {ex.submit(process_card, row): row for row in cards}
    for fut in as_completed(future_to_row):
        count += 1
        try:
            cid, pid, r, g, b = fut.result()
        except Exception as e:
            cid, pid, r, g, b = (None, None, None, None, None)
        if r or g or b:
            try:
                ph_cur.execute('INSERT OR REPLACE INTO cards (id, product_id, r_phash, g_phash, b_phash, grayscale_phash) VALUES (?, ?, ?, ?, ?, ?)', (cid, pid, r, g, b, None))
                success += 1
            except Exception as e:
                fail += 1
        else:
            fail += 1
        if count % 200 == 0:
            ph_conn.commit()
            log(f'Progress: {count}/{len(cards)}  success={success} fail={fail}')

ph_conn.commit()
ph_conn.execute('CREATE INDEX IF NOT EXISTS idx_product_id ON cards(product_id)')
ph_conn.commit()
ph_conn.close()

elapsed = time.time() - start
log(f'Completed. Success: {success} Failed: {fail} Elapsed seconds: {round(elapsed,2)}')
scanner.close()

# Attempt atomic replace
try:
    if PHASH_DB.exists():
        try:
            PHASH_DB.unlink()
            log('Removed old PHASH DB')
        except Exception as e:
            log(f'Could not remove old PHASH DB: {e}')
    shutil.move(str(NEW_DB), str(PHASH_DB))
    log(f'Replaced PHASH DB with new file: {PHASH_DB}')
except Exception as e:
    log(f'Failed to replace PHASH DB: {e}')
    log(f'New DB left at: {NEW_DB}')

log('Regeneration complete. Please restart any running scanner processes to pick up the new DB if necessary.')
