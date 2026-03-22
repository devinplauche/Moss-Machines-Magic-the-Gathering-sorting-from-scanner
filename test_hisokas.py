#!/usr/bin/env python3
"""
Test script: Recognize Hisokas Defiance from local JPG
Tests the scanner's card recognition capability
"""
import sys
import os
from PIL import Image
import cv2

# Add Current-Version to path
sys.path.insert(0, r'Current-Version')

from optimized_scanner import OptimizedCardScanner

def test_card_recognition():
    """Test recognizing Hisokas Defiance card"""
    
    print("\n" + "=" * 80)
    print("CARD RECOGNITION TEST: Hisokas Defiance")
    print("=" * 80)
    
    # Image path
    image_path = r'Current-Version\hisokas-defiance.jpg'
    print(f"\nImage: {image_path}")
    print(f"Exists: {os.path.exists(image_path)}")
    
    if not os.path.exists(image_path):
        print("ERROR: Image file not found!")
        return False
    
    try:
        # Load image
        img_pil = Image.open(image_path)
        print(f"Image size: {img_pil.size}")
        print(f"Image mode: {img_pil.mode}")
        
        # Initialize scanner with correct database path
        print("\nInitializing scanner...")
        db_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), 
            'Current-Version', 
            'unified_card_database.db'
        )
        print(f"Database: {db_path}")
        
        scanner = OptimizedCardScanner(
            db_path=db_path,
            max_workers=8,
            cache_enabled=True
        )
        print(f"✅ Scanner ready with {len(scanner.games)} games")
        
        # Recognize the card
        print("\nScanning card (this may take a few seconds)...")
        results, elapsed_time = scanner.scan_card(img_pil, threshold=40, top_n=10)
        
        print(f"\n📊 RESULTS - Top 10 Matches (scan time: {elapsed_time:.2f}s):")
        print("-" * 80)
        
        if not results:
            print("❌ No matches found!")
            scanner.close()
            return False
        
        target_found = False
        for idx, result in enumerate(results, 1):
            card_name = result.get('name', 'Unknown')
            distance = result.get('distance', 999)
            confidence = result.get('confidence', 0)
            game = result.get('game', 'Unknown')
            
            # Check if this is the target card
            is_target = 'hisokas' in card_name.lower() and 'defiance' in card_name.lower()
            marker = "✅ TARGET FOUND!" if is_target else ""
            
            print(f"{idx}. {card_name:40s} | Distance: {distance:3d} | Conf: {confidence:5.1f}% | {game:30s} {marker}")
            
            if is_target:
                target_found = True
        
        print("-" * 80)
        
        # Summary
        print("\n📈 TEST SUMMARY:")
        if target_found:
            print("✅ SUCCESS: Card recognized as Hisokas Defiance!")
            top_card = results[0].get('name', 'Unknown')
            top_distance = results[0].get('distance', 999)
            print(f"   Top match: {top_card} (distance: {top_distance})")
        else:
            print("❌ FAILED: Hisokas Defiance not found in top 10")
            top_card = results[0].get('name', 'Unknown')
            print(f"   Best match instead: {top_card}")
        
        scanner.close()
        return target_found
        
    except Exception as e:
        print(f"❌ ERROR: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == '__main__':
    success = test_card_recognition()
    sys.exit(0 if success else 1)
