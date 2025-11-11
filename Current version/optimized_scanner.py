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
import cv2
import numpy as np
import sqlite3
from pathlib import Path
import imagehash
from PIL import Image
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from queue import PriorityQueue
import time
import serial
import os

def download_database(db_path="unified_card_database.db"):
    """Download database from server if it doesn't exist locally"""
    if os.path.exists(db_path):
        return True
    
    print(f"[!] Database not found at {db_path}")
    print("[*] Attempting to download from server...")
    
    # Try both server URLs (HTTP for local, HTTPS for public)
    servers = [
        "http://10.0.0.36:5000/download/unified_card_database.db",  # Direct to Flask on port 5000
        "https://www.tcgtraders.app/download/unified_card_database.db"
    ]
    
    for server_url in servers:
        try:
            print(f"[*] Trying {server_url}...")
            import urllib.request
            import ssl
            
            # Create SSL context that doesn't verify certificates for local network
            if server_url.startswith("http://"):
                context = None
            else:
                context = ssl._create_unverified_context()
            
            # Download with progress indication
            def reporthook(count, block_size, total_size):
                if total_size > 0:
                    percent = int(count * block_size * 100 / total_size)
                    print(f"\r[*] Downloading: {percent}%", end='', flush=True)
            
            if context:
                urllib.request.urlretrieve(server_url, db_path, reporthook=reporthook, context=context)
            else:
                urllib.request.urlretrieve(server_url, db_path, reporthook=reporthook)
            print(f"\n[+] Successfully downloaded database from {server_url}")
            return True
            
        except Exception as e:
            print(f"\n[!] Failed to download from {server_url}: {e}")
            continue
    
    print("[!] Could not download database from any server")
    return False

class OptimizedCardScanner:
    def __init__(self, db_path="unified_card_database.db", max_workers=8, cache_enabled=True, 
                 serial_port=None, baud_rate=9600):
        """Initialize optimized scanner"""
        self.db_path = db_path
        self.max_workers = max_workers
        self.cache_enabled = cache_enabled
        
        # Download database if it doesn't exist
        if not download_database(db_path):
            raise FileNotFoundError(f"Database not found and could not be downloaded: {db_path}")
        
        # Main database connection
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.cursor = self.conn.cursor()
        
        # Thread-local storage for database connections
        self.local = threading.local()
        
        # Hash cache for popular games (Magic, Pokemon, YuGiOh)
        self.hash_cache = {}
        
        # Load games list and find actual table names from database
        self.games = {}
        games_data = self.cursor.execute("SELECT id, name, display_name, total_cards FROM games").fetchall()
        
        # Get all actual card table names from database
        all_tables = self.cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'cards_%'").fetchall()
        table_set = {t[0] for t in all_tables}
        
        for game_id, name, display_name, total_cards in games_data:
            # Clean the game name for table lookup
            clean_name = name.lower()
            # Remove/replace special characters
            for char in [" ", "-", ":", "!", "&", ".", "/", "(", ")", "'", ","]:
                clean_name = clean_name.replace(char, "_")
            # Remove consecutive underscores
            while "__" in clean_name:
                clean_name = clean_name.replace("__", "_")
            clean_name = clean_name.strip("_")
            
            # Try different table name patterns
            possible_tables = [
                f'cards_{clean_name}',  # Standard pattern
                f'cards_g_{clean_name}',  # Prefixed with g_ (for games starting with numbers)
            ]
            
            # Also try without underscores in some places
            if "_" in clean_name:
                # Try removing underscores after certain punctuation removals
                alt_clean = name.lower().replace(" ", "_")
                for char in ["-", ":", "!", ".", "/", "(", ")", "'"]:
                    alt_clean = alt_clean.replace(char, "")
                alt_clean = alt_clean.replace("&", "")
                while "__" in alt_clean:
                    alt_clean = alt_clean.replace("__", "_")
                alt_clean = alt_clean.strip("_")
                possible_tables.append(f'cards_{alt_clean}')
                possible_tables.append(f'cards_g_{alt_clean}')
            
            # Find matching table
            actual_table = None
            for candidate in possible_tables:
                if candidate in table_set:
                    actual_table = candidate
                    break
            
            if actual_table:
                self.games[name] = {
                    'id': game_id,
                    'display_name': display_name,
                    'total_cards': total_cards,
                    'table': actual_table
                }
        
        print(f"[+] Loaded {len(self.games)} games")
        print(f"[+] Max workers: {max_workers}")
        print(f"[+] Hash caching: {'enabled' if cache_enabled else 'disabled'}")
        
        # Default: scan all games
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
        
        # Performance stats
        self.stats = {
            'scans': 0,
            'total_time': 0,
            'cards_checked': 0,
            'cache_hits': 0
        }
    
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
        self.active_games = [g for g in game_names if g in self.games]
        print(f"[+] Active games: {len(self.active_games)}")
    
    def preload_cache(self, games=['Magic', 'Pokemon', 'YuGiOh']):
        """Preload hash cache for specified games (WARNING: memory intensive)"""
        if not self.cache_enabled:
            return
        
        print("\n[*] Preloading hash cache...")
        for game_name in games:
            if game_name not in self.games:
                continue
            
            print(f"    Loading {game_name}...", end='', flush=True)
            start = time.time()
            
            table = self.games[game_name]['table']
            try:
                # Use a permissive SELECT so we can map columns by name later
                try:
                    query = f"SELECT * FROM {table} WHERE r_phash IS NOT NULL"
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
        """Compute perceptual hash (256-bit, hash_size=16)"""
        if isinstance(image, np.ndarray):
            rgb_image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
            pil_image = Image.fromarray(rgb_image)
        else:
            pil_image = image
        
        r, g, b = pil_image.split()
        
        r_hash = str(imagehash.phash(Image.merge('L', [r]), hash_size=16))
        g_hash = str(imagehash.phash(Image.merge('L', [g]), hash_size=16))
        b_hash = str(imagehash.phash(Image.merge('L', [b]), hash_size=16))
        
        return r_hash, g_hash, b_hash
    
    def hamming_distance(self, hash1, hash2):
        """Fast Hamming distance calculation"""
        if not hash1 or not hash2:
            return 999
        
        try:
            return bin(int(hash1, 16) ^ int(hash2, 16)).count('1')
        except:
            return 999
    
    def quick_filter(self, hash1, hash2, max_dist=50):
        """
        Quick rejection filter using single channel
        Returns True if worth checking all channels
        """
        dist = self.hamming_distance(hash1, hash2)
        return dist <= max_dist
    
    def scan_game(self, game_name, r_hash, g_hash, b_hash, threshold, found_exact, set_filter=None, foil_type_filter=None, rarity_filter=None):
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
            # Check cache first
            if game_name in self.hash_cache:
                cards = self.hash_cache[game_name]
                self.stats['cache_hits'] += 1
            else:
                # Build query with optional set filter
                if set_filter:
                    placeholders = ','.join(['?' for _ in set_filter])
                    # Use SELECT * and filter by set_code; convert rows to dicts
                    try:
                        query = f"SELECT * FROM {table} WHERE r_phash IS NOT NULL AND UPPER(set_code) IN ({placeholders})"
                        rows = cursor.execute(query, [s.upper() for s in set_filter]).fetchall()
                        colnames = [d[0] for d in cursor.description]
                        cards = [dict(zip(colnames, row)) for row in rows]
                    except sqlite3.OperationalError:
                        cards = []
                else:
                    try:
                        query = f"SELECT * FROM {table} WHERE r_phash IS NOT NULL"
                        rows = cursor.execute(query).fetchall()
                        colnames = [d[0] for d in cursor.description]
                        cards = [dict(zip(colnames, row)) for row in rows]
                    except sqlite3.OperationalError:
                        cards = []
            
            # Scan cards with early termination
            for card in cards:
                if found_exact.is_set():
                    break  # Exact match found by another thread

                # Card is a dict (from cache or direct query)
                name = card.get('name') or card.get('card_name') or 'Unknown'
                number = card.get('number')
                card_r = card.get('r_phash')
                card_g = card.get('g_phash')
                card_b = card.get('b_phash')
                set_code = card.get('set_code') or card.get('set') or card.get('setCode')
                rarity = card.get('rarity')
                subTypeName = card.get('subTypeName') or card.get('sub_type_name') or card.get('subtype')
                unique_id_value = card.get('unique_id') or card.get('uid') or card.get('id')
                market_price = card.get('market_price') or card.get('price') or card.get('marketPrice')
                low_price = card.get('low_price') or card.get('lowPrice') or card.get('lowprice')

                # Apply set filter if using cache
                if set_filter and game_name in self.hash_cache:
                    if not set_code or set_code.upper() not in [s.upper() for s in set_filter]:
                        continue

                # Apply foil type filter
                if foil_type_filter and subTypeName:
                    if subTypeName not in foil_type_filter:
                        continue

                # Apply rarity filter
                if rarity_filter and rarity:
                    if rarity.upper() not in [r.upper() for r in rarity_filter]:
                        continue

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

                    # Determine canonical unique_id preference order (ignore product_id)
                    unique_id = unique_id_value

                    # Price fallback: prefer market_price/price, then low_price variants
                    price_value = None
                    try:
                        if market_price is not None and float(market_price) > 0:
                            price_value = float(market_price)
                        elif low_price is not None and float(low_price) > 0:
                            price_value = float(low_price)
                    except Exception:
                        price_value = None

                    matches.append({
                        'game': game_info['display_name'],
                        'name': name,
                        'number': number,
                        'set': set_code,
                        'rarity': rarity,
                        'foil_type': subTypeName,
                        'unique_id': unique_id,
                        'market_price': price_value,
                        'distance': avg_distance,
                        'confidence': confidence,
                        'dist_r': dist_r,
                        'dist_g': dist_g,
                        'dist_b': dist_b
                    })

                    # Exact match found!
                    if avg_distance == 0:
                        found_exact.set()
                        break
            
            self.stats['cards_checked'] += len(cards)
        
        except sqlite3.OperationalError:
            pass
        
        return matches
    
    def scan_card(self, image, threshold=10, top_n=10, set_filter=None, foil_type_filter=None, rarity_filter=None):
        """
        Multi-threaded card scanning with early termination
        
        Args:
            set_filter: List of set codes to filter by (None = all sets)
            foil_type_filter: List of foil types (subTypeName values) to filter by (None = all types)
            rarity_filter: List of rarity codes to filter by (None = all rarities)
        """
        start_time = time.time()
        
        # Compute hash
        r_hash, g_hash, b_hash = self.compute_phash(image)
        
        # Event for early termination when exact match found
        found_exact = threading.Event()
        
        all_matches = []
        
        # Submit game scans to thread pool
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
                    rarity_filter
                ): game_name 
                for game_name in self.active_games
            }
            
            # Collect results as they complete
            for future in as_completed(futures):
                matches = future.result()
                all_matches.extend(matches)
                
                # If exact match found, cancel remaining tasks
                if found_exact.is_set():
                    break
        
        # Sort by distance
        # Deduplicate matches by unique_id if present, otherwise by (game,name,number,set)
        deduped = {}
        for m in all_matches:
            key = m.get('unique_id') if m.get('unique_id') is not None else (m['game'], m['name'], m['number'], m['set'])
            if key in deduped:
                # Keep the one with smaller distance
                if m['distance'] < deduped[key]['distance']:
                    deduped[key] = m
            else:
                deduped[key] = m

        all_matches = list(deduped.values())
        all_matches.sort(key=lambda x: x['distance'])
        
        # Update stats
        elapsed = time.time() - start_time
        self.stats['scans'] += 1
        self.stats['total_time'] += elapsed
        
        return all_matches[:top_n], elapsed
    
    def scan_from_file(self, image_path, threshold=10, top_n=10, set_filter=None, foil_type_filter=None, rarity_filter=None):
        """Scan from image file"""
        image = cv2.imread(str(image_path))
        if image is None:
            print(f"[!] Failed to load image: {image_path}")
            return [], 0
        
        return self.scan_card(image, threshold, top_n, set_filter, foil_type_filter, rarity_filter)
    
    def adaptive_scan(self, image, max_threshold=20, target_matches=5, set_filter=None, foil_type_filter=None, rarity_filter=None):
        """
        Adaptive threshold scanning
        Starts strict, gradually relaxes until target matches found
        """
        print("\n[*] Adaptive scan mode...")
        
        for threshold in range(5, max_threshold + 1, 2):
            matches, elapsed = self.scan_card(image, threshold=threshold, top_n=target_matches, set_filter=set_filter, foil_type_filter=foil_type_filter, rarity_filter=rarity_filter)
            
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
        print(f"Total time: {self.stats['total_time']:.2f}s")
        if self.stats['scans'] > 0:
            avg_time = self.stats['total_time'] / self.stats['scans']
            print(f"Average scan time: {avg_time:.2f}s")
        print(f"Total cards checked: {self.stats['cards_checked']:,}")
        print(f"Cache hits: {self.stats['cache_hits']}")
        print("=" * 80)
    
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
        print(f"Arduino: {'Connected' if self.ser else 'Disabled'}")
        print("\nPress 'q' to quit")
        print("=" * 80)
        
        # Open webcam
        cap = cv2.VideoCapture(0)
        if not cap.isOpened():
            print("[!] Cannot open webcam")
            return
        
        frame_count = 0
        total_processing_time = 0
        
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
                
                # Exit on 'q'
                if cv2.waitKey(1) & 0xFF == ord('q'):
                    break
        
        finally:
            cap.release()
            cv2.destroyAllWindows()
            print(f"\n[+] Processed {frame_count} frames")
            if frame_count > 0:
                print(f"[+] Average processing time: {total_processing_time/frame_count:.3f}s")
    
    def _find_card_contour(self, frame):
        """
        Find card contour in frame
        This is a placeholder - you should import or implement proper card detection
        """
        # Simple implementation - convert to grayscale and find largest rectangle
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        blur = cv2.GaussianBlur(gray, (5, 5), 0)
        _, thresh = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        
        contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        
        if not contours:
            return None
        
        # Find largest rectangular contour
        for contour in sorted(contours, key=cv2.contourArea, reverse=True)[:5]:
            peri = cv2.arcLength(contour, True)
            approx = cv2.approxPolyDP(contour, 0.02 * peri, True)
            
            if len(approx) == 4 and cv2.contourArea(approx) > 10000:
                return approx
        
        return None
    
    def _process_card_from_contour(self, frame, card_approx):
        """
        Extract and process card from contour
        Returns card info dict or None
        """
        try:
            # Get perspective-corrected card
            warped = self._get_perspective_corrected_card(frame, card_approx)
            
            if warped is None:
                return None
            
            # Crop to hash region (top-left corner)
            crop_size = 745
            cropped = warped[:crop_size, :crop_size]
            
            # Convert to PIL for hashing
            rgb_image = cv2.cvtColor(cropped, cv2.COLOR_BGR2RGB)
            pil_image = Image.fromarray(rgb_image)
            
            # Scan the card
            matches, _ = self.scan_card(pil_image, threshold=10, top_n=1)
            
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
            pts = card_approx.reshape(4, 2).astype(np.float32)
            
            # Order points: top-left, top-right, bottom-right, bottom-left
            rect = self._order_points(pts)
            
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
            
            return warped
        
        except Exception as e:
            print(f"[!] Perspective correction error: {e}")
            return None
    
    @staticmethod
    def _order_points(pts):
        """Order points in clockwise order starting from top-left"""
        rect = np.zeros((4, 2), dtype=np.float32)
        
        s = pts.sum(axis=1)
        rect[0] = pts[np.argmin(s)]  # top-left
        rect[2] = pts[np.argmax(s)]  # bottom-right
        
        diff = np.diff(pts, axis=1)
        rect[1] = pts[np.argmin(diff)]  # top-right
        rect[3] = pts[np.argmax(diff)]  # bottom-left
        
        return rect
    
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
    parser.add_argument('--cache', action='store_true', help='Preload hash cache for Magic, Pokemon, YuGiOh')
    parser.add_argument('--game', '-g', action='append', help='Limit to specific game(s). Can be used multiple times. Example: -g Magic -g Pokemon')
    parser.add_argument('--set', '-s', action='append', help='Limit to specific set code(s). Can be used multiple times. Example: -s LEA -s LEB')
    parser.add_argument('--foil-type', '-f', action='append', help='Filter by foil type (subTypeName). Can be used multiple times. Example: -f Foil -f Normal')
    parser.add_argument('--rarity', '-r', action='append', help='Filter by rarity. Can be used multiple times. Example: -r M -r R')
    parser.add_argument('--list-games', action='store_true', help='List all available games and exit')
    parser.add_argument('--list-foil-types', metavar='GAME', help='List all foil types (subTypeName values) for a specific game and exit')
    parser.add_argument('--list-rarities', metavar='GAME', help='List all rarities for a specific game and exit')
    parser.add_argument('--list-sets', metavar='GAME', help='List all sets for a specific game and exit')
    parser.add_argument('--threshold', '-t', type=int, default=10, help='Match threshold (default: 10, lower = stricter)')
    parser.add_argument('--top', '-n', type=int, default=10, help='Number of top matches to show (default: 10)')
    parser.add_argument('--min-confidence', '-c', type=float, default=85.0, help='Minimum confidence (percent) for Method 1 to be considered reasonable (default: 85.0)')
    
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
    scanner = OptimizedCardScanner(max_workers=8, cache_enabled=True,
                                   serial_port=args.serial_port,
                                   baud_rate=args.baud_rate)
    
    # Enable inventory tracking if requested
    if args.track_inventory:
        scanner.enable_inventory_tracking(True)
    
    # Initialize serial if port specified
    if args.serial_port:
        scanner.init_serial()
    
    # List games and exit
    if args.list_games:
        print("\nAvailable games (317 total):")
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
        # Try to parse as game ID first
        matched_game = None
        
        try:
            game_id = int(args.list_foil_types)
            # Find game by ID
            for name, info in scanner.games.items():
                if info['id'] == game_id:
                    matched_game = (name, info)
                    break
        except ValueError:
            # Not a number, try matching by name (case-insensitive, partial match)
            matched_games = [(name, info) for name, info in scanner.games.items() 
                            if args.list_foil_types.lower() in name.lower()]
            if matched_games:
                if len(matched_games) == 1:
                    matched_game = matched_games[0]
                else:
                    print(f"\n[!] Multiple games found matching '{args.list_foil_types}':")
                    for name, info in matched_games:
                        print(f"    ID {info['id']}: {info['display_name']}")
                    print(f"\nPlease use a specific game ID: --list-foil-types <ID>")
                    scanner.close()
                    return
        
        if not matched_game:
            print(f"\n[!] No game found matching: {args.list_foil_types}")
            print("    Use --list-games to see available games with IDs")
            scanner.close()
            return
        
        game_name, game_info = matched_game
        table = game_info['table']
        
        print(f"\n{'=' * 80}")
        print(f"Foil Types for: {game_info['display_name']} (Game ID: {game_info['id']})")
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
        # Try to parse as game ID first
        matched_game = None
        
        try:
            game_id = int(args.list_rarities)
            # Find game by ID
            for name, info in scanner.games.items():
                if info['id'] == game_id:
                    matched_game = (name, info)
                    break
        except ValueError:
            # Not a number, try matching by name (case-insensitive, partial match)
            matched_games = [(name, info) for name, info in scanner.games.items() 
                            if args.list_rarities.lower() in name.lower()]
            if matched_games:
                if len(matched_games) == 1:
                    matched_game = matched_games[0]
                else:
                    print(f"\n[!] Multiple games found matching '{args.list_rarities}':")
                    for name, info in matched_games:
                        print(f"    ID {info['id']}: {info['display_name']}")
                    print(f"\nPlease use a specific game ID: --list-rarities <ID>")
                    scanner.close()
                    return
        
        if not matched_game:
            print(f"\n[!] No game found matching: {args.list_rarities}")
            print("    Use --list-games to see available games with IDs")
            scanner.close()
            return
        
        game_name, game_info = matched_game
        table = game_info['table']
        
        print(f"\n{'=' * 80}")
        print(f"Rarities for: {game_info['display_name']} (Game ID: {game_info['id']})")
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
        # Try to parse as game ID first
        game_id = None
        matched_game = None
        
        try:
            game_id = int(args.list_sets)
            # Find game by ID
            for name, info in scanner.games.items():
                if info['id'] == game_id:
                    matched_game = (name, info)
                    break
        except ValueError:
            # Not a number, try matching by name (case-insensitive, partial match)
            matched_games = [(name, info) for name, info in scanner.games.items() 
                            if args.list_sets.lower() in name.lower()]
            if matched_games:
                if len(matched_games) == 1:
                    matched_game = matched_games[0]
                else:
                    print(f"\n[!] Multiple games found matching '{args.list_sets}':")
                    for name, info in matched_games:
                        print(f"    ID {info['id']}: {info['display_name']}")
                    print(f"\nPlease use a specific game ID: --list-sets <ID>")
                    scanner.close()
                    return
        
        if not matched_game:
            print(f"\n[!] No game found matching: {args.list_sets}")
            print("    Use --list-games to see available games with IDs")
            scanner.close()
            return
        
        game_name, game_info = matched_game
        
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
        print_sorting_options()
        
        # Get sorting mode
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
        
        # Ask about inventory tracking (for set/buy modes)
        if choice in ["3", "6"]:
            track_inv = input("Track inventory (reject duplicates)? (Y/N): ").strip().upper()
            if track_inv == "Y":
                scanner.enable_inventory_tracking(True)
        
        # Optionally preload cache
        if args.cache:
            cache_games = ['Magic', 'Pokemon', 'YuGiOh']
            scanner.preload_cache(cache_games)
        
        # Run realtime mode
        scanner.run_realtime_mode(sorting_mode=sorting_mode, 
                                 threshold=threshold)
        
        scanner.print_stats()
        scanner.close()
        return
    
    # =====================================================================
    # COMMAND LINE MODE (Original functionality)
    # =====================================================================
    
    # Filter by games if specified
    if args.game:
        valid_games = []
        for game_filter in args.game:
            # Try to parse as game ID first
            try:
                game_id = int(game_filter)
                # Find game by ID
                matched = [name for name, info in scanner.games.items() if info['id'] == game_id]
                valid_games.extend(matched)
            except ValueError:
                # Match game names (case-insensitive, partial match)
                matched = [name for name in scanner.games.keys() 
                          if game_filter.lower() in name.lower()]
                valid_games.extend(matched)
        
        if valid_games:
            scanner.set_active_games(valid_games)
            print(f"\n[+] Filtering to {len(valid_games)} game(s):")
            for game in valid_games:
                print(f"    - ID {scanner.games[game]['id']}: {scanner.games[game]['display_name']}")
        else:
            print(f"\n[!] No games matched filter: {args.game}")
            print("    Use --list-games to see available games with IDs")
            scanner.close()
            return
    
    # Optionally preload cache for top games (uses ~500MB RAM)
    if args.cache:
        cache_games = ['Magic', 'Pokemon', 'YuGiOh']
        # Only cache active games
        if args.game:
            cache_games = [g for g in cache_games if g in scanner.active_games]
        if cache_games:
            scanner.preload_cache(cache_games)
    
    if args.image:
        image_path = args.image
        print(f"\n[*] Scanning image: {image_path}")
        
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
            if not args.set:
                print("\n\n[Method 2] Adaptive threshold (running because Method 1 was not confident enough):")
                image = cv2.imread(image_path)
                adapt_matches, adapt_elapsed = scanner.adaptive_scan(image, max_threshold=20, target_matches=5, set_filter=set_filter, foil_type_filter=foil_type_filter, rarity_filter=rarity_filter)
                
                print(f"\n[+] Adaptive best matches:")
                for i, match in enumerate(adapt_matches[:5], 1):
                    price = match.get('market_price')
                    price_str = f"${price:.2f}" if price else "N/A"
                    print(f"{i}. {match['name']} - {match['confidence']:.1f}% ({match['distance']:.2f}) - Rarity: {match.get('rarity', 'N/A')} - Price: {price_str}")
            else:
                print("\n[+] Set filter present — adaptive scan skipped to keep results focused on the selected set(s).")
        
        scanner.print_stats()
    else:
        print("\nUsage:")
        print("  python optimized_scanner.py <image_path> [options]")
        print("\nModes:")
        print("  --interactive        Interactive mode with sorting menu (like original)")
        print("  --realtime           Realtime webcam scanning mode")
        print("\nOptions:")
        print("  --cache              Preload hash cache for Magic, Pokemon, YuGiOh (faster but uses RAM)")
        print("  -g, --game GAME      Limit to specific game(s) by ID or name. Can be used multiple times.")
        print("  -s, --set SET        Limit to specific set code(s) or ID(s). Can be used multiple times.")
        print("  -f, --foil-type TYPE Filter by foil type (subTypeName). Can be used multiple times.")
        print("  -r, --rarity RARITY  Filter by rarity code. Can be used multiple times.")
        print("  -t, --threshold N    Match threshold (default: 10, lower = stricter)")
        print("  -n, --top N          Number of top matches to show (default: 10)")
        print("  --serial-port PORT   Serial port for Arduino (e.g., COM3)")
        print("  --baud-rate BAUD     Serial baud rate (default: 9600)")
        print("  --track-inventory    Enable inventory tracking (reject duplicates)")
        print("  --list-games         List all available games with their IDs")
        print("  --list-sets GAME     List all sets for a specific game (by ID or name)")
        print("  --list-foil-types GAME   List all foil types for a specific game")
        print("  --list-rarities GAME     List all rarities for a specific game")
        print("\nExamples:")
        print("  python optimized_scanner.py --interactive --serial-port COM3")
        print("  python optimized_scanner.py --realtime --cache --track-inventory")
        print("  python optimized_scanner.py card.jpg --cache")
        print("  python optimized_scanner.py card.jpg -g 167              # Magic by ID")
        print("  python optimized_scanner.py card.jpg -g Magic -g Pokemon # By name")
        print("  python optimized_scanner.py card.jpg -g 311 -s 1234      # YuGiOh set by ID")
        print("  python optimized_scanner.py card.jpg -f Foil -r M        # Foil mythic rares only")
        print("  python optimized_scanner.py card.jpg -f Normal -r R -r M # Normal rares/mythics")
        print("  python optimized_scanner.py --list-games                 # Show all games with IDs")
        print("  python optimized_scanner.py --list-sets 167              # Magic sets by game ID")
        print("  python optimized_scanner.py --list-rarities Magic        # Show Magic rarities")
        print("  python optimized_scanner.py --list-foil-types 167        # Show Magic foil types")
    
    scanner.close()

if __name__ == '__main__':
    main()

