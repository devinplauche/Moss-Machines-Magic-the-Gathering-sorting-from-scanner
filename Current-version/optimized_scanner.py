#!/usr/bin/env python3
"""
Optimized Universal Card Scanner
High-performance version with multi-threading, early termination, and hash indexing

Performance optimizations:
1. Multi-threaded game scanning (parallel processing)
2. Early termination when exact match found
3. Progressive threshold tightening
4. Batch SQL queries with LIMIT
5. In-memory hash caching for frequently scanned games
6. Distance pre-filtering (quick rejection of bad matches)
"""
try:
    import cv2
except Exception:
    cv2 = None
import numpy as np
import sqlite3
from pathlib import Path
import imagehash
from PIL import Image
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
import time
try:
    import serial
except Exception:
    serial = None
import os
import json
import re
import shutil
import difflib
import io
from card_filter import CardFilter
from scanner_modules.image_preprocessing import (
    MANUAL_CROP_PRESETS,
    apply_manual_crop_preset,
    detect_and_warp_card,
    ensure_image_path_exists,
)
try:
    import pytesseract
except Exception:
    pytesseract = None
try:
    import requests
except Exception:
    requests = None

# Import collection manager
from card_collection_manager import CardCollectionManager

def download_database(db_path="unified_card_database.db"):
    """Download database from server if it doesn't exist locally.

    Accepts any filename; attempts to download from configured servers
    under the `/download/<filename>` endpoint.
    """
    # If absolute path provided, use the basename for remote path but save to the full path
    target_path = Path(db_path)
    filename = target_path.name

    if target_path.exists():
        return True

    print(f"[!] Database not found at {db_path}")
    print("[*] Attempting to download from server...")

    servers = [
        "https://www.tcgtraders.app"
    ]

    for base in servers:
        server_url = f"{base}/download/{filename}"
        try:
            print(f"[*] Trying {server_url}...")
            import urllib.request
            import ssl

            if server_url.startswith("http://"):
                context = None
            else:
                context = ssl._create_unverified_context()

            def reporthook(count, block_size, total_size):
                if total_size > 0:
                    percent = int(count * block_size * 100 / total_size)
                    print(f"\r[*] Downloading: {percent}%", end='', flush=True)

            if context:
                urllib.request.urlretrieve(server_url, str(target_path), reporthook=reporthook, context=context)
            else:
                urllib.request.urlretrieve(server_url, str(target_path), reporthook=reporthook)

            print(f"\n[+] Successfully downloaded database from {server_url}")
            return True
        except Exception as e:
            print(f"\n[!] Failed to download from {server_url}: {e}")
            continue

    print("[!] Could not download database from any server")
    return False

class OptimizedCardScanner:
    def __init__(self, db_path="unified_card_database.db", max_workers=8, cache_enabled=True, 
                 serial_port=None, baud_rate=9600, use_grayscale_phash=False,
                 auto_vector_when_unfiltered=True, enable_collection=True,
                 default_condition='Near Mint', default_language='EN', default_foil=False,
                 prompt_for_details=True,
                 enable_mser_scoring=True, mser_weight=0.15,
                 enable_custom_phash_overrides=False,
                 enable_ocr_live_fast_path=False):
        """Initialize optimized scanner
        
        Args:
            default_condition: Default condition for saved cards (Near Mint, Lightly Played, etc.)
            default_language: Default language code (EN, JP, FR, etc.)
            default_foil: Default foil status (True/False)
            prompt_for_details: If False, use defaults without prompting
        """
        self.db_path = db_path
        self.max_workers = max_workers
        self.cache_enabled = cache_enabled
        
        # Collection default settings
        self.default_condition = default_condition
        self.default_language = default_language
        self.default_foil = default_foil
        self.prompt_for_details = prompt_for_details
        
        # Download database if it doesn't exist
        if not download_database(db_path):
            raise FileNotFoundError(f"Database not found and could not be downloaded: {db_path}")
        
        # Main database connection
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.cursor = self.conn.cursor()
        
        # Thread-local storage for database connections
        self.local = threading.local()
        
        # Hash cache for active game aliases (Magic-only mode)
        self.hash_cache = {}
        
        # Collection manager for saving scans
        self.collection_enabled = enable_collection
        self.collection_manager = None
        if enable_collection:
            try:
                self.collection_manager = CardCollectionManager()
                print("[+] Collection manager initialized")
            except Exception as e:
                print(f"[!] Collection manager disabled: {e}")
                self.collection_enabled = False
        
        # Load games list and find actual table names from database
        self.games = {}
        
        # Check if game_table_mapping exists for fast lookups
        try:
            mapping_exists = self.cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='game_table_mapping'"
            ).fetchone()
        except:
            mapping_exists = None
        
        # Get game list
        try:
            games_data = self.cursor.execute("SELECT id, name, display_name, total_cards FROM games").fetchall()
        except sqlite3.OperationalError:
            rows = self.cursor.execute("SELECT id, name, total_cards FROM games").fetchall()
            # normalize to 4-tuple with display_name == name
            games_data = [(r[0], r[1], r[1], r[2] if len(r) > 2 else 0) for r in rows]
        
        # If game_table_mapping exists, use it for accurate lookups
        if mapping_exists:
            mapping = {}
            for row in self.cursor.execute("SELECT game_name, table_name FROM game_table_mapping").fetchall():
                mapping[row[0]] = row[1]
            
            for game_id, name, display_name, total_cards in games_data:
                if name in mapping:
                    self.games[name] = {
                        'id': game_id,
                        'display_name': display_name,
                        'total_cards': total_cards,
                        'table': mapping[name]
                    }
        else:
            # Fallback: use cards_{id} pattern directly from games.id
            for game_id, name, display_name, total_cards in games_data:
                table_name = f'cards_{game_id}'
                # Verify table exists
                if self.cursor.execute(f"SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table_name,)).fetchone():
                    self.games[name] = {
                        'id': game_id,
                        'display_name': display_name,
                        'total_cards': total_cards,
                        'table': table_name
                    }
        
        self._enforce_magic_only_games()

        print(f"[+] Loaded {len(self.games)} game(s)")
        print("[+] Scanner mode: Magic: The Gathering only")
        print(f"[+] Max workers: {max_workers}")
        print(f"[+] Hash caching: {'enabled' if cache_enabled else 'disabled'}")
        
        # Default: scan all available Magic aliases in DB
        self.active_games = list(self.games.keys())

        
        # Serial communication for Arduino
        self.serial_port = serial_port
        self.baud_rate = baud_rate
        self.ser = None
        self.start_marker = 60  # '<'
        self.end_marker = 62    # '>'
        
        # Inventory tracking
        self.inventory_file = "Collection/Collection.txt"
        self.track_inventory = False

        # Matching parameters (can be adjusted by GUI at runtime)
        self.scan_threshold = 40
        self.quick_filter_max = 80
        # Fallback search for low-quality crops: ensures a candidate is returned for review.
        self.no_match_fallback_enabled = True
        self.no_match_fallback_threshold = 140
        
        # Performance stats
        self.stats = {
            'scans': 0,
            'total_time': 0,
            'cards_checked': 0,
            'cache_hits': 0
        }
        # Matching backend (pHash only; vector/ORB disabled)
        self.use_vector = False
        self.use_resnet50 = False
        self.use_orb = False
        self._orb = None
        self.use_grayscale_phash = use_grayscale_phash
        self.auto_vector_when_unfiltered = False
        self.vector_searcher = None
        # MSER-based quality scoring (input-only signal, blended into confidence)
        self.enable_mser_scoring = enable_mser_scoring
        self.mser_weight = float(mser_weight)
        self.manual_review_confidence_threshold = 70.0

        # Stage-1 metadata pre-filter controls (all independently toggleable)
        self.metadata_filter_config = {
            'enabled': True,
            'name': True,
            'color_identity': True,
            'cmc': True,
            'ocr_name': True,
            'ocr_cmc': True,
            'set': True,
            'collector_number': True,
            'type': True,
            'subtype': True,
        }
        self.ocr_top_ratio = 0.15
        self._ocr_ready = None
        self._ocr_status_logged = False
        self._known_set_codes = None
        self.card_filter = CardFilter(self.metadata_filter_config)
        self.metadata_cache = {}
        self.enable_custom_phash_overrides = bool(enable_custom_phash_overrides)
        self.custom_phash_cards = self._load_custom_phash_cards()
        self.on_demand_phash_cache = {}
        # GUI live camera path can force OCR-primary matching before pHash.
        self.enable_ocr_live_fast_path = bool(enable_ocr_live_fast_path)

    def _enforce_magic_only_games(self):
        """Reduce loaded game list to Magic entries only."""
        magic_games = {}
        for key, info in self.games.items():
            labels = [str(key or '').lower(), str(info.get('display_name') or '').lower()]
            if any('magic' in label for label in labels):
                magic_games[key] = info

        if not magic_games:
            raise RuntimeError("Magic: The Gathering game data was not found in the database")

        self.games = magic_games

    def _ensure_ocr_ready(self):
        """Check whether pytesseract and the tesseract binary are available."""
        if self._ocr_ready is not None:
            return self._ocr_ready

        if pytesseract is None:
            self._ocr_ready = False
            return self._ocr_ready

        tesseract_path = shutil.which('tesseract')
        if not tesseract_path:
            common_windows = [
                r"C:\Program Files\Tesseract-OCR\tesseract.exe",
                r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
            ]
            for p in common_windows:
                if os.path.exists(p):
                    tesseract_path = p
                    break
        if not tesseract_path:
            self._ocr_ready = False
            return self._ocr_ready

        try:
            pytesseract.pytesseract.tesseract_cmd = tesseract_path
            _ = pytesseract.get_tesseract_version()
            self._ocr_ready = True
        except Exception:
            self._ocr_ready = False

        return self._ocr_ready

    @staticmethod
    def _trim_white_background(image, white_threshold=244, min_shrink_ratio=0.04, padding=6):
        """Trim large white margins from scanner photos while preserving card edges."""
        if cv2 is None or image is None or not isinstance(image, np.ndarray):
            return image

        try:
            h, w = image.shape[:2]
            if h < 40 or w < 40:
                return image

            if image.ndim == 2:
                non_white = image < int(white_threshold)
            else:
                non_white = np.any(image < int(white_threshold), axis=2)

            ys, xs = np.where(non_white)
            if len(xs) == 0 or len(ys) == 0:
                return image

            x1, x2 = int(xs.min()), int(xs.max())
            y1, y2 = int(ys.min()), int(ys.max())
            crop_w = max(1, x2 - x1 + 1)
            crop_h = max(1, y2 - y1 + 1)

            # Avoid tiny/noisy crops; only trim if margins are meaningful.
            shrink_ratio = 1.0 - ((crop_w * crop_h) / float(w * h))
            if shrink_ratio < float(min_shrink_ratio):
                return image

            pad = max(0, int(padding))
            x1 = max(0, x1 - pad)
            y1 = max(0, y1 - pad)
            x2 = min(w - 1, x2 + pad)
            y2 = min(h - 1, y2 + pad)
            return image[y1:y2 + 1, x1:x2 + 1]
        except Exception:
            return image

    @staticmethod
    def _apply_ocr_gibberish_fixes(text):
        """Correct recurring OCR slips before canonical name resolution."""
        cleaned = str(text or '').strip().lower()
        if not cleaned:
            return cleaned

        cleaned = cleaned.replace('\u2019', "'").replace('\u2018', "'")
        cleaned = re.sub(r"\s+", " ", cleaned).strip()

        # Known scanner OCR mistakes seen in this dataset.
        replacements = [
            (r"\bdower\b", "power"),
            (r"\bdee\s+sey\b", "seeker"),
            (r"\bdee\b", "see"),
        ]
        for pattern, repl in replacements:
            cleaned = re.sub(pattern, repl, cleaned)

        return cleaned

    def _extract_top_band_ocr_hints(self, image):
        """Extract OCR hints (name + CMC) from the top 15% of the image using Tesseract."""
        if image is None or cv2 is None:
            return {}

        if not self._ensure_ocr_ready():
            if not self._ocr_status_logged:
                print("[!] Tesseract OCR unavailable; skipping OCR name/mana extraction")
                self._ocr_status_logged = True
            return {}

        try:
            if isinstance(image, Image.Image):
                rgb = np.array(image.convert('RGB'))
            elif isinstance(image, np.ndarray):
                if image.ndim == 3:
                    rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
                else:
                    rgb = cv2.cvtColor(image, cv2.COLOR_GRAY2RGB)
            else:
                return {}

            # Remove scanner-paper whitespace so OCR focuses on the card frame.
            rgb = self._trim_white_background(rgb, white_threshold=246, min_shrink_ratio=0.02, padding=4)

            h, w = rgb.shape[:2]
            if h < 40 or w < 40:
                return {}

            top_h = max(1, int(round(h * float(self.ocr_top_ratio))))
            top = rgb[:top_h, :]

            # Left area: expected name line.
            name_roi = top[:, :max(1, int(w * 0.72))]
            # Right area: expected mana symbols/cost.
            mana_roi = top[:, max(0, int(w * 0.62)):]

            hints = {}

            if self.metadata_filter_config.get('ocr_name', True):
                # Left 78% of the title strip = card name area (excludes mana cost on right).
                # Multi-pass preprocessing handles both dark-on-light (standard) and light-on-dark
                # (e.g. white text on gray frame) MTG card styles.
                name_strip = top[:, :max(1, int(w * 0.78))]
                ns_gray = cv2.cvtColor(name_strip, cv2.COLOR_RGB2GRAY)
                ns_gray = cv2.resize(ns_gray, None, fx=3.0, fy=3.0, interpolation=cv2.INTER_CUBIC)
                _, ns_otsu = cv2.threshold(ns_gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
                # Inverted Otsu for white-on-dark (white/yellow text on gray/black frame).
                _, ns_otsu_inv = cv2.threshold(ns_gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
                # CLAHE-enhanced version: improves local contrast for mid-tone gray backgrounds.
                clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(4, 4))
                ns_clahe = clahe.apply(ns_gray)
                _, ns_clahe_otsu = cv2.threshold(ns_clahe, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
                # Adaptive threshold: works well for mixed-contrast frames.
                ns_adaptive = cv2.adaptiveThreshold(
                    ns_gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 8
                )

                def _alpha_ratio(s):
                    alpha = sum(ch.isalpha() for ch in s)
                    return alpha / max(len(s), 1)

                candidates = []
                for roi, psm in (
                    (ns_otsu, 7), (ns_gray, 7), (ns_otsu, 6),
                    (ns_otsu_inv, 7), (ns_clahe_otsu, 7), (ns_adaptive, 7),
                ):
                    text = pytesseract.image_to_string(
                        roi,
                        config=f'--oem 3 --psm {psm}'
                    )
                    c = re.sub(r'\s+', ' ', str(text or '')).strip()
                    alpha_chars = len(re.sub(r'[^A-Za-z]', '', c))
                    if alpha_chars < 4:
                        continue
                    # Reject obvious garbage: alpha ratio too low or string too long for a card name.
                    if _alpha_ratio(c) < 0.40 or len(c) > 160:
                        continue
                    candidates.append(c)

                name = ''
                if candidates:
                    ranked = sorted(candidates, key=lambda s: (_alpha_ratio(s), sum(ch.isalpha() for ch in s)), reverse=True)
                    raw = ranked[0].replace('\u2019', "'").replace('\u2018', "'")

                    # Strategy 1: possessive title e.g. "Teferi's Protection"
                    possessive_match = re.search(r"[A-Z][A-Za-z]+'s\s+[A-Z][A-Za-z\-' ]+", raw)
                    if possessive_match:
                        name = possessive_match.group(0).strip()
                    else:
                        words = raw.split()
                        # Strip leading mana-bleed: single chars, lowercase short words
                        while words:
                            w = re.sub(r'[^A-Za-z]', '', words[0])
                            if not w or len(w) <= 1 or (w[0].islower() and len(w) <= 3):
                                words.pop(0)
                            else:
                                break
                        # Strip trailing mana noise: short all-caps, lowercase, or <=2 chars
                        while words:
                            w = re.sub(r'[^A-Za-z]', '', words[-1])
                            if not w or len(w) <= 2 or w.isupper() or (w[0].islower() and len(w) <= 4):
                                words.pop()
                            else:
                                break
                        if words:
                            name = ' '.join(words)

                # Tidy up final name: strip stray leading punctuation
                name = re.sub(r'^[^A-Za-z]+', '', name).strip()

                # Only forward names with enough alphabetic signal.
                if len(name) >= 4:
                    hints['name'] = name

            if self.metadata_filter_config.get('ocr_cmc', True):
                gray_m = cv2.cvtColor(mana_roi, cv2.COLOR_RGB2GRAY)
                gray_m = cv2.GaussianBlur(gray_m, (3, 3), 0)
                mana_text = pytesseract.image_to_string(
                    gray_m,
                    config='--oem 3 --psm 7 -c tessedit_char_whitelist=WUBRGwubrg0123456789{}()/ '
                )
                mana_text = re.sub(r'\s+', ' ', str(mana_text or '')).strip()

                cmc = None
                if mana_text:
                    # Parse MTG-like braces first, e.g. {2}{R}{R}.
                    tokens = re.findall(r'\{([^}]+)\}', mana_text)
                    if tokens:
                        total = 0
                        for tok in tokens:
                            t = tok.strip().upper()
                            if t.isdigit():
                                total += int(t)
                            elif '/' in t:
                                # Hybrid symbol counts as one mana symbol.
                                total += 1
                            elif t and t[0] in {'W', 'U', 'B', 'R', 'G', 'X', 'C'}:
                                total += 1
                        if total > 0:
                            cmc = total
                    else:
                        # Fallback: sum standalone digits and count W/U/B/R/G symbols as 1 each.
                        digits = [int(d) for d in re.findall(r'\d+', mana_text)]
                        symbols = re.findall(r'[WUBRGwubrg]', mana_text)
                        rough = sum(digits) + len(symbols)
                        if rough > 0:
                            cmc = rough

                if cmc is not None:
                    hints['cmc'] = int(cmc)

            return hints
        except Exception:
            return {}

    def _extract_bottom_band_ocr_hints(self, image):
        """Extract OCR hints (collector number/set code) from the bottom metadata strip."""
        if image is None or cv2 is None:
            return {}

        if not self._ensure_ocr_ready():
            return {}

        try:
            if isinstance(image, Image.Image):
                rgb = np.array(image.convert('RGB'))
            elif isinstance(image, np.ndarray):
                if image.ndim == 3:
                    rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
                else:
                    rgb = cv2.cvtColor(image, cv2.COLOR_GRAY2RGB)
            else:
                return {}

            h, w = rgb.shape[:2]
            if h < 40 or w < 40:
                return {}

            # Bottom 10% contains collector number + set mark/code for most MTG prints.
            strip_h = max(1, int(round(h * 0.10)))
            bottom = rgb[max(0, h - strip_h):h, :]

            gray = cv2.cvtColor(bottom, cv2.COLOR_RGB2GRAY)
            gray = cv2.GaussianBlur(gray, (3, 3), 0)
            gray = cv2.resize(gray, None, fx=2.0, fy=2.0, interpolation=cv2.INTER_CUBIC)
            thr = cv2.adaptiveThreshold(
                gray,
                255,
                cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                cv2.THRESH_BINARY,
                31,
                8,
            )

            text_blocks = []
            # Keep OCR lightweight: two targeted passes over bottom metadata strip.
            for roi, psm in ((thr, 6), (gray, 7)):
                text = pytesseract.image_to_string(roi, config=f'--oem 3 --psm {psm}')
                cleaned = re.sub(r'\s+', ' ', str(text or '')).strip()
                if cleaned:
                    text_blocks.append(cleaned)

            if not text_blocks:
                return {}

            merged = " ".join(text_blocks)
            merged = merged.replace('’', "'")

            hints = {}

            # Collector number examples: 120, 120a, 120/350, 045/280 M
            # Collector number is usually at least two digits.
            number_match = re.search(r'\b(\d{2,4}[A-Za-z]?)\s*(?:/\s*\d{2,4})?\b', merged)
            if number_match:
                hints['collector_number'] = number_match.group(1).strip().lower()

            # Set code is often 2..5 alnum chars near bottom metadata.
            tokens = re.findall(r'\b[A-Z0-9]{3,5}\b', merged.upper())
            blocked = {'TM', 'TCG', 'EN', 'JP', 'FOIL', 'MTG', 'MAGIC'}
            for tok in tokens:
                if tok in blocked:
                    continue
                if sum(ch.isalpha() for ch in tok) >= 2:
                    hints['set_code'] = tok
                    break

            return hints
        except Exception:
            return {}

    def _extract_border_referenced_ocr_hints(self, image):
        """Run OCR against a border-warped card crop when available, falling back to input image."""
        if image is None:
            return {}

        reference = image
        if isinstance(image, np.ndarray):
            warped = detect_and_warp_card(image)
            if warped is not None and warped.size > 0:
                reference = warped

        hints = {}
        hints.update(self._extract_top_band_ocr_hints(reference))
        hints.update(self._extract_bottom_band_ocr_hints(reference))
        return hints

    def _extract_full_image_ocr_name(self, image):
        """Fallback OCR pass over full card text to recover card name when band OCR fails."""
        if image is None or cv2 is None:
            return None

        if not self._ensure_ocr_ready():
            return None

        try:
            if isinstance(image, Image.Image):
                rgb = np.array(image.convert('RGB'))
            elif isinstance(image, np.ndarray):
                if image.ndim == 3:
                    rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
                else:
                    rgb = cv2.cvtColor(image, cv2.COLOR_GRAY2RGB)
            else:
                return None

            h, w = rgb.shape[:2]
            if h < 40 or w < 40:
                return None

            gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
            gray = cv2.GaussianBlur(gray, (3, 3), 0)
            gray = cv2.resize(gray, None, fx=1.6, fy=1.6, interpolation=cv2.INTER_CUBIC)
            thr = cv2.adaptiveThreshold(
                gray,
                255,
                cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                cv2.THRESH_BINARY,
                31,
                8,
            )

            extracted = []
            for roi, psm in ((gray, 6), (thr, 6)):
                text = pytesseract.image_to_string(
                    roi,
                    config=f'--oem 3 --psm {psm} -c preserve_interword_spaces=1'
                )
                if text:
                    extracted.extend(str(text).splitlines())

            if not extracted:
                return None

            for line in extracted:
                cleaned = re.sub(r"[^A-Za-z'\- ]", ' ', str(line or ''))
                cleaned = re.sub(r'\s+', ' ', cleaned).strip(" -_\n\r\t")
                if len(cleaned) < 5:
                    continue
                resolved = self._resolve_ocr_name_candidate(cleaned)
                if resolved:
                    return resolved

            return None
        except Exception:
            return None

    def _extract_full_image_ocr_text(self, image, max_chars=1800):
        """Extract broader OCR text from full image for rules-text matching fallbacks."""
        if image is None or cv2 is None:
            return None

        if not self._ensure_ocr_ready():
            return None

        try:
            if isinstance(image, Image.Image):
                rgb = np.array(image.convert('RGB'))
            elif isinstance(image, np.ndarray):
                if image.ndim == 3:
                    rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
                else:
                    rgb = cv2.cvtColor(image, cv2.COLOR_GRAY2RGB)
            else:
                return None

            gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
            gray = cv2.GaussianBlur(gray, (3, 3), 0)
            gray = cv2.resize(gray, None, fx=1.5, fy=1.5, interpolation=cv2.INTER_CUBIC)
            thr = cv2.adaptiveThreshold(
                gray,
                255,
                cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                cv2.THRESH_BINARY,
                31,
                8,
            )

            chunks = []
            for roi, psm in ((gray, 6), (thr, 6)):
                text = pytesseract.image_to_string(
                    roi,
                    config=f'--oem 3 --psm {psm} -c preserve_interword_spaces=1'
                )
                if text:
                    chunks.append(str(text))

            if not chunks:
                return None

            merged = "\n".join(chunks)
            merged = re.sub(r"\s+", " ", merged).strip()
            if len(merged) < 40:
                return None
            return merged[:max_chars]
        except Exception:
            return None

    def _resolve_ocr_rules_text_candidate(self, ocr_text, game_filter=None):
        """Resolve OCR rules text against DB descriptions when title OCR is missing."""
        if not ocr_text:
            return None

        normalized_ocr = re.sub(r"[^a-z ]", " ", str(ocr_text).lower())
        normalized_ocr = re.sub(r"\s+", " ", normalized_ocr).strip()
        if len(normalized_ocr) < 40:
            return None

        tokens = [tok for tok in re.findall(r"[a-z]{4,}", normalized_ocr)]
        if not tokens:
            return None

        # Prioritize card-rules words likely to survive OCR noise.
        prioritized = []
        for tok in ('library', 'remove', 'shuffles', 'graveyard', 'instant', 'sorcery', 'counter', 'spell', 'controller', 'name'):
            if tok in normalized_ocr:
                prioritized.append(tok)

        stopwords = {
            'that', 'this', 'with', 'from', 'then', 'your', 'their', 'into', 'until',
            'target', 'cards', 'card', 'player', 'players', 'search', 'hand', 'same',
            'game', 'when', 'where', 'each', 'and', 'the', 'for', 'all'
        }
        fallback_tokens = [tok for tok in tokens if tok not in stopwords]
        fallback_tokens = sorted(set(fallback_tokens), key=len, reverse=True)
        query_tokens = prioritized + [tok for tok in fallback_tokens if tok not in prioritized]
        query_tokens = query_tokens[:6]
        if not query_tokens:
            return None

        strong_tokens = [
            tok for tok in ('library', 'remove', 'spell', 'shuffles', 'graveyard', 'sorcery', 'instant', 'counter')
            if tok in normalized_ocr
        ]

        search_games = self._resolve_game_names([game_filter]) if game_filter else list(self.active_games)
        if not search_games:
            return None

        cursor = self.get_connection()
        candidates = []

        for game_name in search_games:
            table = self.games.get(game_name, {}).get('table')
            if not table:
                continue

            try:
                cursor.execute(f"PRAGMA table_info({table})")
                cols = [r[1] for r in cursor.fetchall()]
            except Exception:
                cols = []

            if 'description' not in cols:
                continue

            where_parts = ["description IS NOT NULL"]
            params = []
            if len(strong_tokens) >= 2:
                # Prefer selective AND constraints when OCR captured strong rules-text words.
                for tok in strong_tokens[:3]:
                    where_parts.append("lower(description) LIKE ?")
                    params.append(f"%{tok}%")
            else:
                token_clauses = []
                for tok in query_tokens:
                    token_clauses.append("lower(description) LIKE ?")
                    params.append(f"%{tok}%")
                where_parts.append(f"({' OR '.join(token_clauses)})")

            select_cols = [
                'product_id',
                'name',
                'number' if 'number' in cols else ('collector_number' if 'collector_number' in cols else 'NULL AS number'),
                'set_code' if 'set_code' in cols else ('set' if 'set' in cols else 'NULL AS set_code'),
                'rarity' if 'rarity' in cols else 'NULL AS rarity',
                'subTypeName' if 'subTypeName' in cols else 'NULL AS subTypeName',
                'market_price' if 'market_price' in cols else 'NULL AS market_price',
                'low_price' if 'low_price' in cols else 'NULL AS low_price',
                'description',
            ]

            try:
                rows = cursor.execute(
                    f"SELECT {', '.join(select_cols)} FROM {table} WHERE {' AND '.join(where_parts)} LIMIT 120",
                    params,
                ).fetchall()
            except Exception:
                rows = []

            for row in rows:
                description = str(row[8] or '')
                normalized_desc = re.sub(r"[^a-z ]", " ", description.lower())
                normalized_desc = re.sub(r"\s+", " ", normalized_desc).strip()
                if not normalized_desc:
                    continue

                score = difflib.SequenceMatcher(None, normalized_ocr, normalized_desc).ratio()
                # Old templating phrase helps separate Quash from lookalikes like Test of Talents.
                if 'remove' in normalized_ocr and 'remove' in normalized_desc:
                    score += 0.04
                if 'game' in normalized_ocr and 'game' in normalized_desc:
                    score += 0.03
                if 'shuffles' in normalized_ocr and 'shuffles' in normalized_desc:
                    score += 0.03
                if 'same name' in normalized_ocr and 'same name' in normalized_desc:
                    score += 0.03
                ocr_has_instant = 'instant' in normalized_ocr
                ocr_has_sorcery = 'sorcery' in normalized_ocr
                desc_has_instant = 'instant' in normalized_desc
                desc_has_sorcery = 'sorcery' in normalized_desc
                if ocr_has_instant and ocr_has_sorcery and desc_has_instant and desc_has_sorcery:
                    score += 0.12
                if ocr_has_instant and not desc_has_instant:
                    score -= 0.08
                if ocr_has_sorcery and not desc_has_sorcery:
                    score -= 0.08
                if (
                    'remove' in normalized_ocr
                    and 'from the game' in normalized_ocr
                    and 'remove' in normalized_desc
                    and 'from the game' in normalized_desc
                ):
                    score += 0.08

                candidates.append((score, {
                    'product_id': row[0],
                    'name': row[1],
                    'number': row[2],
                    'set_code': row[3],
                    'rarity': row[4],
                    'subTypeName': row[5],
                    'market_price': row[6],
                    'low_price': row[7],
                    'game_name': game_name,
                }))

        if not candidates:
            return None

        candidates.sort(key=lambda x: x[0], reverse=True)
        best_score, best = candidates[0]
        second_score = candidates[1][0] if len(candidates) > 1 else 0.0

        if best_score < 0.50:
            return None
        if second_score > 0 and (best_score - second_score) < 0.05:
            return None

        return best

    def _resolve_ocr_metadata_candidate(self, hints, game_filter=None):
        """Resolve OCR hints against DB with fast SQL-first strategy."""
        if not hints or not hints.get('name'):
            return None

        search_games = self._resolve_game_names([game_filter]) if game_filter else list(self.active_games)
        if not search_games:
            return None

        resolved_name = self._resolve_ocr_name_candidate(hints.get('name'))
        if not resolved_name:
            return None

        collector_hint = str(hints.get('collector_number') or '').strip().lower() or None
        set_hint = str(hints.get('set_code') or '').strip().upper() or None
        cmc_hint = hints.get('cmc')

        cursor = self.get_connection()
        candidates = []

        for game_name in search_games:
            table = self.games.get(game_name, {}).get('table')
            if not table:
                continue

            try:
                cursor.execute(f"PRAGMA table_info({table})")
                cols = [r[1] for r in cursor.fetchall()]
            except Exception:
                cols = []

            where = ["lower(name)=lower(?)"]
            params = [resolved_name]

            if set_hint:
                set_col = 'set_code' if 'set_code' in cols else ('set' if 'set' in cols else None)
                if set_col:
                    where.append(f"upper({set_col})=upper(?)")
                    params.append(set_hint)

            if collector_hint:
                num_col = 'number' if 'number' in cols else ('collector_number' if 'collector_number' in cols else None)
                if num_col:
                    where.append(f"lower({num_col})=lower(?)")
                    params.append(collector_hint)

            select_cols = [
                'product_id',
                'name',
                'number' if 'number' in cols else ('collector_number' if 'collector_number' in cols else 'NULL AS number'),
                'set_code' if 'set_code' in cols else ('set' if 'set' in cols else 'NULL AS set_code'),
                'rarity' if 'rarity' in cols else 'NULL AS rarity',
                'subTypeName' if 'subTypeName' in cols else 'NULL AS subTypeName',
                'market_price' if 'market_price' in cols else 'NULL AS market_price',
                'low_price' if 'low_price' in cols else 'NULL AS low_price',
                'cmc' if 'cmc' in cols else ('mana_value' if 'mana_value' in cols else 'NULL AS cmc'),
            ]

            try:
                rows = cursor.execute(
                    f"SELECT {', '.join(select_cols)} FROM {table} WHERE {' AND '.join(where)} LIMIT 25",
                    params,
                ).fetchall()
            except Exception:
                rows = []

            for row in rows:
                candidate = {
                    'product_id': row[0],
                    'name': row[1],
                    'number': row[2],
                    'set_code': row[3],
                    'rarity': row[4],
                    'subTypeName': row[5],
                    'market_price': row[6],
                    'low_price': row[7],
                    'cmc': row[8],
                    'game_name': game_name,
                }

                if cmc_hint is not None and candidate.get('cmc') is not None:
                    try:
                        if int(float(candidate.get('cmc'))) != int(cmc_hint):
                            continue
                    except Exception:
                        pass

                candidates.append(candidate)

        if not candidates:
            return None

        ranked = {}
        raw_name = str(hints.get('name') or '').strip().lower()
        resolved_name_l = str(resolved_name).strip().lower()
        for c in candidates:
            pid = str(c.get('product_id') or '')
            if not pid:
                continue
            score = 0
            cname = str(c.get('name') or '').strip().lower()
            if cname == resolved_name_l:
                score += 120
            if raw_name and cname == raw_name:
                score += 80
            if collector_hint and str(c.get('number') or '').strip().lower() == collector_hint:
                score += 90
            if set_hint and str(c.get('set_code') or '').strip().upper() == set_hint:
                score += 85
            if cmc_hint is not None:
                cmc = c.get('cmc')
                try:
                    if cmc is not None and int(float(cmc)) == int(cmc_hint):
                        score += 70
                except Exception:
                    pass
            prev = ranked.get(pid)
            if prev is None or score > prev[0]:
                ranked[pid] = (score, c)

        if not ranked:
            return None

        ranked_list = sorted(ranked.values(), key=lambda x: x[0], reverse=True)
        top_score = ranked_list[0][0]
        top_group = [item for item in ranked_list if item[0] == top_score]

        # Require strong metadata support for OCR-only acceptance.
        strong_enough = bool(
            top_score >= 190
            or (collector_hint and set_hint and top_score >= 170)
        )
        if strong_enough and len(top_group) == 1:
            return top_group[0][1]
        return None

    def _load_custom_phash_cards(self):
        """Load optional custom pHash card overlays from recognition_data/custom_phash_cards.json."""
        if not getattr(self, 'enable_custom_phash_overrides', False):
            return []
        path = Path(__file__).parent / 'recognition_data' / 'custom_phash_cards.json'
        if not path.exists():
            return []
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
            if isinstance(data, list):
                return data
        except Exception as e:
            print(f"[!] Failed to load custom pHash cards: {e}")
        return []

    def _custom_cards_for_game(self, game_name, game_info):
        """Return custom pHash entries that apply to a given game."""
        if not self.custom_phash_cards:
            return []
        gid = game_info.get('id')
        out = []
        for c in self.custom_phash_cards:
            c_gid = c.get('game_id')
            c_gname = c.get('game_name')
            if c_gid is not None and gid == c_gid:
                out.append(dict(c))
            elif isinstance(c_gname, str) and c_gname.lower() == str(game_name).lower():
                out.append(dict(c))
        return out

    def _load_on_demand_phash_cards(self, table, cursor, product_ids):
        """Load pHash cards on demand from unified DB image URLs for specific product_ids."""
        if not product_ids or requests is None:
            return []

        ids = [str(pid) for pid in product_ids if pid]
        table_key = str(table or '')

        def _cache_key(pid):
            return f"{table_key}:{pid}"

        to_fetch = [pid for pid in ids if _cache_key(pid) not in self.on_demand_phash_cache]
        if to_fetch:
            try:
                cursor.execute(f"PRAGMA table_info({table})")
                cols = [r[1] for r in cursor.fetchall()]
            except Exception:
                cols = []

            img_col = None
            for c in ('image_url', 'imageUrl', 'image', 'img_url'):
                if c in cols:
                    img_col = c
                    break
            if not img_col:
                return [
                    self.on_demand_phash_cache[_cache_key(pid)]
                    for pid in ids
                    if _cache_key(pid) in self.on_demand_phash_cache
                ]

            select_cols = [c for c in ('product_id', 'name', 'number', 'set_code', 'rarity', 'subTypeName', 'color', 'cmc', img_col) if c in cols]
            placeholders = ','.join(['?' for _ in to_fetch])
            try:
                rows = cursor.execute(
                    f"SELECT {', '.join(select_cols)} FROM {table} WHERE product_id IN ({placeholders})",
                    to_fetch,
                ).fetchall()
            except Exception:
                rows = []

            for row in rows:
                rec = dict(zip(select_cols, row))
                pid = str(rec.get('product_id') or '')
                if not pid:
                    continue
                image_url = rec.get(img_col)
                if not image_url:
                    continue
                try:
                    resp = requests.get(str(image_url), timeout=10)
                    resp.raise_for_status()
                    pil = Image.open(io.BytesIO(resp.content))
                    r_h, g_h, b_h = self.compute_phash(pil)
                    if not r_h or not g_h or not b_h:
                        continue
                    self.on_demand_phash_cache[_cache_key(pid)] = {
                        'product_id': pid,
                        'name': rec.get('name'),
                        'number': rec.get('number'),
                        'set_code': rec.get('set_code'),
                        'rarity': rec.get('rarity'),
                        'subTypeName': rec.get('subTypeName'),
                        'color': rec.get('color'),
                        'cmc': rec.get('cmc'),
                        'r_phash': r_h,
                        'g_phash': g_h,
                        'b_phash': b_h,
                    }
                except Exception:
                    continue

        return [
            self.on_demand_phash_cache[_cache_key(pid)]
            for pid in ids
            if _cache_key(pid) in self.on_demand_phash_cache
        ]

    def get_connection(self):
        """Get thread-local database connection"""
        if not hasattr(self.local, 'conn'):
            self.local.conn = sqlite3.connect(self.db_path, check_same_thread=False)
            self.local.cursor = self.local.conn.cursor()
        return self.local.cursor
    
    # =====================================================================
    # SERIAL COMMUNICATION (Arduino Interface)
    # =====================================================================
    
    def init_serial(self):
        """Initialize serial connection to Arduino"""
        if not self.serial_port:
            print("[!] Serial port not configured")
            return False
        
        try:
            # Set a read timeout so recv operations don't block forever
            self.ser = serial.Serial(self.serial_port, self.baud_rate, timeout=5)
            print(f"[+] Serial port {self.serial_port} opened (Baudrate: {self.baud_rate})")
            self.wait_for_arduino()
            return True
        except Exception as e:
            print(f"[!] Failed to open serial port: {e}")
            return False
    
    def send_to_arduino(self, send_str):
        """Send data to Arduino with proper encoding"""
        if not self.ser or not send_str:
            return None

        try:
            # Write the command
            self.ser.write(send_str.encode('utf-8'))
            print(f"[->] Sent to Arduino: {send_str}")

            # Wait for one delimited response from Arduino (uses recv_from_arduino)
            resp = self.recv_from_arduino()
            if resp:
                print(f"[<-] Arduino: {resp}")
            else:
                print("[!] No response received from Arduino (timeout)")

            return resp
        except Exception as e:
            print(f"[!] Error sending to Arduino: {e}")
            return None
    
    def recv_from_arduino(self):
        """Receive data from Arduino using start/end markers"""
        if not self.ser:
            return ""
        ck = ""
        started = False
        start_b = bytes([self.start_marker])
        end_b = bytes([self.end_marker])

        while True:
            b = self.ser.read(1)
            if not b:
                # timeout or no data
                break

            if not started:
                if b == start_b:
                    started = True
                # ignore until start marker
                continue

            # started == True
            if b == end_b:
                break

            try:
                ck += b.decode('utf-8', errors='replace')
            except Exception:
                # ignore decode errors
                pass

        return ck
    
    def wait_for_arduino(self):
        """Wait for Arduino ready signal"""
        if not self.ser:
            return
        
        msg = ""
        while "Arduino is ready" not in msg:
            while self.ser.in_waiting == 0:
                pass
            msg = self.recv_from_arduino()
            if msg:
                print(f"[<-] Arduino: {msg}")
    
    # =====================================================================
    # SORTING & BIN ASSIGNMENT
    # =====================================================================
    
    @staticmethod
    def is_basic_land(name):
        """Check if card is a basic land"""
        return name.lower() in {"plains", "island", "swamp", "mountain", "forest", "wastes"}
    
    @staticmethod
    def is_land_card(types):
        """Check if card has 'land' type"""
        if isinstance(types, str):
            return "land" in types.lower()
        elif isinstance(types, list):
            return any("land" in t.lower() for t in types)
        return False
    
    def get_bin_color(self, card_info):
        """Get bin based on card color"""
        types = card_info.get("type", "") or card_info.get("types", [])
        
        if self.is_land_card(types):
            name = card_info.get("name", "") or card_info.get("card_name", "")
            return "Basic land" if self.is_basic_land(name) else "Nonbasic land"
        
        # Try different color field names
        colors = (card_info.get("colors") or 
                 card_info.get("color") or 
                 card_info.get("Colors") or [])
        
        if isinstance(colors, str):
            colors = [colors]
        
        if not colors or len(colors) == 0:
            return "Colorless"
        
        return "Multicolor" if len(colors) > 1 else colors[0]
    
    def get_bin_mana(self, card_info):
        """Get bin based on mana value (CMC)"""
        mv = (card_info.get("cmc") or 
              card_info.get("mana_value") or 
              card_info.get("convertedManaCost") or 0)
        
        try:
            mv = int(float(mv))
        except:
            mv = 0
        
        if mv <= 1:
            return "One"
        elif mv <= 8:
            return str(mv).capitalize()
        else:
            return "RejectCard"
    
    def get_bin_set(self, card_info):
        """Get bin based on set code"""
        set_code = (card_info.get("set") or 
                   card_info.get("set_code") or 
                   card_info.get("setCode") or "???").lower()
        
        types = card_info.get("type", "") or card_info.get("types", [])
        
        if isinstance(types, str) and "token" in types.lower():
            return "token"
        elif isinstance(types, list) and any("token" in t.lower() for t in types):
            return "token"
        
        return set_code if set_code != "???" else "RejectCard"
    
    def get_bin_price(self, card_info, threshold=1000000):
        """Get bin based on price"""
        price_str = (card_info.get("market_price") or 
                    card_info.get("price") or 
                    card_info.get("Price") or "null")
        
        if price_str == "null" or price_str is None:
            return "RejectCard"
        
        try:
            price = float(str(price_str).strip('$'))
        except (ValueError, TypeError):
            return "RejectCard"
        
        # Price binning
        bins = {
            0.02: "tray1",
            0.05: "tray7",
            0.10: "tray14",
            0.25: "tray18",
            0.50: "tray21",
            1.0: "tray24",
            2.0: "tray25",
            4.0: "tray26",
            8.0: "tray27",
            16.0: "tray28",
            32.0: "tray29",
            64.0: "tray30",
            128.0: "tray31",
            float('inf'): "tray32"
        }
        
        for upper_limit, bin_name in bins.items():
            if price <= upper_limit and price <= threshold:
                return bin_name
        
        return "RejectCard"
    
    def get_bin_type(self, card_info):
        """Get bin based on card type"""
        types = card_info.get("type", "") or card_info.get("types", [])
        
        if isinstance(types, str):
            types = [types]
        
        type_mapping = {
            "creature": "creature",
            "artifact": "artifact",
            "enchantment": "enchantment",
            "instant": "instant",
            "sorcery": "sorcery",
            "battle": "battle",
            "planeswalker": "planeswalker",
            "land": "land",
            "token": "token"
        }
        
        for card_type in types:
            card_type_lower = str(card_type).lower()
            for key, value in type_mapping.items():
                if key in card_type_lower:
                    return value
        
        return "RejectCard"
    
    def get_bin_alpha(self, card_info):
        """Get bin based on first letter of card name (A-Z alphabetical)"""
        name = (card_info.get("name") or 
                card_info.get("card_name") or 
                card_info.get("Name") or "")
        
        if not name:
            return "RejectCard"
        
        # Get first character and convert to uppercase
        first_char = name.strip()[0].upper()
        
        # Check if it's a letter A-Z
        if first_char.isalpha():
            return first_char
        else:
            # Numbers and symbols go to a special bin
            return "0-9"
    
    def get_bin_rarity(self, card_info):
        """Get bin based on card rarity"""
        rarity = (card_info.get("rarity") or 
                 card_info.get("Rarity") or "").lower()
        
        if not rarity:
            return "RejectCard"
        
        # Standard rarity bins
        rarity_mapping = {
            "common": "Common",
            "uncommon": "Uncommon",
            "rare": "Rare",
            "mythic": "Mythic",
            "mythic rare": "Mythic",
            "special": "Special",
            "bonus": "Bonus",
            "promo": "Promo",
            "token": "Token"
        }
        
        for key, value in rarity_mapping.items():
            if key in rarity:
                return value
        
        return "RejectCard"
    
    def get_bin_finish(self, card_info):
        """Get bin based on foil/nonfoil finish"""
        # Check various foil field names across different games
        finishes = card_info.get("finishes") or card_info.get("finish") or []
        is_foil = card_info.get("foil") or card_info.get("isFoil") or False
        
        # Handle different data structures
        if isinstance(finishes, list):
            has_foil = any("foil" in str(f).lower() for f in finishes)
            has_nonfoil = any("nonfoil" in str(f).lower() or "normal" in str(f).lower() for f in finishes)
            
            if has_foil and has_nonfoil:
                return "Both"
            elif has_foil:
                return "Foil"
            elif has_nonfoil:
                return "Nonfoil"
        
        # Simple boolean check
        if isinstance(is_foil, bool):
            return "Foil" if is_foil else "Nonfoil"
        
        # String check
        if isinstance(is_foil, str):
            return "Foil" if is_foil.lower() in ["true", "yes", "foil"] else "Nonfoil"
        
        return "Nonfoil"  # Default to nonfoil if unknown
    
    def get_bin_number(self, card_info, mode, threshold=1000000):
        """
        Get bin number for card based on sorting mode
        Modes: color, mana_value, set, price, type, buy, alpha, rarity, finish
        """
        if not card_info or card_info == "RejectCard":
            return "RejectCard"
        
        if mode == "color":
            return self.get_bin_color(card_info)
        elif mode == "mana_value":
            return self.get_bin_mana(card_info)
        elif mode == "set":
            return self.get_bin_set(card_info)
        elif mode == "price":
            return self.get_bin_price(card_info, 1000000)
        elif mode == "type":
            return self.get_bin_type(card_info)
        elif mode == "buy":
            return self.get_bin_price(card_info, threshold)
        elif mode == "alpha":
            return self.get_bin_alpha(card_info)
        elif mode == "rarity":
            return self.get_bin_rarity(card_info)
        elif mode == "finish":
            return self.get_bin_finish(card_info)
        else:
            return "RejectCard"
    
    # =====================================================================
    # INVENTORY TRACKING
    # =====================================================================
    
    def enable_inventory_tracking(self, enable=True):
        """Enable or disable inventory tracking"""
        self.track_inventory = enable
        if enable:
            # Ensure directory exists
            os.makedirs(os.path.dirname(self.inventory_file), exist_ok=True)
            # Create file if it doesn't exist
            if not os.path.exists(self.inventory_file):
                open(self.inventory_file, 'w').close()
            print(f"[+] Inventory tracking enabled: {self.inventory_file}")
    
    def check_inventory(self, card_info):
        """
        Check if card is already in inventory
        Returns "RejectCard" if found, otherwise adds it and returns card_info
        """
        if not self.track_inventory:
            return card_info
        
        card_str = str(card_info)
        
        try:
            # Read existing inventory
            with open(self.inventory_file, 'r') as f:
                if card_str in f.read():
                    print('[*] Card already in inventory - rejecting')
                    return "RejectCard"
            
            # Add to inventory
            with open(self.inventory_file, 'a') as f:
                f.write(card_str + "\n")
            
            print('[+] Card added to inventory')
            return card_info
        
        except Exception as e:
            print(f"[!] Inventory check error: {e}")
            return card_info
    

    def set_active_games(self, game_names):
        """Limit scanning to specific games"""
        resolved = self._resolve_game_names(game_names)
        if resolved:
            self.active_games = resolved
        else:
            print("[!] No requested games matched database names; keeping previous active game list")
        print(f"[+] Active games: {len(self.active_games)}")

    def _resolve_game_names(self, game_names):
        """Resolve user-provided labels, but keep scanning locked to Magic-only entries."""
        magic_keys = list(self.games.keys())
        if not game_names:
            return magic_keys

        if isinstance(game_names, str):
            requested = [game_names]
        else:
            requested = [g for g in game_names if g]

        invalid = []
        for raw in requested:
            token = str(raw).strip().lower()
            if not token:
                continue
            if token in {'magic', 'magic: the gathering', 'mtg'}:
                continue
            if 'magic' in token:
                continue
            invalid.append(raw)

        if invalid:
            print(f"[!] Ignoring non-Magic game filters: {invalid}")

        return magic_keys
    
    def preload_cache(self, games=None):
        """Preload hash cache for specified games (WARNING: memory intensive)"""
        if not self.cache_enabled:
            return

        if games is None:
            games = list(self.games.keys())
        
        print("\n[*] Preloading hash cache...")
        for game_name in games:
            if game_name not in self.games:
                continue
            
            print(f"    Loading {game_name}...", end='', flush=True)
            start = time.time()
            
            table = self.games[game_name]['table']
            try:
                # Use optimized SELECT with only needed columns for faster loading
                needed_cols = "product_id, name, number, r_phash, g_phash, b_phash, set_code, rarity, subTypeName, market_price, low_price"
                try:
                    query = f"SELECT {needed_cols} FROM {table} WHERE r_phash IS NOT NULL"
                    rows = self.cursor.execute(query).fetchall()
                    colnames = [d[0] for d in self.cursor.description]
                    # convert to list of dicts for robust column-name access
                    cards = [dict(zip(colnames, row)) for row in rows]
                except sqlite3.OperationalError:
                    print(f" Table not found")
                    cards = []

                self.hash_cache[game_name] = cards
                
                elapsed = time.time() - start
                print(f" {len(cards):,} cards loaded in {elapsed:.2f}s")
            except sqlite3.OperationalError:
                print(f" Table not found")
    
    def compute_phash(self, image):
        """Compute perceptual hash (256-bit, hash_size=16).

        Behavior aligned with `scripts/recompute_phashes_square.py`:
        - Rotate image 90deg if width > height
        - Resize to fit within a 1024x1024 square preserving aspect ratio
        - Paste centered onto a white 1024x1024 background (letterbox)
        - Compute per-channel pHash with hash_size=16 and normalize to 64-hex
        Originals are not modified.
        """
        if isinstance(image, np.ndarray):
            rgb_image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
            pil_image = Image.fromarray(rgb_image)
        else:
            pil_image = image

        # Check if image is None
        if pil_image is None:
            return None, None, None

        if pil_image.mode != 'RGB':
            pil_image = pil_image.convert('RGB')

        # Rotate to portrait if necessary
        if pil_image.width > pil_image.height:
            pil_image = pil_image.transpose(Image.ROTATE_90)

        size = 1024
        w, h = pil_image.size
        if w == 0 or h == 0:
            return None, None, None

        scale = min(float(size) / w, float(size) / h)
        new_w = max(1, int(round(w * scale)))
        new_h = max(1, int(round(h * scale)))
        im_resized = pil_image.resize((new_w, new_h), Image.LANCZOS)
        bg = Image.new('RGB', (size, size), (255, 255, 255))
        offset = ((size - new_w) // 2, (size - new_h) // 2)
        bg.paste(im_resized, offset)
        sq = bg

        def _norm(phash_obj):
            if not phash_obj:
                return None
            s = str(phash_obj)
            if s.startswith('0x'):
                s = s[2:]
            s = s.lower()
            return s.ljust(64, '0')[:64]

        if self.use_grayscale_phash:
            try:
                h = imagehash.phash(sq.convert('L'), hash_size=16)
                nh = _norm(h)
                return nh, nh, nh
            except Exception:
                return None, None, None

        r, g, b = sq.split()
        try:
            r_h = _norm(imagehash.phash(r, hash_size=16))
            g_h = _norm(imagehash.phash(g, hash_size=16))
            b_h = _norm(imagehash.phash(b, hash_size=16))
        except Exception:
            return None, None, None

        return r_h, g_h, b_h

    def _compute_mser_score(self, image):
        """Compute a lightweight MSER quality score for the input image (0..1)."""
        if not self.enable_mser_scoring or cv2 is None:
            return None

        try:
            if isinstance(image, Image.Image):
                img = np.array(image)
                if img.ndim == 3:
                    gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
                else:
                    gray = img
            elif isinstance(image, np.ndarray):
                if image.ndim == 3:
                    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
                else:
                    gray = image
            else:
                return None

            h, w = gray.shape[:2]
            if h < 50 or w < 50:
                return None

            mser = cv2.MSER_create()
            regions, _ = mser.detectRegions(gray)
            if not regions:
                return 0.0

            img_area = float(h * w)
            # Estimate region area mass
            area_sum = 0.0
            for p in regions:
                if p is None or len(p) < 5:
                    continue
                try:
                    area_sum += float(cv2.contourArea(p.reshape(-1, 1, 2)))
                except Exception:
                    continue

            region_count = len(regions)
            area_ratio = area_sum / img_area if img_area > 0 else 0.0

            # Normalize metrics into 0..1 range (tuned for card-sized crops)
            def _norm(val, vmin, vmax):
                if vmax <= vmin:
                    return 0.0
                return max(0.0, min(1.0, (val - vmin) / (vmax - vmin)))

            # Typical MSER counts on card crops are in the hundreds
            count_score = _norm(region_count, 120, 1200)
            # Area mass ratio for text/edges tends to be small but non-zero
            area_score = _norm(area_ratio, 0.008, 0.18)

            score = 0.7 * count_score + 0.3 * area_score
            return max(0.0, min(1.0, score))

        except Exception:
            return None

    def hamming_distance(self, hash1, hash2):
        """Fast Hamming distance calculation"""
        if not hash1 or not hash2:
            return 999
        
        try:
            return bin(int(hash1, 16) ^ int(hash2, 16)).count('1')
        except:
            return 999
    
    def quick_filter(self, hash1, hash2, max_dist=None):
        """
        Quick rejection filter using single channel
        Returns True if worth checking all channels
        """
        if max_dist is None:
            max_dist = getattr(self, 'quick_filter_max', 80)
        dist = self.hamming_distance(hash1, hash2)
        return dist <= max_dist

    def _load_game_metadata_cache(self, game_name, table, cursor):
        """Load lightweight metadata used by Stage-1 prefilter and cache it by game."""
        if game_name in self.metadata_cache:
            return self.metadata_cache[game_name]

        try:
            cursor.execute(f"PRAGMA table_info({table})")
            cols = {r[1] for r in cursor.fetchall()}

            def col_expr(preferred, alias):
                for c in preferred:
                    if c in cols:
                        return f"{c} AS {alias}"
                return f"NULL AS {alias}"

            select_sql = ", ".join([
                col_expr(['product_id'], 'product_id'),
                col_expr(['name', 'card_name'], 'name'),
                col_expr(['color', 'colors'], 'color'),
                col_expr(['cmc', 'converted_cost', 'mana_value', 'convertedManaCost'], 'cmc'),
                col_expr(['set_code', 'set'], 'set_code'),
                col_expr(['number', 'collector_number'], 'number'),
                col_expr(['type', 'card_type', 'full_type'], 'type'),
                col_expr(['subTypeName', 'subtype'], 'subTypeName'),
            ])

            rows = cursor.execute(f"SELECT {select_sql} FROM {table}").fetchall()
            meta = [
                {
                    'product_id': str(r[0]),
                    'name': r[1],
                    'color': r[2],
                    'cmc': r[3],
                    'set_code': r[4],
                    'number': r[5],
                    'type': r[6],
                    'subTypeName': r[7],
                }
                for r in rows
                if r and r[0] is not None
            ]
            # Include metadata for custom overlay cards so Stage-1 can score/filter them.
            game_info = self.games.get(game_name, {})
            for c in self._custom_cards_for_game(game_name, game_info):
                pid = c.get('product_id')
                if pid is None:
                    continue
                meta.append({
                    'product_id': str(pid),
                    'name': c.get('name'),
                    'color': c.get('color'),
                    'cmc': c.get('cmc'),
                    'set_code': c.get('set_code'),
                    'number': c.get('number'),
                    'type': c.get('type'),
                    'subTypeName': c.get('subTypeName'),
                })

            self.metadata_cache[game_name] = meta
            return meta
        except Exception:
            self.metadata_cache[game_name] = []
            return []

    def _detect_color_identity(self, image):
        """Best-effort color identity hint from top-right mana-pip region."""
        if cv2 is None or image is None:
            return None

        try:
            if isinstance(image, Image.Image):
                arr = np.array(image.convert('RGB'))
                bgr = cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
            elif isinstance(image, np.ndarray):
                bgr = image
            else:
                return None

            h, w = bgr.shape[:2]
            if h < 50 or w < 50:
                return None

            y1, y2 = int(h * 0.02), int(h * 0.16)
            x1, x2 = int(w * 0.62), int(w * 0.96)
            roi = bgr[max(0, y1):max(y1 + 1, y2), max(0, x1):max(x1 + 1, x2)]
            if roi.size == 0:
                return None

            hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
            H, S, V = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]

            def ratio(mask):
                return float(np.count_nonzero(mask)) / float(mask.size)

            detected = []
            if ratio((S < 55) & (V > 170)) > 0.10:
                detected.append('W')
            if ratio((H >= 90) & (H <= 135) & (S > 60)) > 0.08:
                detected.append('U')
            if ratio((V < 70) & (S < 85)) > 0.08:
                detected.append('B')
            if ratio((((H <= 15) | (H >= 165)) & (S > 80) & (V > 70)) > 0.06):
                detected.append('R')
            if ratio((H >= 35) & (H <= 85) & (S > 70) & (V > 60)) > 0.08:
                detected.append('G')

            detected = sorted(set(detected))
            if not detected:
                return 'colorless'
            if len(detected) > 1:
                return 'multi'
            return detected[0]
        except Exception:
            return None

    def _detect_cmc(self, image):
        """Best-effort CMC hint from counting round mana symbols in top-right region."""
        if cv2 is None or image is None:
            return None

        try:
            if isinstance(image, Image.Image):
                arr = np.array(image.convert('RGB'))
                bgr = cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
            elif isinstance(image, np.ndarray):
                bgr = image
            else:
                return None

            h, w = bgr.shape[:2]
            y1, y2 = int(h * 0.02), int(h * 0.18)
            x1, x2 = int(w * 0.55), int(w * 0.96)
            roi = bgr[max(0, y1):max(y1 + 1, y2), max(0, x1):max(x1 + 1, x2)]
            if roi.size == 0:
                return None

            gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
            gray = cv2.GaussianBlur(gray, (5, 5), 0)
            circles = cv2.HoughCircles(
                gray,
                cv2.HOUGH_GRADIENT,
                dp=1.2,
                minDist=12,
                param1=80,
                param2=20,
                minRadius=4,
                maxRadius=20,
            )
            if circles is None:
                return None
            count = int(circles.shape[1])
            if count <= 0 or count > 12:
                return None
            return min(count, 20)
        except Exception:
            return None

    def _build_metadata_hints(self, image, metadata_hints, set_filter=None):
        hints = dict(metadata_hints or {})
        skip_ocr = bool(hints.pop('_skip_ocr', False))
        resolved_name = None

        # Primary OCR pass on the current crop; only fall back to border-referenced OCR when needed.
        ocr_hints = {}
        if not skip_ocr:
            top_hints = self._extract_top_band_ocr_hints(image)
            bottom_hints = self._extract_bottom_band_ocr_hints(image)
            ocr_hints.update(top_hints)
            ocr_hints.update(bottom_hints)

            has_primary_signal = bool(
                top_hints.get('name')
                or bottom_hints.get('collector_number')
                or bottom_hints.get('set_code')
            )
            if not has_primary_signal:
                border_hints = self._extract_border_referenced_ocr_hints(image)
                for key, value in border_hints.items():
                    if ocr_hints.get(key) is None and value is not None:
                        ocr_hints[key] = value

        if 'name' not in hints and ocr_hints.get('name'):
            resolved_name = self._resolve_ocr_name_candidate(ocr_hints.get('name'))
            if resolved_name:
                hints['name'] = resolved_name

        # Full-image OCR name fallback is intentionally disabled:
        # reading card body text misidentifies card names and produces wrong
        # metadata hints that poison the Stage-1 filter (→ DB fallback → wrong pHash).
        # Top-band OCR (above) is the only name source we trust.
        if 'cmc' not in hints and ocr_hints.get('cmc') is not None:
            hints['cmc'] = ocr_hints['cmc']
        if 'collector_number' not in hints and ocr_hints.get('collector_number'):
            hints['collector_number'] = ocr_hints['collector_number']
        if 'set_code' not in hints and ocr_hints.get('set_code'):
            set_code = str(ocr_hints.get('set_code') or '').strip().upper()
            # Only trust OCR set code when accompanied by stronger OCR evidence.
            if self._is_known_set_code(set_code) and (resolved_name or hints.get('collector_number')):
                hints['set_code'] = set_code

        if set_filter and len(set_filter) == 1 and 'set_code' not in hints:
            hints['set_code'] = set_filter[0]

        if 'color_identity' not in hints and self.metadata_filter_config.get('color_identity', True):
            detected_color = self._detect_color_identity(image)
            if detected_color:
                hints['color_identity'] = detected_color

        if 'cmc' not in hints and self.metadata_filter_config.get('cmc', True):
            detected_cmc = self._detect_cmc(image)
            # CMC circle detection is noisy; only use it when other metadata exists.
            if detected_cmc is not None and (hints.get('name') or hints.get('set_code') or hints.get('collector_number')):
                hints['cmc'] = detected_cmc

        return hints

    def _is_known_set_code(self, set_code):
        """Validate that a set code exists in loaded Magic DB tables."""
        code = str(set_code or '').strip().upper()
        if not code or len(code) < 2 or len(code) > 6:
            return False

        if self._known_set_codes is None:
            known = set()
            cursor = self.get_connection()
            for game_name in self.active_games:
                table = self.games.get(game_name, {}).get('table')
                if not table:
                    continue
                try:
                    rows = cursor.execute(
                        f"SELECT DISTINCT set_code FROM {table} WHERE set_code IS NOT NULL"
                    ).fetchall()
                except Exception:
                    rows = []
                for row in rows:
                    val = str(row[0] or '').strip().upper()
                    if val:
                        known.add(val)
            self._known_set_codes = known

        return code in self._known_set_codes

    def _resolve_ocr_name_candidate(self, ocr_name):
        """Resolve noisy OCR name text to a likely canonical card name."""
        raw = str(ocr_name or '').strip()
        if not raw:
            return None

        raw = raw.replace('\u2019', "'").replace('\u2018', "'")
        raw = re.sub(r"\s+", " ", raw).strip()
        raw = re.sub(r"^[^A-Za-z]+", "", raw).strip()
        if len(raw) < 3:
            return None

        fixed_raw = self._apply_ocr_gibberish_fixes(raw)
        raw_variants = []
        for value in (raw, fixed_raw):
            v = str(value or '').strip()
            if v and v not in raw_variants:
                raw_variants.append(v)
        raw_variants_l = [v.lower() for v in raw_variants]
        raw_l = raw_variants_l[0]

        # Prefer custom overlay names first (useful for local custom cards).
        custom_names = [str(c.get('name')) for c in self.custom_phash_cards if c.get('name')]
        if custom_names:
            best_custom = max(custom_names, key=lambda n: difflib.SequenceMatcher(None, raw_l, n.lower()).ratio())
            if difflib.SequenceMatcher(None, raw_l, best_custom.lower()).ratio() >= 0.72:
                return best_custom

        candidates = set()

        try:
            cursor = self.get_connection()

            # 1) Fast exact-name lookup (case-insensitive) across active games.
            for game_name in self.active_games:
                table = self.games.get(game_name, {}).get('table')
                if not table:
                    continue
                try:
                    for query_name in raw_variants:
                        row = cursor.execute(
                            f"SELECT name FROM {table} WHERE lower(name)=lower(?) LIMIT 1",
                            (query_name,),
                        ).fetchone()
                        if row and row[0]:
                            return str(row[0])
                except Exception:
                    continue

            # 2) Broader token-based candidate collection.
            words = []
            for rv in raw_variants_l:
                words.extend(re.findall(r"[A-Za-z']+", rv))
            words = [w for w in words if len(w) >= 3]
            if not words:
                return None

            # Search by up to 4 strongest tokens to avoid missing common names.
            tokens = sorted(set(words), key=len, reverse=True)[:4]

            for token in tokens:
                for game_name in self.active_games:
                    table = self.games.get(game_name, {}).get('table')
                    if not table:
                        continue
                    try:
                        rows = cursor.execute(
                            f"SELECT name FROM {table} WHERE lower(name) LIKE ? LIMIT 80",
                            (f"%{token}%",),
                        ).fetchall()
                    except Exception:
                        rows = []
                    for r in rows:
                        if r and r[0]:
                            candidates.add(str(r[0]))
                    if len(candidates) >= 600:
                        break
                if len(candidates) >= 600:
                    break
        except Exception:
            pass

        if not candidates:
            return None

        raw_words = set()
        for rv in raw_variants_l:
            raw_words.update(re.findall(r"[A-Za-z']+", rv))

        def _best_ratio(candidate_name):
            n_l = str(candidate_name or '').strip().lower()
            if not n_l:
                return 0.0
            return max(
                difflib.SequenceMatcher(None, rv, n_l).ratio()
                for rv in raw_variants_l
            )

        def _fuzzy_token_overlap(raw_tokens, candidate_tokens):
            if not raw_tokens or not candidate_tokens:
                return 0
            score = 0
            used = set()
            for rw in raw_tokens:
                if len(rw) < 3:
                    continue
                best_idx = None
                best_ratio = 0.0
                for idx, cw in enumerate(candidate_tokens):
                    if idx in used:
                        continue
                    ratio = difflib.SequenceMatcher(None, rw, cw).ratio()
                    if ratio > best_ratio:
                        best_ratio = ratio
                        best_idx = idx
                if best_idx is not None and best_ratio >= 0.72:
                    used.add(best_idx)
                    score += 1
            return score

        def _score_name(name):
            n = str(name or '').strip()
            n_l = n.lower()
            ratio = _best_ratio(n_l)
            n_words = re.findall(r"[A-Za-z']+", n_l)
            overlap = len(raw_words & set(n_words))
            fuzzy_overlap = _fuzzy_token_overlap(list(raw_words), n_words)
            first_token = raw_l.split(' ')[0] if raw_l else ''
            prefix = 1 if first_token and n_l.startswith(first_token) else 0
            return (ratio, fuzzy_overlap, overlap, prefix, -abs(len(n_l) - len(raw_l)))

        best = max(candidates, key=_score_name)
        score = _score_name(best)

        # Accept with a balanced threshold: either good global similarity,
        # or moderate similarity with strong token overlap.
        if score[0] >= 0.72:
            return best
        if score[0] >= 0.60 and (score[1] >= 2 or score[2] >= 2):
            return best
        if score[0] >= 0.52 and score[1] >= 2:
            return best
        return None

    def _apply_stage1_prefilter(self, game_name, table, cards, cursor, metadata_hints):
        """Return filtered pHash cards, per-card metadata match scores, and diagnostics."""
        diagnostics = {
            'stage1_initial_count': len(cards),
            'stage1_final_count': len(cards),
            'stage1_filters_applied': [],
            'stage1_fallback_to_full_db': False,
        }

        if not self.metadata_filter_config.get('enabled', True):
            return cards, {}, diagnostics

        metadata_rows = self._load_game_metadata_cache(game_name, table, cursor)
        if not metadata_rows:
            return cards, {}, diagnostics

        filtered_rows, filter_diag, match_scores = self.card_filter.apply(metadata_rows, metadata_hints)
        diagnostics['stage1_filters_applied'] = filter_diag.get('applied_filters', [])

        if filter_diag.get('filters_used', 0) > 0 and not filtered_rows:
            # Stage-1 metadata hints filtered out every candidate.  Falling back
            # to the full DB pHash scan produces consistently wrong results because
            # noisy OCR hints (color, CMC) eliminate the real card while leaving
            # unrelated cards to match on pHash alone.
            # → Return NO_MATCH so the caller can surface a low-confidence review
            #   item rather than a confidently wrong card name.
            diagnostics['stage1_fallback_to_full_db'] = False
            diagnostics['stage1_final_count'] = 0
            if metadata_hints and not metadata_hints.get('name'):
                print(f"[!] Stage 1 over-filtered for {game_name}; returning no-match (hints: {list((metadata_hints or {}).keys())})")
            return [], match_scores, diagnostics

        if not filtered_rows:
            return cards, {}, diagnostics

        allowed_ids = {str(r.get('product_id')) for r in filtered_rows if r.get('product_id') is not None}
        if not allowed_ids:
            return cards, {}, diagnostics

        filtered_cards = [c for c in cards if str(c.get('product_id')) in allowed_ids]
        diagnostics['stage1_final_count'] = len(filtered_cards)
        return filtered_cards, match_scores, diagnostics

    
    def scan_game(self, game_name, r_hash, g_hash, b_hash, threshold, found_exact,
                  set_filter=None, foil_type_filter=None, rarity_filter=None,
                  metadata_hints=None):
        """
        Scan a single game (runs in thread)
        Returns list of matches from this game
        
        Args:
            set_filter: List of set codes to filter by (None = all sets)
            foil_type_filter: List of foil types (subTypeName values) to filter by (None = all types)
            rarity_filter: List of rarity codes to filter by (None = all rarities)
        """
        if found_exact.is_set():
            return []  # Another thread found exact match, abort
        
        cursor = self.get_connection()
        matches = []
        
        game_info = self.games[game_name]
        table = game_info['table']
        
        try:
            # Always load pHash data from per-game pHash DB (no fallbacks)
            if game_name in self.hash_cache:
                cards = list(self.hash_cache[game_name])
                self.stats['cache_hits'] += 1
            else:
                gid = str(game_info.get('id'))
                phash_db = Path(__file__).parent / 'recognition_data' / f"phash_cards_{gid}.db"
                if not phash_db.exists():
                    cards = []
                else:
                    ph_conn = sqlite3.connect(str(phash_db))
                    ph_cur = ph_conn.cursor()
                    ph_cur.execute("PRAGMA table_info(cards)")
                    ph_cols = [r[1] for r in ph_cur.fetchall()]
                    wanted = [c for c in ('product_id', 'r_phash', 'g_phash', 'b_phash', 'grayscale_phash') if c in ph_cols]
                    if 'product_id' not in wanted:
                        ph_conn.close()
                        cards = []
                    else:
                        ph_cur.execute(f"SELECT {', '.join(wanted)} FROM cards")
                        rows = ph_cur.fetchall()
                        ph_conn.close()
                        cards = [dict(zip(wanted, row)) for row in rows]
                        if self.cache_enabled:
                            self.hash_cache[game_name] = list(cards)

            # Merge custom overlay cards for this game.
            cards.extend(self._custom_cards_for_game(game_name, game_info))
            if not cards:
                return []

            # Stage 1: metadata pre-filter to reduce pHash candidate pool
            cards, metadata_match_scores, stage1_diag = self._apply_stage1_prefilter(
                game_name,
                table,
                cards,
                cursor,
                metadata_hints,
            )

            # Database-driven fallback: hydrate pHash for filtered candidates via image_url when missing.
            if not cards and metadata_hints and metadata_hints.get('name'):
                candidate_ids = [
                    pid for pid, score in (metadata_match_scores or {}).items()
                    if score > 0 and isinstance(pid, str) and not pid.startswith('idx:')
                ][:25]
                if candidate_ids:
                    cards = self._load_on_demand_phash_cards(table, cursor, candidate_ids)
            
            # Scan cards with early termination
            match_count = 0
            max_matches_per_game = 50  # Stop after finding 50 good matches per game to speed up multi-game scans
            
            candidate_ids = []
            candidate_rows = []
            for card in cards:
                if found_exact.is_set():
                    break  # Exact match found by another thread
                
                # Early exit if we have enough matches from this game
                if match_count >= max_matches_per_game:
                    break

                # Card is a dict (from pHash DB)
                product_id = card.get('product_id')
                if product_id is not None:
                    product_id = str(product_id)
                name = card.get('name') or 'Unknown'
                number = card.get('number')
                if self.use_grayscale_phash:
                    gray = card.get('grayscale_phash')
                    card_r = gray
                    card_g = gray
                    card_b = gray
                else:
                    card_r = card.get('r_phash')
                    card_g = card.get('g_phash')
                    card_b = card.get('b_phash')
                set_code = card.get('set_code') or card.get('set')
                rarity = card.get('rarity')
                subTypeName = card.get('subTypeName')
                market_price = card.get('market_price')
                low_price = card.get('low_price')

                # Quick filter on R channel first (cheapest check)
                if not self.quick_filter(r_hash, card_r, threshold * 3):
                    continue

                # Calculate full distance
                dist_r = self.hamming_distance(r_hash, card_r)
                dist_g = self.hamming_distance(g_hash, card_g)
                dist_b = self.hamming_distance(b_hash, card_b)

                avg_distance = (dist_r + dist_g + dist_b) / 3.0

                if avg_distance <= threshold:
                    confidence = max(0, 100 - (avg_distance / 256 * 100))

                    # Price fallback: prefer market_price/price, then low_price variants
                    price_value = None
                    try:
                        if market_price is not None and float(market_price) > 0:
                            price_value = float(market_price)
                        elif low_price is not None and float(low_price) > 0:
                            price_value = float(low_price)
                    except Exception:
                        price_value = None

                    candidate_ids.append(product_id)
                    candidate_rows.append({
                        'product_id': product_id,
                        'game': game_info['display_name'],
                        'name': name,
                        'number': number,
                        'set': set_code,
                        'rarity': rarity,
                        'foil_type': subTypeName,
                        'market_price': price_value,
                        'distance': avg_distance,
                        'confidence': confidence,
                        'dist_r': dist_r,
                        'dist_g': dist_g,
                        'dist_b': dist_b,
                        'metadata_match_score': metadata_match_scores.get(str(product_id), 0),
                        'stage1_filters_applied': stage1_diag.get('stage1_filters_applied', []),
                        'stage1_fallback_to_full_db': stage1_diag.get('stage1_fallback_to_full_db', False),
                        'stage1_initial_count': stage1_diag.get('stage1_initial_count', len(cards)),
                        'stage1_final_count': stage1_diag.get('stage1_final_count', len(cards)),
                    })
                    
                    match_count += 1

                    # Exact match found!
                    if avg_distance == 0:
                        found_exact.set()
                        break
            
            # Enrich candidates with unified metadata
            meta_map = {}
            if candidate_ids and table:
                try:
                    placeholders = ','.join(['?' for _ in candidate_ids])
                    cur = cursor
                    cur.execute(
                        f"SELECT product_id, name, number, set_code, set_name, rarity, subTypeName, market_price, low_price, color "
                        f"FROM {table} WHERE product_id IN ({placeholders})",
                        candidate_ids
                    )
                    for row in cur.fetchall():
                        meta_map[str(row[0])] = {
                            'name': row[1],
                            'number': row[2],
                            'set_code': row[3],
                            'set_name': row[4],
                            'rarity': row[5],
                            'subTypeName': row[6],
                            'market_price': row[7],
                            'low_price': row[8],
                            'color': row[9]
                        }
                except Exception:
                    meta_map = {}

            for m in candidate_rows:
                pid = m.get('product_id')
                if pid is not None:
                    pid = str(pid)
                meta = meta_map.get(pid, {})
                if meta:
                    m['name'] = meta.get('name') or m['name']
                    m['number'] = meta.get('number') or m['number']
                    m['set'] = meta.get('set_code') or m.get('set')
                    m['set_name'] = meta.get('set_name')
                    m['rarity'] = meta.get('rarity') or m.get('rarity')
                    m['foil_type'] = meta.get('subTypeName') or m.get('foil_type')
                    if meta.get('color') is not None:
                        m['color'] = meta.get('color')
                    price_value = None
                    try:
                        if meta.get('market_price') is not None and float(meta.get('market_price')) > 0:
                            price_value = float(meta.get('market_price'))
                        elif meta.get('low_price') is not None and float(meta.get('low_price')) > 0:
                            price_value = float(meta.get('low_price'))
                    except Exception:
                        price_value = m.get('market_price')
                    m['market_price'] = price_value

                # Apply filters after metadata
                if set_filter and m.get('set'):
                    if m.get('set').upper() not in [s.upper() for s in set_filter]:
                        continue
                if foil_type_filter and m.get('foil_type'):
                    if m.get('foil_type') not in foil_type_filter:
                        continue
                if rarity_filter and m.get('rarity'):
                    if m.get('rarity').upper() not in [r.upper() for r in rarity_filter]:
                        continue

                matches.append(m)

            self.stats['cards_checked'] += len(cards)
        
        except sqlite3.OperationalError:
            pass
        
        return matches
    
    def scan_card(self, image, threshold=None, top_n=10, set_filter=None, foil_type_filter=None, rarity_filter=None, game_filter=None, metadata_hints=None, _allow_no_match_fallback=True):
        """
        Multi-threaded card scanning with early termination
        
        Args:
            set_filter: List of set codes to filter by (None = all sets)
            foil_type_filter: List of foil types (subTypeName values) to filter by (None = all types)
            rarity_filter: List of rarity codes to filter by (None = all rarities)
            game_filter: Specific game name to filter by (None = all active games)
        """
        start_time = time.time()
        
        # pHash-based scanning (vector/embedding disabled)
        # Compute phash if not already computed (ResNet50 path skips this)
        if not 'r_hash' in locals():
            r_hash, g_hash, b_hash = self.compute_phash(image)
        
        # Check if hash computation failed
        if r_hash is None or g_hash is None or b_hash is None:
            return [], 0

        stage1_hints = self._build_metadata_hints(image, metadata_hints, set_filter=set_filter)

        # Optional MSER-based quality score
        mser_score = self._compute_mser_score(image)
        
        # Event for early termination when exact match found
        found_exact = threading.Event()
        
        all_matches = []
        
        # Determine effective threshold (use instance default if not provided)
        if threshold is None:
            threshold = getattr(self, 'scan_threshold', 40)

        # Submit game scans to thread pool
        target_games = self._resolve_game_names([game_filter]) if game_filter else list(self.active_games)
        if not target_games:
            target_games = list(self.active_games)

        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {
                executor.submit(
                    self.scan_game, 
                    game_name, 
                    r_hash, 
                    g_hash, 
                    b_hash, 
                    threshold,
                    found_exact,
                    set_filter,
                    foil_type_filter,
                    rarity_filter,
                    stage1_hints,
                ): game_name 
                for game_name in target_games
            }
            
            # Collect results as they complete
            for future in as_completed(futures):
                matches = future.result()
                all_matches.extend(matches)
                
                # If exact match found, cancel remaining tasks
                if found_exact.is_set():
                    break
        
        # Sort by distance
        # Deduplicate matches by product_id (faster than complex tuple key)
        deduped = {}
        for m in all_matches:
            pid = m.get('product_id')
            if pid:
                if pid in deduped:
                    # Keep the one with smaller distance
                    if m['distance'] < deduped[pid]['distance']:
                        deduped[pid] = m
                else:
                    deduped[pid] = m
            else:
                # Fallback for entries without product_id
                key = (m['game'], m['name'], m['number'], m['set'])
                if key in deduped:
                    if m['distance'] < deduped[key]['distance']:
                        deduped[key] = m
                else:
                    deduped[key] = m

        all_matches = list(deduped.values())
        all_matches.sort(key=lambda x: (-int(x.get('metadata_match_score', 0)), x['distance']))

        # OCR-first resolution: if metadata gives a unique card, prefer it over pHash ordering.
        ocr_candidate = self._resolve_ocr_metadata_candidate(stage1_hints, game_filter=game_filter)
        if ocr_candidate:
            price_value = None
            try:
                if ocr_candidate.get('market_price') is not None and float(ocr_candidate.get('market_price')) > 0:
                    price_value = float(ocr_candidate.get('market_price'))
                elif ocr_candidate.get('low_price') is not None and float(ocr_candidate.get('low_price')) > 0:
                    price_value = float(ocr_candidate.get('low_price'))
            except Exception:
                price_value = None

            elapsed = time.time() - start_time
            self.stats['scans'] += 1
            self.stats['total_time'] += elapsed

            return ([{
                'product_id': str(ocr_candidate.get('product_id')) if ocr_candidate.get('product_id') is not None else None,
                'game': self.games.get(ocr_candidate.get('game_name'), {}).get('display_name') or ocr_candidate.get('game_name'),
                'name': ocr_candidate.get('name'),
                'number': ocr_candidate.get('number'),
                'set': ocr_candidate.get('set_code'),
                'rarity': ocr_candidate.get('rarity'),
                'foil_type': ocr_candidate.get('subTypeName'),
                'market_price': price_value,
                'distance': 998.0,
                'confidence': 97.0,
                'phash_confidence': 0.0,
                'mser_score': self._compute_mser_score(image),
                'metadata_match_score': 999,
                'confidence_level': 'high',
                'manual_review_required': False,
                'fallback_reason': 'ocr_metadata_unique',
                'ocr_name_raw': (stage1_hints or {}).get('name'),
                'ocr_cmc': (stage1_hints or {}).get('cmc'),
                'ocr_set_code': (stage1_hints or {}).get('set_code'),
                'ocr_collector_number': (stage1_hints or {}).get('collector_number'),
            }], elapsed)

        # If no pHash candidates and OCR yielded a name, return OCR-only manual-review result.
        if not all_matches:
            ocr_name = (stage1_hints or {}).get('name')
            if ocr_name:
                cleaned_ocr_name = str(ocr_name or '').strip()
                alpha_chars = sum(1 for ch in cleaned_ocr_name if ch.isalpha())
                alpha_ratio = alpha_chars / max(1, len(cleaned_ocr_name))
                has_title_signal = bool(re.search(r"[A-Za-z]{3,}\s+[A-Za-z]{3,}", cleaned_ocr_name))

                resolved_name = self._resolve_ocr_name_candidate(ocr_name)
                name_similarity = 0.0
                if resolved_name:
                    name_similarity = difflib.SequenceMatcher(
                        None,
                        str(ocr_name).lower(),
                        str(resolved_name).lower(),
                    ).ratio()
                metadata_support = bool(
                    (stage1_hints or {}).get('set_code')
                    or (stage1_hints or {}).get('collector_number')
                    or (stage1_hints or {}).get('cmc') is not None
                )
                db_candidate = None
                search_games = self._resolve_game_names([game_filter]) if game_filter else list(self.active_games)
                fallback_game_display = (
                    self.games.get(search_games[0], {}).get('display_name') or search_games[0]
                    if search_games else 'Magic: The Gathering'
                )
                if resolved_name and search_games and (metadata_support or name_similarity >= 0.90):
                    cursor = self.get_connection()
                    for gname in search_games:
                        table = self.games.get(gname, {}).get('table')
                        if not table:
                            continue
                        try:
                            row = cursor.execute(
                                f"SELECT product_id, name, number, set_code, rarity, subTypeName, market_price, low_price "
                                f"FROM {table} WHERE lower(name)=lower(?) LIMIT 1",
                                (resolved_name,),
                            ).fetchone()
                        except Exception:
                            row = None
                        if row:
                            price_value = None
                            try:
                                if row[6] is not None and float(row[6]) > 0:
                                    price_value = float(row[6])
                                elif row[7] is not None and float(row[7]) > 0:
                                    price_value = float(row[7])
                            except Exception:
                                price_value = None
                            db_candidate = {
                                'product_id': str(row[0]) if row[0] is not None else None,
                                'game': self.games[gname].get('display_name') or gname,
                                'name': row[1] or resolved_name,
                                'number': row[2],
                                'set': row[3],
                                'rarity': row[4],
                                'foil_type': row[5],
                                'market_price': price_value,
                            }
                            break
                if db_candidate:
                    elapsed = time.time() - start_time
                    self.stats['scans'] += 1
                    self.stats['total_time'] += elapsed
                    return ([{
                        **db_candidate,
                        'distance': 999.0,
                        'confidence': 60.0,
                        'phash_confidence': 0.0,
                        'mser_score': self._compute_mser_score(image),
                        'metadata_match_score': 1,
                        'confidence_level': 'low',
                        'manual_review_required': True,
                        'fallback_reason': 'metadata_name_only',
                        'ocr_name_raw': ocr_name,
                        'ocr_cmc': (stage1_hints or {}).get('cmc'),
                    }], elapsed)
                # If OCR looks too noisy, allow relaxed pHash fallback below.
                if alpha_ratio >= 0.55 or has_title_signal:
                    elapsed = time.time() - start_time
                    self.stats['scans'] += 1
                    self.stats['total_time'] += elapsed
                    return ([{
                        'product_id': None,
                        'game': fallback_game_display,
                        'name': resolved_name or ocr_name,
                        'number': None,
                        'set': None,
                        'rarity': None,
                        'foil_type': None,
                        'market_price': None,
                        'distance': 999.0,
                        'confidence': 55.0,
                        'phash_confidence': 0.0,
                        'mser_score': self._compute_mser_score(image),
                        'metadata_match_score': 1,
                        'confidence_level': 'low',
                        'manual_review_required': True,
                        'fallback_reason': 'ocr_name_only',
                        'ocr_name_raw': ocr_name,
                        'ocr_cmc': (stage1_hints or {}).get('cmc'),
                    }], elapsed)

        # If strict threshold produced no candidates, run one relaxed pass and mark results for review.
        if not all_matches and _allow_no_match_fallback and getattr(self, 'no_match_fallback_enabled', True):
            relaxed_threshold = max(int(threshold), int(getattr(self, 'no_match_fallback_threshold', 140)))
            if relaxed_threshold > int(threshold):
                relaxed_matches, relaxed_elapsed = self.scan_card(
                    image,
                    threshold=relaxed_threshold,
                    top_n=top_n,
                    set_filter=set_filter,
                    foil_type_filter=foil_type_filter,
                    rarity_filter=rarity_filter,
                    game_filter=game_filter,
                    metadata_hints={**(stage1_hints or {}), '_skip_ocr': True},
                    _allow_no_match_fallback=False,
                )
                for m in relaxed_matches:
                    m['fallback_relaxed_threshold'] = relaxed_threshold
                    m['fallback_reason'] = 'no_match_at_strict_threshold'
                    m['manual_review_required'] = True
                    m['confidence_level'] = 'low'
                elapsed = (time.time() - start_time) + float(relaxed_elapsed)
                return relaxed_matches, elapsed

        # Final guard: if nothing matched even after relaxed pass, return OCR-only candidate.
        if not all_matches:
            ocr_name = (stage1_hints or {}).get('name')
            if ocr_name:
                fallback_games = self._resolve_game_names([game_filter]) if game_filter else list(self.active_games)
                fallback_game_display = (
                    self.games.get(fallback_games[0], {}).get('display_name') or fallback_games[0]
                    if fallback_games else 'Magic: The Gathering'
                )
                elapsed = time.time() - start_time
                self.stats['scans'] += 1
                self.stats['total_time'] += elapsed
                return ([{
                    'product_id': None,
                    'game': fallback_game_display,
                    'name': self._resolve_ocr_name_candidate(ocr_name) or ocr_name,
                    'number': None,
                    'set': None,
                    'rarity': None,
                    'foil_type': None,
                    'market_price': None,
                    'distance': 999.0,
                    'confidence': 45.0,
                    'phash_confidence': 0.0,
                    'mser_score': mser_score,
                    'metadata_match_score': 0,
                    'confidence_level': 'low',
                    'manual_review_required': True,
                    'fallback_reason': 'ocr_name_only_noisy',
                    'ocr_name_raw': ocr_name,
                    'ocr_cmc': (stage1_hints or {}).get('cmc'),
                }], elapsed)

        # Blend MSER into confidence as a total score
        weight_mser = max(0.0, min(1.0, float(self.mser_weight)))
        weight_phash = max(0.0, 1.0 - weight_mser)

        for m in all_matches:
            phash_conf = float(m.get('confidence', 0) or 0)
            total_conf = (
                phash_conf * weight_phash +
                (mser_score or 0) * 100.0 * weight_mser
            )
            m['phash_confidence'] = phash_conf
            m['mser_score'] = mser_score
            m['confidence'] = total_conf
            if total_conf >= 90:
                level = 'high'
            elif total_conf >= 75:
                level = 'medium'
            else:
                level = 'low'
            m['confidence_level'] = level
            m['manual_review_required'] = total_conf < float(self.manual_review_confidence_threshold)
        
        # Update stats
        elapsed = time.time() - start_time
        self.stats['scans'] += 1
        self.stats['total_time'] += elapsed
        
        return all_matches[:top_n], elapsed
    
    def scan_from_file(self, image_path, threshold=10, top_n=10, set_filter=None, foil_type_filter=None, rarity_filter=None, game_filter=None, metadata_hints=None):
        """Scan from image file"""
        path = ensure_image_path_exists(image_path)

        image = cv2.imread(str(path))
        if image is None:
            raise ValueError(f"Failed to decode image file: {path}")

        # Normalize scanner captures by trimming large white background margins.
        image = self._trim_white_background(image, white_threshold=246, min_shrink_ratio=0.03, padding=6)

        candidates = [(image, 'original')]
        deferred_candidates = []

        # Known full-frame photos can use manual crop presets to isolate the card first.
        manual_crop, manual_applied = apply_manual_crop_preset(image, path.name)
        if manual_applied:
            # Try preset crop first; enqueue broader fallbacks only if needed.
            candidates = [(manual_crop, 'manual_crop')]
            contour_crop = detect_and_warp_card(image)
            if contour_crop is not None:
                deferred_candidates.append((contour_crop, 'contour_crop'))
        else:
            contour_crop = detect_and_warp_card(image)
            if contour_crop is not None:
                # For non-preset files, try warped crop first then original frame.
                candidates = [(contour_crop, 'contour_crop'), (image, 'original')]

        best_matches = []
        best_elapsed = 0.0
        best_score = (-1, -1.0, -9999.0)
        prefer_cropped_for_file = str(path.name).lower() in MANUAL_CROP_PRESETS

        for scan_image, source in candidates:
            # OCR fast-path: resolve metadata directly before any pHash work.
            fast_hints = self._build_metadata_hints(scan_image, metadata_hints, set_filter=set_filter)
            fast_candidate = self._resolve_ocr_metadata_candidate(fast_hints, game_filter=game_filter)
            if fast_candidate:
                price_value = None
                try:
                    if fast_candidate.get('market_price') is not None and float(fast_candidate.get('market_price')) > 0:
                        price_value = float(fast_candidate.get('market_price'))
                    elif fast_candidate.get('low_price') is not None and float(fast_candidate.get('low_price')) > 0:
                        price_value = float(fast_candidate.get('low_price'))
                except Exception:
                    price_value = None

                return ([{
                    'product_id': str(fast_candidate.get('product_id')) if fast_candidate.get('product_id') is not None else None,
                    'game': self.games.get(fast_candidate.get('game_name'), {}).get('display_name') or fast_candidate.get('game_name'),
                    'name': fast_candidate.get('name'),
                    'number': fast_candidate.get('number'),
                    'set': fast_candidate.get('set_code'),
                    'rarity': fast_candidate.get('rarity'),
                    'foil_type': fast_candidate.get('subTypeName'),
                    'market_price': price_value,
                    'distance': 998.0,
                    'confidence': 98.0,
                    'phash_confidence': 0.0,
                    'mser_score': self._compute_mser_score(scan_image),
                    'metadata_match_score': 1000,
                    'confidence_level': 'high',
                    'manual_review_required': False,
                    'fallback_reason': 'ocr_fast_path',
                    'ocr_name_raw': fast_hints.get('name'),
                    'ocr_cmc': fast_hints.get('cmc'),
                    'ocr_set_code': fast_hints.get('set_code'),
                    'ocr_collector_number': fast_hints.get('collector_number'),
                    'scan_source': source,
                }], 0.0)

            merged_hints = dict(metadata_hints or {})
            for key in ('name', 'cmc', 'collector_number', 'set_code', 'color_identity'):
                if key not in merged_hints and fast_hints.get(key) is not None:
                    merged_hints[key] = fast_hints.get(key)
            # Prevent repeated OCR extraction for this same candidate image.
            merged_hints['_skip_ocr'] = True

            matches, elapsed = self.scan_card(
                scan_image,
                threshold,
                top_n,
                set_filter,
                foil_type_filter,
                rarity_filter,
                game_filter,
                merged_hints,
            )

            if not matches:
                if source == 'manual_crop' and deferred_candidates:
                    candidates.extend(deferred_candidates)
                    deferred_candidates = []
                continue

            top = matches[0]
            score = (
                0 if top.get('manual_review_required') else 1,
                1 if (prefer_cropped_for_file and source != 'original') else 0,
                float(top.get('confidence', 0.0) or 0.0),
                -float(top.get('distance', 9999.0) or 9999.0),
            )

            if score > best_score:
                best_score = score
                best_matches = matches
                best_elapsed = elapsed
                for match in best_matches:
                    match['scan_source'] = source

            # If preset crop only produced low-confidence/manual-review output, try broader fallbacks.
            if source == 'manual_crop' and deferred_candidates and top.get('manual_review_required'):
                candidates.extend(deferred_candidates)
                deferred_candidates = []

        return best_matches, best_elapsed
    
    def adaptive_scan(self, image, max_threshold=20, target_matches=5, set_filter=None, foil_type_filter=None, rarity_filter=None):
        """
        Adaptive threshold scanning
        Starts strict, gradually relaxes until target matches found
        """
        print("\n[*] Adaptive scan mode...")
        
        for threshold in range(5, max_threshold + 1, 2):
            matches, elapsed = self.scan_card(
                image,
                threshold=threshold,
                top_n=target_matches,
                set_filter=set_filter,
                foil_type_filter=foil_type_filter,
                rarity_filter=rarity_filter,
                _allow_no_match_fallback=False,
            )
            
            print(f"    Threshold {threshold}: {len(matches)} matches in {elapsed:.2f}s")
            
            if len(matches) >= target_matches:
                return matches, elapsed
            
            if matches and matches[0]['distance'] == 0:
                # Exact match found
                return matches, elapsed
        
        # Return best we found
        return matches, elapsed
    
    def print_stats(self):
        """Print performance statistics"""
        print("\n" + "=" * 80)
        print("PERFORMANCE STATISTICS")
        print("=" * 80)
        print(f"Total scans: {self.stats['scans']}")
        if self.stats['scans'] > 0:
            print(f"Average time: {self.stats['total_time'] / self.stats['scans']:.2f}s")
            print(f"Average cards checked: {self.stats['cards_checked'] / self.stats['scans']:.0f}")
            print(f"Cache hit rate: {100 * self.stats['cache_hits'] / max(self.stats['scans'], 1):.1f}%")
        print("=" * 80)
        
        # Collection stats if enabled
        if self.collection_manager:
            print("\n")
            self.collection_manager.print_summary()
    
    def save_to_collection(self, card_info, quantity=None, condition=None, language=None, is_foil=None, prompt=None):
        """
        Save a scanned card to the collection
        
        Args:
            card_info: Card data from scan results
            quantity: Number of copies (uses default 1 if None)
            condition: Card condition (uses default if None)
            language: Language code (uses default if None)
            is_foil: Whether card is foil (uses default if None)
            prompt: Override prompt_for_details setting (True/False/None)
        
        Returns:
            Card entry if saved, None if collection disabled
        """
        if not self.collection_manager:
            print("[!] Collection manager not initialized")
            return None
        
        # Use defaults if not specified
        if quantity is None:
            quantity = 1
        if condition is None:
            condition = self.default_condition
        if language is None:
            language = self.default_language
        if is_foil is None:
            is_foil = self.default_foil
        
        try:
            entry = self.collection_manager.add_card(
                card_info, 
                quantity=quantity,
                condition=condition,
                language=language,
                is_foil=is_foil
            )
            print(f"[+] Saved to collection: {entry['name']} ({entry['sku']})")
            return entry
        except Exception as e:
            print(f"[!] Error saving to collection: {e}")
            return None
    
    def export_collection(self, format_type='both', by_game=False):
        """
        Export the collection
        
        Args:
            format_type: 'tcgtraders', 'tcgplayer', or 'both'
            by_game: Whether to separate exports by game
        
        Returns:
            Exported file path(s)
        """
        if not self.collection_manager:
            print("[!] Collection manager not initialized")
            return None
        
        if by_game:
            return self.collection_manager.export_by_game(format_type)
        else:
            results = {}
            if format_type in ['tcgtraders', 'both']:
                results['tcgtraders'] = self.collection_manager.export_tcgtraders_csv()
            if format_type in ['tcgplayer', 'both']:
                results['tcgplayer'] = self.collection_manager.export_tcgplayer_text()
            return results
    
    def close(self):
        """Close database and serial connections"""
        self.conn.close()
        if hasattr(self.local, 'conn'):
            self.local.conn.close()
        if self.ser:
            self.ser.close()
            print("[+] Serial connection closed")
    
    # =====================================================================
    # WEBCAM/REALTIME CAPTURE MODE
    # =====================================================================

    def run_realtime_mode(self, sorting_mode="color", threshold=1000000):
        """
        Run scanner in realtime webcam mode (phash-based identification)
        Automatically scans cards when detected
        """
        print("\n" + "=" * 80)
        print("REALTIME CARD SCANNER MODE")
        print("=" * 80)
        print(f"Sorting mode: {sorting_mode}")
        if sorting_mode in ["buy", "price"]:
            print(f"Price threshold: ${threshold:,.2f}")
        print(f"Inventory tracking: {self.track_inventory}")
        print(f"Collection mode: {'Enabled' if self.collection_manager else 'Disabled'}")
        print(f"Arduino: {'Connected' if self.ser else 'Disabled'}")
        print("\nKeyboard shortcuts:")
        print("  'q' - Quit")
        print("  's' - Save last scanned card to collection")
        print("  'c' - View collection stats")
        print("=" * 80)
        
        # Open webcam
        cap = cv2.VideoCapture(0)
        if not cap.isOpened():
            print("[!] Cannot open webcam")
            return
        
        frame_count = 0
        total_processing_time = 0
        last_scanned_card = None  # Store last scanned card for 's' shortcut
        
        try:
            while True:
                frame_start = time.time()
                ret, frame = cap.read()
                
                if not ret:
                    print("[!] Failed to grab frame")
                    break
                
                # Show camera feed
                cv2.imshow("Camera Feed", frame)
                
                display_frame = frame.copy()
                
                # Find card contour
                card_approx = self._find_card_contour(frame)
                
                if card_approx is not None:
                    # Scan the card using phash
                    card_info = self._process_card_from_contour(frame, card_approx)
                    
                    if card_info:
                        # Store for keyboard shortcut
                        last_scanned_card = card_info
                        
                        # Check inventory if enabled
                        if self.track_inventory:
                            card_info = self.check_inventory(card_info)
                        
                        if card_info != "RejectCard":
                            # Get bin assignment
                            bin_result = self.get_bin_number(card_info, sorting_mode, threshold)
                            
                            # Display result
                            self._handle_recognized_card(display_frame, card_info, bin_result)
                            
                            # Send to Arduino
                            if self.ser:
                                self.send_to_arduino(bin_result)
                        else:
                            self._handle_unrecognized_card(display_frame, card_approx, "In inventory")
                    else:
                        self._handle_unrecognized_card(display_frame, card_approx, "Card not found in database")
                    
                    cv2.imshow("Detected Card", display_frame)
                else:
                    cv2.imshow("Detected Card", display_frame)
                
                # Stats
                frame_count += 1
                frame_time = time.time() - frame_start
                total_processing_time += frame_time
                avg_time = total_processing_time / frame_count
                
                if frame_count % 30 == 0:  # Print every 30 frames
                    print(f"[*] Frame {frame_count} | Time: {frame_time:.3f}s | Avg: {avg_time:.3f}s")
                
                # Handle keyboard input
                key = cv2.waitKey(1) & 0xFF
                
                if key == ord('q'):
                    break
                elif key == ord('s') and last_scanned_card and self.collection_manager:
                    # Quick save last scanned card
                    print("\n[*] Saving to collection:")
                    print(f"    Card: {last_scanned_card.get('name', 'Unknown')}")
                    
                    if self.prompt_for_details:
                        # Prompt for each detail
                        print("    Enter details (or press Enter for defaults):")
                        
                        qty = input("    Quantity (1): ").strip() or "1"
                        cond = input("    Condition (NM/LP/MP/HP/DMG) [NM]: ").strip().upper() or "NM"
                        lang = input("    Language (EN/JP/FR/etc) [EN]: ").strip().upper() or "EN"
                        foil = input("    Foil? (Y/N) [N]: ").strip().upper() == "Y"
                        
                        cond_map = {
                            'NM': 'Near Mint',
                            'LP': 'Lightly Played', 
                            'MP': 'Moderately Played',
                            'HP': 'Heavily Played',
                            'DMG': 'Damaged'
                        }
                        
                        self.save_to_collection(
                            last_scanned_card,
                            quantity=int(qty),
                            condition=cond_map.get(cond, 'Near Mint'),
                            language=lang,
                            is_foil=foil
                        )
                    else:
                        # Auto-save with defaults (no prompts)
                        self.save_to_collection(last_scanned_card)
                        print(f"    Saved with defaults: {self.default_condition}, {self.default_language}, {'Foil' if self.default_foil else 'Normal'}")
                    
                    print("[+] Card saved! Press any key to continue scanning...\n")
                    cv2.waitKey(0)
                
                elif key == ord('c') and self.collection_manager:
                    # Show collection stats
                    print("\n" + "=" * 60)
                    stats = self.collection_manager.get_stats()
                    print(f"Collection Statistics:")
                    print(f"  Total cards: {stats['total_cards']}")
                    print(f"  Unique cards: {stats['unique_cards']}")
                    print(f"  Session cards: {stats['session_cards']}")
                    if stats['by_game']:
                        print(f"  By game:")
                        for game, count in stats['by_game'].items():
                            print(f"    {game}: {count}")
                    print("=" * 60)
                    print("[*] Press any key to continue scanning...\n")
                    cv2.waitKey(0)
        
        finally:
            cap.release()
            cv2.destroyAllWindows()
            print(f"\n[+] Processed {frame_count} frames")
            if frame_count > 0:
                print(f"[+] Average processing time: {total_processing_time/frame_count:.3f}s")
    
    def _find_card_contour(self, frame):
        """
        Find card contour using the internal detection pipeline.
        """
        try:
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            blurred = cv2.GaussianBlur(gray, (3, 3), 0)
            edges = cv2.Canny(blurred, 50, 150)
            kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
            closed = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, kernel)
            contours, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            best_contour = None
            max_area = 0
            for contour in contours:
                approx = cv2.approxPolyDP(contour, 0.02 * cv2.arcLength(contour, True), True)
                if len(approx) == 4:
                    area = cv2.contourArea(approx)
                    if area > 10000 and area > max_area:
                        max_area = area
                        best_contour = approx
            return best_contour
        except Exception:
            return None
        return None
    
    def _process_card_from_contour(self, frame, card_approx):
        """
        Extract and process card from contour
        Returns card info dict or None
        """
        try:
            # Use the internal perspective correction
            warped = self._get_perspective_corrected_card(frame, card_approx)

            if warped is None:
                return None

            # Crop using internal WIDTH/HEIGHT (don't force a square)
            crop_w, crop_h = 745, 1043

            # Ensure we don't index out of bounds if the warped image is smaller
            h, w = warped.shape[:2]
            crop_w = min(crop_w, w)
            crop_h = min(crop_h, h)

            cropped = warped[:crop_h, :crop_w]

            stage1_hints = None
            # OCR-first for live scans: only fall back to pHash when OCR cannot uniquely resolve.
            if getattr(self, 'enable_ocr_live_fast_path', False):
                try:
                    stage1_hints = self._build_metadata_hints(cropped, None)
                    ocr_candidate = self._resolve_ocr_metadata_candidate(stage1_hints)
                except Exception:
                    stage1_hints = None
                    ocr_candidate = None

                if ocr_candidate:
                    price_value = None
                    try:
                        if ocr_candidate.get('market_price') is not None and float(ocr_candidate.get('market_price')) > 0:
                            price_value = float(ocr_candidate.get('market_price'))
                        elif ocr_candidate.get('low_price') is not None and float(ocr_candidate.get('low_price')) > 0:
                            price_value = float(ocr_candidate.get('low_price'))
                    except Exception:
                        price_value = None

                    return {
                        'product_id': str(ocr_candidate.get('product_id')) if ocr_candidate.get('product_id') is not None else None,
                        'game': self.games.get(ocr_candidate.get('game_name'), {}).get('display_name') or ocr_candidate.get('game_name'),
                        'name': ocr_candidate.get('name'),
                        'number': ocr_candidate.get('number'),
                        'set': ocr_candidate.get('set_code'),
                        'rarity': ocr_candidate.get('rarity'),
                        'foil_type': ocr_candidate.get('subTypeName'),
                        'market_price': price_value,
                        'distance': 998.0,
                        'confidence': 98.0,
                        'phash_confidence': 0.0,
                        'mser_score': self._compute_mser_score(cropped),
                        'metadata_match_score': 1000,
                        'confidence_level': 'high',
                        'manual_review_required': False,
                        'fallback_reason': 'ocr_fast_path_live',
                        'ocr_name_raw': (stage1_hints or {}).get('name'),
                        'ocr_cmc': (stage1_hints or {}).get('cmc'),
                        'ocr_set_code': (stage1_hints or {}).get('set_code'),
                        'ocr_collector_number': (stage1_hints or {}).get('collector_number'),
                        'scan_source': 'contour_crop',
                    }

                # If metadata is not strong enough for unique resolution, still prefer OCR name.
                # This keeps live GUI scans from jumping to unrelated pHash matches.
                resolved_name = self._resolve_ocr_name_candidate((stage1_hints or {}).get('name'))
                if resolved_name:
                    search_games = list(self.active_games)
                    cursor = self.get_connection()
                    exact_rows = []
                    for gname in search_games:
                        table = self.games.get(gname, {}).get('table')
                        if not table:
                            continue
                        try:
                            row = cursor.execute(
                                f"SELECT product_id, name, number, set_code, rarity, subTypeName, market_price, low_price "
                                f"FROM {table} WHERE lower(name)=lower(?) LIMIT 1",
                                (resolved_name,),
                            ).fetchone()
                        except Exception:
                            row = None
                        if row:
                            exact_rows.append((gname, row))

                    if len(exact_rows) == 1:
                        gname, row = exact_rows[0]
                        price_value = None
                        try:
                            if row[6] is not None and float(row[6]) > 0:
                                price_value = float(row[6])
                            elif row[7] is not None and float(row[7]) > 0:
                                price_value = float(row[7])
                        except Exception:
                            price_value = None

                        return {
                            'product_id': str(row[0]) if row[0] is not None else None,
                            'game': self.games.get(gname, {}).get('display_name') or gname,
                            'name': row[1] or resolved_name,
                            'number': row[2],
                            'set': row[3],
                            'rarity': row[4],
                            'foil_type': row[5],
                            'market_price': price_value,
                            'distance': 999.0,
                            'confidence': 62.0,
                            'phash_confidence': 0.0,
                            'mser_score': self._compute_mser_score(cropped),
                            'metadata_match_score': 2,
                            'confidence_level': 'low',
                            'manual_review_required': True,
                            'fallback_reason': 'ocr_name_primary_live',
                            'ocr_name_raw': (stage1_hints or {}).get('name'),
                            'ocr_cmc': (stage1_hints or {}).get('cmc'),
                            'scan_source': 'contour_crop',
                        }

                    return {
                        'product_id': None,
                        'game': self.games.get(search_games[0], {}).get('display_name') or search_games[0] if search_games else 'Magic: The Gathering',
                        'name': resolved_name,
                        'number': None,
                        'set': None,
                        'rarity': None,
                        'foil_type': None,
                        'market_price': None,
                        'distance': 999.0,
                        'confidence': 55.0,
                        'phash_confidence': 0.0,
                        'mser_score': self._compute_mser_score(cropped),
                        'metadata_match_score': 1,
                        'confidence_level': 'low',
                        'manual_review_required': True,
                        'fallback_reason': 'ocr_name_only_live',
                        'ocr_name_raw': (stage1_hints or {}).get('name'),
                        'ocr_cmc': (stage1_hints or {}).get('cmc'),
                        'scan_source': 'contour_crop',
                    }

                # Rules-text fallback for cards where name OCR fails but body text is readable.
                rules_text = self._extract_full_image_ocr_text(cropped)
                rules_candidate = self._resolve_ocr_rules_text_candidate(rules_text)
                if rules_candidate:
                    price_value = None
                    try:
                        if rules_candidate.get('market_price') is not None and float(rules_candidate.get('market_price')) > 0:
                            price_value = float(rules_candidate.get('market_price'))
                        elif rules_candidate.get('low_price') is not None and float(rules_candidate.get('low_price')) > 0:
                            price_value = float(rules_candidate.get('low_price'))
                    except Exception:
                        price_value = None

                    return {
                        'product_id': str(rules_candidate.get('product_id')) if rules_candidate.get('product_id') is not None else None,
                        'game': self.games.get(rules_candidate.get('game_name'), {}).get('display_name') or rules_candidate.get('game_name'),
                        'name': rules_candidate.get('name'),
                        'number': rules_candidate.get('number'),
                        'set': rules_candidate.get('set_code'),
                        'rarity': rules_candidate.get('rarity'),
                        'foil_type': rules_candidate.get('subTypeName'),
                        'market_price': price_value,
                        'distance': 997.0,
                        'confidence': 75.0,
                        'phash_confidence': 0.0,
                        'mser_score': self._compute_mser_score(cropped),
                        'metadata_match_score': 3,
                        'confidence_level': 'medium',
                        'manual_review_required': False,
                        'fallback_reason': 'ocr_rules_text_live',
                        'ocr_name_raw': (stage1_hints or {}).get('name'),
                        'ocr_cmc': (stage1_hints or {}).get('cmc'),
                        'scan_source': 'contour_crop',
                    }

            # Convert to PIL for hashing (original uses per-channel pHash)
            rgb_image = cv2.cvtColor(cropped, cv2.COLOR_BGR2RGB)
            pil_image = Image.fromarray(rgb_image)

            # Compute per-channel pHashes and print live debug output
            try:
                r_hash, g_hash, b_hash = self.compute_phash(pil_image)
                print(f"phashes: r={r_hash} g={g_hash} b={b_hash}")
                try:
                    from pathlib import Path
                    import time
                    outdir = Path(__file__).parent / 'debug_crops'
                    outdir.mkdir(parents=True, exist_ok=True)
                    shortname = f"{int(time.time())}_{(r_hash or '')[:8]}_{(g_hash or '')[:8]}_{(b_hash or '')[:8]}.png"
                    pil_image.save(outdir / shortname)
                    print(f"[+] Saved debug crop: {outdir / shortname}")
                except Exception as _e:
                    print(f"[!] Failed to save debug crop: {_e}")
            except Exception:
                r_hash = g_hash = b_hash = None

            # Scan the card using existing scan path which computes pHash identically
            # Use current scanner threshold (GUI can update this live)
            scan_threshold = getattr(self, 'scan_threshold', 40)
            merged_hints = dict(stage1_hints or {})
            if merged_hints:
                merged_hints['_skip_ocr'] = True
            matches, _ = self.scan_card(pil_image, threshold=scan_threshold, top_n=3, metadata_hints=merged_hints)

            if matches and len(matches) > 0:
                return matches[0]

            return None

        except Exception as e:
            print(f"[!] Error processing card: {e}")
            return None
    
    def _get_perspective_corrected_card(self, frame, card_approx):
        """Get perspective-corrected card image"""
        try:
            # Define card dimensions
            width = 745
            height = 1043
            
            # Get corner points
            pts = card_approx.reshape(4, 2)
            pts = sorted(pts, key=lambda point: point[1])
            top_two, bottom_two = pts[:2], pts[2:]
            top_left, top_right = sorted(top_two, key=lambda point: point[0])
            bottom_left, bottom_right = sorted(bottom_two, key=lambda point: point[0])
            rect = np.array([top_left, top_right, bottom_right, bottom_left], dtype=np.float32)
            
            # Destination points
            dst = np.array([
                [0, 0],
                [width - 1, 0],
                [width - 1, height - 1],
                [0, height - 1]
            ], dtype=np.float32)
            
            # Get perspective transform
            M = cv2.getPerspectiveTransform(rect, dst)
            warped = cv2.warpPerspective(frame, M, (width, height))

            # Rotate to portrait if needed
            h, w = warped.shape[:2]
            if w > h:
                for _ in range(3):
                    warped = cv2.rotate(warped, cv2.ROTATE_90_CLOCKWISE)
                    h, w = warped.shape[:2]
                    if w <= h:
                        break
            
            return warped
        
        except Exception as e:
            print(f"[!] Perspective correction error: {e}")
            return None
    
    def _handle_unrecognized_card(self, display_frame, card_approx, reason="Unknown"):
        """Display unrecognized card with reason"""
        cv2.drawContours(display_frame, [card_approx], -1, (0, 0, 255), 2)
        cv2.putText(display_frame, "Unrecognized Card", (10, 30),
                   cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 255), 2)
        cv2.putText(display_frame, f"Reason: {reason}", (10, 60),
                   cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2)
        cv2.putText(display_frame, "Bin: RejectCard", (10, 200),
                   cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 255), 2)
        print(f"[!] Card unrecognized: {reason}")
    
    def _handle_recognized_card(self, display_frame, card_info, bin_result):
        """Display recognized card with info"""
        # Draw card info
        y_pos = 30
        line_height = 25
        
        name = card_info.get('name') or card_info.get('card_name') or 'Unknown'
        game = card_info.get('game', 'Unknown')
        set_code = card_info.get('set', 'N/A')
        number = card_info.get('number', 'N/A')
        confidence = card_info.get('confidence', 0)
        price = card_info.get('market_price')
        
        cv2.putText(display_frame, f"Card: {name}", (10, y_pos),
                   cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
        y_pos += line_height
        
        cv2.putText(display_frame, f"Game: {game}", (10, y_pos),
                   cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1)
        y_pos += line_height
        
        cv2.putText(display_frame, f"Set: {set_code} #{number}", (10, y_pos),
                   cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1)
        y_pos += line_height
        
        cv2.putText(display_frame, f"Confidence: {confidence:.1f}%", (10, y_pos),
                   cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1)
        y_pos += line_height
        
        if price:
            cv2.putText(display_frame, f"Price: ${price:.2f}", (10, y_pos),
                       cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1)
            y_pos += line_height
        
        y_pos += 15
        
        # Bin number
        bin_color = (0, 255, 0) if bin_result != "RejectCard" else (0, 0, 255)
        cv2.putText(display_frame, f"Bin: {bin_result}", (10, y_pos),
                   cv2.FONT_HERSHEY_SIMPLEX, 1.2, bin_color, 2)
        
        print(f"[+] Recognized: {name} ({game}) -> Bin: {bin_result}")

def print_sorting_options():
    """Print available sorting modes"""
    print("\n" + "=" * 60)
    print("SORTING MODES:")
    print("=" * 60)
    print("1 - Color:      Sort by card color (W/U/B/R/G/Multicolor/Colorless/Land)")
    print("2 - CMC:        Sort by converted mana cost (1-8+)")
    print("3 - Set:        Sort by set code")
    print("4 - Price:      Sort by price (all price ranges)")
    print("5 - Type:       Sort by card type (Creature/Instant/etc)")
    print("6 - Buy mode:   Sort by price with custom threshold")
    print("=" * 60)

def main():
    import sys
    import argparse
    
    parser = argparse.ArgumentParser(description='Optimized Universal Card Scanner')
    parser.add_argument('image', nargs='?', help='Path to card image')
    parser.add_argument('--cache', action='store_true', help='Preload hash cache for Magic cards')
    parser.add_argument('--game', '-g', action='append', help='Game filter (Magic only). Non-Magic values raise an error.')
    parser.add_argument('--set', '-s', action='append', help='Limit to specific set code(s). Can be used multiple times. Example: -s LEA -s LEB')
    parser.add_argument('--foil-type', '-f', action='append', help='Filter by foil type (subTypeName). Can be used multiple times. Example: -f Foil -f Normal')
    parser.add_argument('--rarity', '-r', action='append', help='Filter by rarity. Can be used multiple times. Example: -r M -r R')
    parser.add_argument('--list-games', action='store_true', help='List active game(s) and exit (Magic only)')
    parser.add_argument('--list-foil-types', metavar='GAME', help='List foil types for Magic cards')
    parser.add_argument('--list-rarities', metavar='GAME', help='List rarities for Magic cards')
    parser.add_argument('--list-sets', metavar='GAME', help='List sets for Magic cards')
    parser.add_argument('--threshold', '-t', type=int, default=40, help='Match threshold (default: 40, lower = stricter)')
    parser.add_argument('--top', '-n', type=int, default=10, help='Number of top matches to show (default: 10)')
    parser.add_argument('--min-confidence', '-c', type=float, default=85.0, help='Minimum confidence (percent) for Method 1 to be considered reasonable (default: 85.0)')
    parser.add_argument('--adaptive', action='store_true', help='Enable adaptive threshold fallback scan (slower).')
    
    # NEW OPTIONS
    parser.add_argument('--realtime', action='store_true', help='Run in realtime webcam mode')
    parser.add_argument('--serial-port', help='Serial port for Arduino (e.g., COM3)')
    parser.add_argument('--baud-rate', type=int, default=9600, help='Serial baud rate (default: 9600)')
    parser.add_argument('--track-inventory', action='store_true', help='Enable inventory tracking (reject duplicates)')
    parser.add_argument('--interactive', action='store_true', help='Interactive mode with menus (like original scanner)')
    
    args = parser.parse_args()
    
    print("\n" + "=" * 80)
    print("OPTIMIZED UNIVERSAL CARD SCANNER")
    print("=" * 80)
    
    # Initialize with 8 worker threads
    scanner = OptimizedCardScanner(
        max_workers=8,
        cache_enabled=True,
        serial_port=args.serial_port,
        baud_rate=args.baud_rate,
    )
    scanner.set_active_games(['magic'])

    magic_game_name = next(iter(scanner.games.keys()))
    magic_game_info = scanner.games[magic_game_name]
    
    # Enable inventory tracking if requested
    if args.track_inventory:
        scanner.enable_inventory_tracking(True)
    
    # Initialize serial if port specified
    if args.serial_port:
        scanner.init_serial()
    
    # List games and exit
    if args.list_games:
        print("\nActive game(s):")
        print(f"{'ID':>4}  {'Game Name'}")
        print("-" * 80)
        for name, info in sorted(scanner.games.items(), key=lambda x: x[1]['id']):
            try:
                print(f"{info['id']:4d}. {info['display_name']}")
            except UnicodeEncodeError:
                # Handle special characters that can't be printed in console
                safe_name = info['display_name'].encode('ascii', 'replace').decode('ascii')
                print(f"{info['id']:4d}. {safe_name}")
        scanner.close()
        return
    
    # List foil types for a specific game and exit
    if args.list_foil_types:
        table = magic_game_info['table']

        print(f"\n{'=' * 80}")
        print(f"Foil Types for: {magic_game_info['display_name']} (Game ID: {magic_game_info['id']})")
        print('=' * 80)

        # Query distinct subTypeName values
        try:
            cursor = scanner.get_connection()
            query = f"SELECT DISTINCT subTypeName FROM {table} WHERE subTypeName IS NOT NULL ORDER BY subTypeName"
            foil_types = cursor.execute(query).fetchall()

            if foil_types:
                print(f"\nFound {len(foil_types)} foil type(s):\n")
                for (foil_type,) in foil_types:
                    print(f"  - {foil_type}")
            else:
                print("\nNo foil types found (subTypeName column may not exist or is empty)")

        except sqlite3.OperationalError as e:
            print(f"\n[!] Error accessing foil types: {e}")
            print("    The subTypeName column may not exist for this game")

        scanner.close()
        return
    
    # List rarities for a specific game and exit
    if args.list_rarities:
        table = magic_game_info['table']
        
        print(f"\n{'=' * 80}")
        print(f"Rarities for: {magic_game_info['display_name']} (Game ID: {magic_game_info['id']})")
        print('=' * 80)
        
        # Query distinct rarity values with counts
        try:
            cursor = scanner.get_connection()
            query = f"SELECT rarity, COUNT(*) as count FROM {table} WHERE rarity IS NOT NULL GROUP BY rarity ORDER BY rarity"
            rarities = cursor.execute(query).fetchall()
            
            if rarities:
                print(f"\nFound {len(rarities)} rarity type(s):\n")
                for rarity, count in rarities:
                    print(f"  {rarity:4s} - {count:,} cards")
            else:
                print("\nNo rarities found (rarity column may not exist or is empty)")
        
        except sqlite3.OperationalError as e:
            print(f"\n[!] Error accessing rarities: {e}")
            print("    The rarity column may not exist for this game")
        
        scanner.close()
        return
    
    # List sets for a specific game and exit
    if args.list_sets:
        game_name = magic_game_name
        game_info = magic_game_info
        
        print(f"\n{'=' * 80}")
        print(f"Sets for: {game_info['display_name']} (Game ID: {game_info['id']})")
        print('=' * 80)
        
        # Query sets table using game name
        try:
            cursor = scanner.get_connection()
            query = """
                SELECT id, name, code, total_cards
                FROM sets
                WHERE game = ?
                ORDER BY name
            """
            sets = cursor.execute(query, (game_name,)).fetchall()
            
            if sets:
                print(f"\nFound {len(sets)} sets:\n")
                print(f"{'ID':>6}  {'Set Name':60s}  Cards")
                print("-" * 80)
                for set_id, name, code, count in sets:
                    card_count = f"({count:,} cards)" if count else ""
                    print(f"{set_id:6d}. {name:60s} {card_count}")
            else:
                print("\nNo sets found in database")
        
        except sqlite3.OperationalError as e:
            print(f"\n[!] Error accessing sets data: {e}")
        
        scanner.close()
        return
    
    # =====================================================================
    # INTERACTIVE MODE
    # =====================================================================
    if args.interactive or args.realtime:
        # Show main menu
        while True:
            print("\n" + "=" * 80)
            print("CARD SCANNER - MAIN MENU")
            print("=" * 80)
            print("1 - Start Realtime Scanning (Webcam)")
            print("2 - Scan Single Image File")
            print("3 - View Collection Statistics")
            print("4 - Export Collection")
            print("5 - Clear Current Session")
            print("6 - Collection Settings")
            print("7 - Exit")
            print("=" * 80)
            
            # Show current settings
            if scanner.collection_manager:
                print(f"\nCurrent Settings:")
                print(f"  Condition: {scanner.default_condition}")
                print(f"  Language: {scanner.default_language}")
                print(f"  Foil: {'Yes' if scanner.default_foil else 'No'}")
                print(f"  Prompt Mode: {'Individual prompts' if scanner.prompt_for_details else 'Auto-save with defaults'}")
            
            menu_choice = input("\nSelect option: ").strip()
            
            if menu_choice == "1":
                # Realtime scanning mode
                print_sorting_options()
                choice = input("\nEnter the number of the sorting method: ").strip()
                sorting_modes = {
                    "1": "color",
                    "2": "mana_value",
                    "3": "set",
                    "4": "price",
                    "5": "type",
                    "6": "buy"
                }
                sorting_mode = sorting_modes.get(choice, "color")
                
                # Get price threshold for buy mode
                threshold = 1000000
                if sorting_mode in ["buy", "price"]:
                    threshold_input = input("Enter a price threshold: ").strip()
                    try:
                        threshold = float(threshold_input)
                    except:
                        threshold = 1000000
                
                # Ask about inventory tracking
                if choice in ["3", "6"]:
                    track_inv = input("Track inventory (reject duplicates)? (Y/N): ").strip().upper()
                    if track_inv == "Y":
                        scanner.enable_inventory_tracking(True)
                
                # Optionally preload cache
                if args.cache:
                    scanner.preload_cache()
                
                # Run realtime mode
                print("\n[*] Starting realtime scanner...")
                print("[*] Press 'q' to return to menu")
                if scanner.prompt_for_details:
                    print("[*] Press 's' to save card (with prompts)")
                else:
                    print("[*] Press 's' to save card (auto-save with defaults)")
                scanner.run_realtime_mode(sorting_mode=sorting_mode, 
                                        threshold=threshold)
                
            elif menu_choice == "2":
                # Scan single file
                filepath = input("\nEnter image path: ").strip()
                if os.path.exists(filepath):
                    print(f"\n[*] Scanning {filepath}...")
                    matches, elapsed = scanner.scan_from_file(filepath, threshold=args.threshold, top_n=args.top)
                    
                    if matches:
                        print(f"\n[+] Found {len(matches)} matches in {elapsed:.2f}s:\n")
                        for i, match in enumerate(matches, 1):
                            print(f"{i}. {match['name']} ({match['set_code']}) - Distance: {match['distance']}")
                        
                        # Ask to save to collection
                        save = input("\nSave to collection? (Y/N): ").strip().upper()
                        if save == "Y" and matches and scanner.collection_manager:
                            card = matches[0]  # Use best match
                            
                            if scanner.prompt_for_details:
                                # Prompt for details
                                qty = input("Quantity (default 1): ").strip() or "1"
                                cond = input("Condition (NM/LP/MP/HP/DMG, default NM): ").strip().upper() or "NM"
                                lang = input("Language (EN/JP/FR/etc, default EN): ").strip().upper() or "EN"
                                foil = input("Foil? (Y/N, default N): ").strip().upper() == "Y"
                                
                                cond_map = {'NM': 'Near Mint', 'LP': 'Lightly Played', 'MP': 'Moderately Played', 
                                           'HP': 'Heavily Played', 'DMG': 'Damaged'}
                                
                                scanner.save_to_collection(
                                    card, 
                                    quantity=int(qty),
                                    condition=cond_map.get(cond, 'Near Mint'),
                                    language=lang,
                                    is_foil=foil
                                )
                            else:
                                # Auto-save with defaults
                                scanner.save_to_collection(card)
                                print(f"[+] Saved with defaults: {scanner.default_condition}, {scanner.default_language}, {'Foil' if scanner.default_foil else 'Normal'}")
                    else:
                        print("\n[!] No matches found")
                else:
                    print(f"\n[!] File not found: {filepath}")
            
            elif menu_choice == "3":
                # View stats
                scanner.print_stats()
            
            elif menu_choice == "4":
                # Export collection
                if not scanner.collection_manager:
                    print("\n[!] Collection manager not initialized")
                    continue
                
                print("\n" + "=" * 60)
                print("EXPORT OPTIONS")
                print("=" * 60)
                print("1 - Export for TCGTraders (our website)")
                print("2 - Export for TCGPlayer")
                print("3 - Export both formats")
                print("4 - Export by game (separate files)")
                print("=" * 60)
                
                export_choice = input("\nSelect export format: ").strip()
                
                if export_choice == "1":
                    result = scanner.export_collection(format_type='tcgtraders', by_game=False)
                    print(f"\n[+] Exported to: {result.get('tcgtraders')}")
                elif export_choice == "2":
                    result = scanner.export_collection(format_type='tcgplayer', by_game=False)
                    print(f"\n[+] Exported to: {result.get('tcgplayer')}")
                elif export_choice == "3":
                    result = scanner.export_collection(format_type='both', by_game=False)
                    print(f"\n[+] TCGTraders: {result.get('tcgtraders')}")
                    print(f"[+] TCGPlayer: {result.get('tcgplayer')}")
                elif export_choice == "4":
                    print("\nExport format:")
                    print("1 - TCGTraders")
                    print("2 - TCGPlayer")
                    print("3 - Both")
                    fmt_choice = input("Select format: ").strip()
                    fmt_map = {'1': 'tcgtraders', '2': 'tcgplayer', '3': 'both'}
                    fmt = fmt_map.get(fmt_choice, 'both')
                    
                    results = scanner.export_collection(format_type=fmt, by_game=True)
                    print(f"\n[+] Exported {len(results)} files:")
                    for name, path in results.items():
                        print(f"  {name}: {path}")
            
            elif menu_choice == "5":
                # Clear session
                if scanner.collection_manager:
                    confirm = input("\nClear current session? This will not affect master collection (Y/N): ").strip().upper()
                    if confirm == "Y":
                        scanner.collection_manager.clear_session()
                else:
                    print("\n[!] Collection manager not initialized")
            
            elif menu_choice == "6":
                # Collection Settings
                if not scanner.collection_manager:
                    print("\n[!] Collection manager not initialized")
                    continue
                
                print("\n" + "=" * 80)
                print("COLLECTION SETTINGS")
                print("=" * 80)
                print(f"Current Settings:")
                print(f"  1. Condition: {scanner.default_condition}")
                print(f"  2. Language: {scanner.default_language}")
                print(f"  3. Foil: {'Yes' if scanner.default_foil else 'No'}")
                print(f"  4. Prompt Mode: {'Individual prompts' if scanner.prompt_for_details else 'Auto-save with defaults'}")
                print("  5. Return to main menu")
                print("=" * 80)
                
                setting_choice = input("\nSelect setting to change: ").strip()
                
                if setting_choice == "1":
                    print("\nCondition Options:")
                    print("  1 - Near Mint (NM)")
                    print("  2 - Lightly Played (LP)")
                    print("  3 - Moderately Played (MP)")
                    print("  4 - Heavily Played (HP)")
                    print("  5 - Damaged (DMG)")
                    cond_choice = input("Select condition: ").strip()
                    cond_map = {
                        '1': 'Near Mint',
                        '2': 'Lightly Played',
                        '3': 'Moderately Played',
                        '4': 'Heavily Played',
                        '5': 'Damaged'
                    }
                    if cond_choice in cond_map:
                        scanner.default_condition = cond_map[cond_choice]
                        print(f"[+] Default condition set to: {scanner.default_condition}")
                
                elif setting_choice == "2":
                    print("\nCommon Language Codes:")
                    print("  EN - English")
                    print("  JP - Japanese")
                    print("  FR - French")
                    print("  DE - German")
                    print("  IT - Italian")
                    print("  ES - Spanish")
                    print("  PT - Portuguese")
                    print("  KO - Korean")
                    print("  CN - Chinese")
                    lang = input("\nEnter language code (2 letters): ").strip().upper()
                    if len(lang) == 2:
                        scanner.default_language = lang
                        print(f"[+] Default language set to: {scanner.default_language}")
                    else:
                        print("[!] Invalid language code (must be 2 letters)")
                
                elif setting_choice == "3":
                    foil_choice = input("\nDefault to Foil? (Y/N): ").strip().upper()
                    scanner.default_foil = (foil_choice == "Y")
                    print(f"[+] Default foil set to: {'Yes' if scanner.default_foil else 'No'}")
                
                elif setting_choice == "4":
                    print("\nPrompt Mode Options:")
                    print("  1 - Individual prompts (ask for details on each card)")
                    print("  2 - Auto-save with defaults (no prompts, faster)")
                    mode_choice = input("Select mode: ").strip()
                    if mode_choice == "1":
                        scanner.prompt_for_details = True
                        print("[+] Prompt mode set to: Individual prompts")
                    elif mode_choice == "2":
                        scanner.prompt_for_details = False
                        print("[+] Prompt mode set to: Auto-save with defaults")
                        print(f"    Cards will be saved as: {scanner.default_condition}, {scanner.default_language}, {'Foil' if scanner.default_foil else 'Normal'}")
            
            elif menu_choice == "7":
                # Exit
                print("\n[*] Exiting...")
                scanner.print_stats()
                scanner.close()
                return
            
            else:
                print("\n[!] Invalid option")
    
    # =====================================================================
    # COMMAND LINE MODE (Original functionality)
    # =====================================================================
    
    # Filter by games if specified
    if args.game:
        for game_filter in args.game:
            token = str(game_filter).strip().lower()
            if token in {'magic', 'magic: the gathering', 'mtg'} or 'magic' in token:
                continue
            scanner.close()
            raise ValueError(f"Only Magic cards are supported. Invalid game filter: {game_filter}")
        scanner.set_active_games(['magic'])

    # Optionally preload cache for top games (uses ~500MB RAM)
    if args.cache:
        scanner.preload_cache()
    
    if args.image:
        image_path = args.image
        print(f"\n[*] Scanning image: {image_path}")
        
        # Verify file exists
        if not os.path.exists(image_path):
            scanner.close()
            raise FileNotFoundError(f"Image file not found: {os.path.abspath(image_path)}")
        
        # Set filter if specified. Support either set codes (strings) or set IDs (integers)
        set_filter = None
        if args.set:
            set_codes = []
            set_ids = []
            for s in args.set:
                try:
                    set_ids.append(int(s))
                except ValueError:
                    # treat as set code string
                    set_codes.append(s)

            # Resolve set IDs to set codes via the sets table
            if set_ids:
                placeholders = ','.join(['?' for _ in set_ids])
                try:
                    rows = scanner.cursor.execute(
                        f"SELECT id, code, name, game FROM sets WHERE id IN ({placeholders})",
                        set_ids
                    ).fetchall()
                except sqlite3.OperationalError:
                    rows = []

                if not rows:
                    print(f"[!] No sets found for IDs: {set_ids}")
                else:
                    for rid, code, name, game in rows:
                        set_codes.append(code)

            if set_codes:
                # Normalize to uppercase codes for comparisons
                set_filter = [sc.upper() for sc in set_codes]
                print(f"[+] Filtering to set(s): {', '.join(set_codes)}")
            else:
                set_filter = []
        
        # Foil type filter
        foil_type_filter = None
        if args.foil_type:
            foil_type_filter = args.foil_type
            print(f"[+] Filtering to foil type(s): {', '.join(foil_type_filter)}")
        
        # Rarity filter
        rarity_filter = None
        if args.rarity:
            rarity_filter = [r.upper() for r in args.rarity]
            print(f"[+] Filtering to rarity: {', '.join(rarity_filter)}")
        
        # Option 1: Fixed threshold scan
        print(f"\n[Method 1] Fixed threshold ({args.threshold}):")
        matches, elapsed = scanner.scan_from_file(image_path, threshold=args.threshold, top_n=args.top, set_filter=set_filter, foil_type_filter=foil_type_filter, rarity_filter=rarity_filter)
        
        print(f"\n[+] Found {len(matches)} matches in {elapsed:.2f}s")
        for i, match in enumerate(matches[:args.top], 1):
            print(f"\n{i}. {match['name']} ({match['game']})")
            print(f"   Set: {match['set']} #{match['number']}")
            print(f"   Rarity: {match.get('rarity', 'N/A')}")
            print(f"   Foil Type: {match.get('foil_type', 'N/A')}")
            print(f"   Unique ID: {match.get('unique_id', 'N/A')}")
            price = match.get('market_price')
            if price:
                print(f"   Market Price: ${price:.2f}")
            else:
                print(f"   Market Price: N/A")
            print(f"   Confidence: {match['confidence']:.1f}%")
            print(f"   Distance: {match['distance']:.2f}")
        
        # Option 2: Adaptive threshold scan — only run when Method 1 didn't find a "reasonable" match
        # "Reasonable" is defined by --min-confidence (default 85%). Also keep the previous behavior of
        # skipping adaptive scanning when a set filter is provided (to avoid expensive broad relax).
        top_confidence = matches[0]['confidence'] if matches else 0.0
        print(f"\n[+] Top match confidence (Method 1): {top_confidence:.1f}%")

        if top_confidence >= args.min_confidence:
            print(f"\n[+] Top match meets min-confidence ({args.min_confidence:.1f}%), skipping adaptive scan.")
        else:
            if not args.set and args.adaptive:
                print("\n\n[Method 2] Adaptive threshold (running because Method 1 was not confident enough):")
                image = cv2.imread(image_path)
                if image is None:
                    print(f"[!] Failed to load image for adaptive scan: {image_path}")
                    print(f"[!] Check that the file exists and is a valid image format")
                else:
                    adapt_matches, adapt_elapsed = scanner.adaptive_scan(image, max_threshold=20, target_matches=5, set_filter=set_filter, foil_type_filter=foil_type_filter, rarity_filter=rarity_filter)
                    
                    print(f"\n[+] Adaptive best matches:")
                    for i, match in enumerate(adapt_matches[:5], 1):
                        price = match.get('market_price')
                        price_str = f"${price:.2f}" if price else "N/A"
                        print(f"{i}. {match['name']} - {match['confidence']:.1f}% ({match['distance']:.2f}) - Rarity: {match.get('rarity', 'N/A')} - Price: {price_str}")
            else:
                if args.set:
                    print("\n[+] Set filter present — adaptive scan skipped to keep results focused on the selected set(s).")
                else:
                    print("\n[+] Adaptive scan disabled (use --adaptive to enable slower fallback pass).")
        
        scanner.print_stats()
    else:
        print("\nUsage:")
        print("  python optimized_scanner.py <image_path> [options]")
        print("\nModes:")
        print("  --interactive        Interactive mode with sorting menu (like original)")
        print("  --realtime           Realtime webcam scanning mode")
        print("\nOptions:")
        print("  --cache              Preload hash cache for Magic cards (faster but uses RAM)")
        print("  -g, --game GAME      Optional game filter, but only Magic/MTG is accepted")
        print("  -s, --set SET        Limit to specific set code(s) or ID(s). Can be used multiple times.")
        print("  -f, --foil-type TYPE Filter by foil type (subTypeName). Can be used multiple times.")
        print("  -r, --rarity RARITY  Filter by rarity code. Can be used multiple times.")
        print("  -t, --threshold N    Match threshold (default: 10, lower = stricter)")
        print("  -n, --top N          Number of top matches to show (default: 10)")
        print("  --serial-port PORT   Serial port for Arduino (e.g., COM3)")
        print("  --baud-rate BAUD     Serial baud rate (default: 9600)")
        print("  --track-inventory    Enable inventory tracking (reject duplicates)")
        print("  --list-games         List all available games with their IDs")
        print("  --list-sets GAME     List Magic sets")
        print("  --list-foil-types GAME   List Magic foil types")
        print("  --list-rarities GAME     List Magic rarities")
        print("\nExamples:")
        print("  python optimized_scanner.py --interactive --serial-port COM3")
        print("  python optimized_scanner.py --realtime --cache --track-inventory")
        print("  python optimized_scanner.py card.jpg --cache")
        print("  python optimized_scanner.py card.jpg -g Magic            # Explicit Magic filter")
        print("  python optimized_scanner.py card.jpg -g MTG -s ONS       # MTG alias + set filter")
        print("  python optimized_scanner.py card.jpg -f Foil -r M        # Foil mythic rares only")
        print("  python optimized_scanner.py card.jpg -f Normal -r R -r M # Normal rares/mythics")
        print("  python optimized_scanner.py --list-games                 # Show active game configuration")
        print("  python optimized_scanner.py --list-sets Magic            # Show Magic sets")
        print("  python optimized_scanner.py --list-rarities Magic        # Show Magic rarities")
        print("  python optimized_scanner.py --list-foil-types Magic      # Show Magic foil types")
    
    scanner.close()

if __name__ == '__main__':
    main()

