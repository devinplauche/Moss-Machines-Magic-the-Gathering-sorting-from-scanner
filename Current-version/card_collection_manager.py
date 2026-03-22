#!/usr/bin/env python3
"""
Card Collection Manager
Handles saving scanned cards and exporting to multiple formats
Supports both TCGTraders SKU format and TCGPlayer text format
"""
import os
import json
import csv
from datetime import datetime
from pathlib import Path

class CardCollectionManager:
    """Manages scanned card collections with export to multiple formats"""
    
    def __init__(self, collection_dir="Collection"):
        self.collection_dir = Path(collection_dir)
        self.collection_dir.mkdir(exist_ok=True)
        
        # Collection file paths
        self.master_file = self.collection_dir / "master_collection.json"
        self.session_file = self.collection_dir / "current_session.json"
        
        # Load existing collections
        self.master_collection = self._load_collection(self.master_file)
        self.current_session = self._load_collection(self.session_file)
        
        # Statistics
        self.stats = {
            'total_scans': 0,
            'session_scans': 0,
            'games': {}
        }
        self._update_stats()
    
    def _load_collection(self, filepath):
        """Load collection from JSON file"""
        if filepath.exists():
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                print(f"[!] Error loading {filepath}: {e}")
        return {'cards': [], 'metadata': {'created': datetime.now().isoformat()}}
    
    def _save_collection(self, collection, filepath):
        """Save collection to JSON file"""
        try:
            collection['metadata']['last_updated'] = datetime.now().isoformat()
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(collection, f, indent=2, ensure_ascii=False)
            return True
        except Exception as e:
            print(f"[!] Error saving {filepath}: {e}")
            return False
    
    def _update_stats(self):
        """Update collection statistics"""
        self.stats['total_scans'] = len(self.master_collection.get('cards', []))
        self.stats['session_scans'] = len(self.current_session.get('cards', []))
        
        # Count by game
        for card in self.master_collection.get('cards', []):
            game = card.get('game', 'Unknown')
            self.stats['games'][game] = self.stats['games'].get(game, 0) + 1
    
    def add_card(self, card_info, quantity=1, condition='Near Mint', language='EN', is_foil=False):
        """
        Add a scanned card to the collection
        
        Args:
            card_info: Dictionary with card details from scanner
            quantity: Number of copies scanned
            condition: Card condition (Near Mint, Lightly Played, etc.)
            language: Card language code (EN, JP, FR, etc.)
            is_foil: Whether the card is foil
        """
        # Generate TCGTraders SKU
        product_id = card_info.get('product_id') or card_info.get('UniqueID')
        variant_type = 'GRADE' if condition.startswith('PSA') or condition.startswith('BGS') else 'CONDITION'
        
        # Map condition to short code
        condition_map = {
            'Near Mint': 'NM',
            'Lightly Played': 'LP',
            'Moderately Played': 'MP',
            'Heavily Played': 'HP',
            'Damaged': 'DMG',
            'Mint': 'M'
        }
        
        # Extract grade value if graded
        variant_value = condition_map.get(condition, 'NM')
        if variant_type == 'GRADE':
            # Extract numeric grade (e.g., "PSA 10" -> "10")
            import re
            match = re.search(r'(\d+)', condition)
            variant_value = match.group(1) if match else '10'
        
        # Generate SKU: {product_id}_{variant_value}_{language}_{foil}
        foil_suffix = 'F' if is_foil else 'N'
        sku = f"{product_id}_{variant_value}_{language}_{foil_suffix}"
        
        # Create card entry
        card_entry = {
            'product_id': product_id,
            'sku': sku,
            'name': card_info.get('name') or card_info.get('cardName'),
            'set_code': card_info.get('set_code') or card_info.get('set') or card_info.get('setName'),
            'set_name': card_info.get('setName') or card_info.get('extSet'),
            'game': card_info.get('Game') or card_info.get('game'),
            'rarity': card_info.get('rarity') or card_info.get('extRarity'),
            'number': card_info.get('number') or card_info.get('cardNumber') or card_info.get('extNumber'),
            'quantity': quantity,
            'condition': condition,
            'language': language,
            'is_foil': is_foil,
            'variant_type': variant_type,
            'variant_value': variant_value,
            'price': card_info.get('market_price') or card_info.get('price'),
            'timestamp': datetime.now().isoformat(),
            # Optional scan metadata (used by calibration submissions)
            'scan_image_path': card_info.get('scan_image_path') or card_info.get('scanImagePath'),
            'scan_backend': card_info.get('scan_backend') or card_info.get('backend'),
            'scan_confidence': card_info.get('confidence'),
            'scan_hash': card_info.get('scan_hash')
        }
        
        # Add to both collections
        self.master_collection['cards'].append(card_entry)
        self.current_session['cards'].append(card_entry)
        
        # Update stats
        self.stats['total_scans'] += 1
        self.stats['session_scans'] += 1
        game = card_entry['game']
        self.stats['games'][game] = self.stats['games'].get(game, 0) + 1
        
        # Auto-save
        self._save_collection(self.master_collection, self.master_file)
        self._save_collection(self.current_session, self.session_file)
        
        return card_entry
    
    def export_tcgtraders_csv(self, filename=None, game_filter=None):
        """
        Export collection in TCGTraders CSV format (our website)
        
        Format: Quantity,SKU,Name,Set,Condition,Language
        """
        if filename is None:
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            filename = self.collection_dir / f"traders_export_{timestamp}.csv"
        
        cards = self.master_collection.get('cards', [])
        
        # Filter by game if specified
        if game_filter:
            cards = [c for c in cards if c.get('game') == game_filter]
        
        try:
            with open(filename, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                
                # Write header - TCGTraders import is strictly on SKU
                writer.writerow(['Quantity', 'SKU'])
                
                # Write cards
                for card in cards:
                    writer.writerow([
                        card.get('quantity', 1),
                        card.get('sku', '')
                    ])
            
            print(f"[+] Exported {len(cards)} cards to {filename}")
            return str(filename)
        
        except Exception as e:
            print(f"[!] Error exporting to TCGTraders CSV: {e}")
            return None
    
    def export_tcgplayer_text(self, filename=None, game_filter=None):
        """
        Export collection in TCGPlayer text format
        
        Format (simple): 
        4 Jace, the Mind Sculptor
        1 Stomping Ground [GTC] (Foil, Russian)
        
        Format (CSV with headers):
        Quantity,Name,Set Code,Printing,Condition,Language
        """
        if filename is None:
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            filename = self.collection_dir / f"tcgplayer_export_{timestamp}.txt"
        
        cards = self.master_collection.get('cards', [])
        
        # Filter by game if specified
        if game_filter:
            cards = [c for c in cards if c.get('game') == game_filter]
        
        try:
            with open(filename, 'w', encoding='utf-8') as f:
                # Write CSV header
                f.write("Quantity,Name,Set Code,Printing,Condition,Language\n")
                
                # Write cards in CSV format
                for card in cards:
                    quantity = card.get('quantity', 1)
                    name = card.get('name', '')
                    set_code = card.get('set_code', '')
                    
                    # Printing info (Foil, Non-Foil)
                    printing = 'Foil' if card.get('is_foil') else 'Normal'
                    
                    # Condition
                    condition = card.get('condition', 'Near Mint')
                    
                    # Language
                    language = card.get('language', 'English')
                    # Map language codes to full names
                    lang_map = {
                        'EN': 'English',
                        'JP': 'Japanese',
                        'FR': 'French',
                        'DE': 'German',
                        'ES': 'Spanish',
                        'IT': 'Italian',
                        'PT': 'Portuguese',
                        'KR': 'Korean',
                        'CN': 'Chinese Simplified',
                        'TW': 'Chinese Traditional',
                        'RU': 'Russian'
                    }
                    language_full = lang_map.get(language, language)
                    
                    # Write CSV line
                    f.write(f'{quantity},"{name}",{set_code},{printing},{condition},{language_full}\n')
            
            print(f"[+] Exported {len(cards)} cards to {filename} (TCGPlayer CSV format)")
            return str(filename)
        
        except Exception as e:
            print(f"[!] Error exporting to TCGPlayer text: {e}")
            return None
    
    def export_by_game(self, format_type='both'):
        """
        Export collections separated by game
        
        Args:
            format_type: 'tcgtraders', 'tcgplayer', or 'both'
        
        Returns:
            Dictionary of exported files by game
        """
        exports = {}
        
        # Get unique games
        games = set(card.get('game') for card in self.master_collection.get('cards', []))
        
        for game in games:
            if not game:
                continue
            
            game_safe = game.replace(' ', '_').replace(':', '')
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            
            if format_type in ['tcgtraders', 'both']:
                filename = self.collection_dir / f"tcgtraders_{game_safe}_{timestamp}.csv"
                result = self.export_tcgtraders_csv(filename, game_filter=game)
                if result:
                    exports[f"{game}_tcgtraders"] = result
            
            if format_type in ['tcgplayer', 'both']:
                filename = self.collection_dir / f"tcgplayer_{game_safe}_{timestamp}.txt"
                result = self.export_tcgplayer_text(filename, game_filter=game)
                if result:
                    exports[f"{game}_tcgplayer"] = result
        
        return exports
    
    def clear_session(self):
        """Clear the current session (keep master collection)"""
        self.current_session = {'cards': [], 'metadata': {'created': datetime.now().isoformat()}}
        self._save_collection(self.current_session, self.session_file)
        self.stats['session_scans'] = 0
        print("[+] Session cleared")
    
    def get_stats(self):
        """Get collection statistics"""
        self._update_stats()
        return self.stats
    
    def print_summary(self):
        """Print a summary of the collection"""
        stats = self.get_stats()
        
        print("\n" + "=" * 60)
        print("COLLECTION SUMMARY")
        print("=" * 60)
        print(f"Total cards scanned: {stats['total_scans']}")
        print(f"Current session: {stats['session_scans']}")
        print(f"\nCards by game:")
        for game, count in sorted(stats['games'].items(), key=lambda x: x[1], reverse=True):
            print(f"  {game}: {count} cards")
        print("=" * 60)


if __name__ == "__main__":
    # Demo/Test
    manager = CardCollectionManager()
    
    # Example card
    test_card = {
        'product_id': '288162',
        'name': 'Jace, the Mind Sculptor',
        'set_code': 'WWK',
        'setName': 'Worldwake',
        'Game': 'Magic',
        'rarity': 'Mythic Rare',
        'number': '31',
        'market_price': 89.99
    }
    
    # Add some test cards
    manager.add_card(test_card, quantity=4, condition='Near Mint', language='EN', is_foil=False)
    manager.add_card(test_card, quantity=1, condition='Near Mint', language='JP', is_foil=True)
    
    # Print summary
    manager.print_summary()
    
    # Export examples
    print("\n[*] Exporting collections...")
    manager.export_tcgtraders_csv()
    manager.export_tcgplayer_text()
    
    print("\n[*] Exporting by game...")
    exports = manager.export_by_game(format_type='both')
    for name, filepath in exports.items():
        print(f"  {name}: {filepath}")
