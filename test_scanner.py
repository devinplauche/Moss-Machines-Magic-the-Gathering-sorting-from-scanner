#!/usr/bin/env python3
import sys
import os

# Add Current-Version to path
sys.path.insert(0, r'Current-Version')

from optimized_scanner import OptimizedCardScanner

db_path = os.path.join(os.path.dirname(os.path.abspath('Current-Version/gui_interface_enhanced.py')), 'unified_card_database.db')
print(f"Testing scanner with db_path: {db_path}")

try:
    scanner = OptimizedCardScanner(db_path=db_path, cache_enabled=True, max_workers=4)
    print(f"✅ Scanner initialized successfully!")
    print(f"   Games loaded: {len(scanner.games)}")
    print(f"   Active games: {len(scanner.active_games)}")
    print(f"   Sample games: {list(scanner.games.keys())[:5]}")
    scanner.close()
except Exception as e:
    print(f"❌ Scanner initialization failed: {e}")
    import traceback
    traceback.print_exc()
