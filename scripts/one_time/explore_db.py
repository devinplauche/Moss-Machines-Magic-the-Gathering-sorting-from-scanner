#!/usr/bin/env python3
"""
Explore available cards in database
"""
import sys
import os
import sqlite3

sys.path.insert(0, r'Current-Version')
from optimized_scanner import OptimizedCardScanner

db_path = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), 
    'Current-Version', 
    'unified_card_database.db'
)

scanner = OptimizedCardScanner(db_path=db_path, max_workers=4)
conn = sqlite3.connect(db_path)
c = conn.cursor()

print("\n" + "=" * 80)
print("DATABASE EXPLORATION")
print("=" * 80)

print(f"\nTotal games: {len(scanner.games)}")
print("\nAll available games:")
for game in sorted(scanner.games.keys()):
    game_info = scanner.games[game]
    # game_info might be a dict with 'table' key
    if isinstance(game_info, dict):
        table = game_info.get('table', f'cards_{game_info.get("id", "?")}')
    else:
        table = game_info
    try:
        c.execute(f"SELECT COUNT(*) FROM [{table}]")
        count = c.fetchone()[0]
        print(f"  {game:40s} ({count:7d} cards)")
    except:
        print(f"  {game:40s} (error reading)")

print("\n" + "=" * 80)
print("SAMPLE CARDS BY GAME")
print("=" * 80)

# Show 3 random cards from each of first 5 games
for game in list(scanner.games.keys())[:5]:
    game_info = scanner.games[game]
    if isinstance(game_info, dict):
        table = game_info.get('table', f'cards_{game_info.get("id", "?")}')
    else:
        table = game_info
    print(f"\n{game}:")
    try:
        c.execute(f"SELECT name, set FROM [{table}] LIMIT 5")
        for name, set_code in c.fetchall():
            print(f"  - {name} ({set_code})")
    except Exception as e:
        print(f"  Error: {e}")

conn.close()
scanner.close()

print("\n" + "=" * 80)
print("Hunter x Hunter or anime-related card games may not be in this database.")
print("The database focuses on: Magic, Pokemon, YuGiOh, and many other mainline TCGs.")
print("=" * 80)
