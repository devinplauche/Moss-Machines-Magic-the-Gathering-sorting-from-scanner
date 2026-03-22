#!/usr/bin/env python3
"""
CLI Script for Google Drive Batch Scanning
Run: python scan_google_drive.py --folder-url "<GOOGLE_DRIVE_FOLDER_URL>" [options]
"""
import argparse
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from optimized_scanner import OptimizedCardScanner
from google_drive_scanner import BatchGoogleDriveScanner
import logging


def setup_logging(verbose: bool = False):
    """Setup logging configuration"""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format='%(asctime)s - %(levelname)s - %(message)s'
    )


def main():
    parser = argparse.ArgumentParser(
        description='Scan Magic: The Gathering cards from a Google Drive folder',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
Examples:
  # Scan a folder by URL
  python scan_google_drive.py --folder-url "https://drive.google.com/drive/folders/1g_WOPFaYGWfiKbtLqO3aZAOEQOWnxJLc"
  
  # Scan a folder with output files
  python scan_google_drive.py --folder-url "1g_WOPFaYGWfiKbtLqO3aZAOEQOWnxJLc" \\
    --output-csv results.csv --output-json results.json
  
  # Force re-authentication
  python scan_google_drive.py --folder-url "<URL>" --force-reauth
  
  # Keep downloaded images and process all
  python scan_google_drive.py --folder-url "<URL>" --keep-local
        '''
    )
    
    # Required arguments
    parser.add_argument(
        '--folder-url',
        required=True,
        help='Google Drive folder URL or folder ID'
    )
    
    # Optional arguments
    parser.add_argument(
        '--threshold',
        type=int,
        default=40,
        help='Match threshold for card recognition (default: 40, lower = stricter)'
    )
    
    parser.add_argument(
        '--top-n',
        type=int,
        default=10,
        help='Number of top matches to return (default: 10)'
    )
    
    parser.add_argument(
        '--output-csv',
        help='Path to save results as CSV file'
    )
    
    parser.add_argument(
        '--output-json',
        help='Path to save results as JSON file'
    )

    parser.add_argument(
        '--error-report-csv',
        help='Path to save manual review CSV (low confidence/OCR disagreements)'
    )

    parser.add_argument(
        '--error-report-json',
        help='Path to save manual review JSON (low confidence/OCR disagreements)'
    )

    parser.add_argument(
        '--manual-review-threshold',
        type=float,
        default=75.0,
        help='Confidence threshold for manual review queue (default: 75.0)'
    )
    
    parser.add_argument(
        '--set-filter',
        help='Filter by card set code (optional)'
    )
    
    parser.add_argument(
        '--foil-filter',
        help='Filter by foil type (optional)'
    )
    
    parser.add_argument(
        '--rarity-filter',
        help='Filter by rarity (optional)'
    )
    
    parser.add_argument(
        '--download-dir',
        default='google_drive_downloads',
        help='Directory to download images to (default: google_drive_downloads)'
    )
    
    parser.add_argument(
        '--keep-local',
        action='store_true',
        help='Keep downloaded images after processing (default: delete)'
    )
    
    parser.add_argument(
        '--force-reauth',
        action='store_true',
        help='Force re-authentication to Google Drive'
    )
    
    parser.add_argument(
        '--verbose',
        action='store_true',
        help='Verbose output'
    )
    
    parser.add_argument(
        '--db-path',
        default='unified_card_database.db',
        help='Path to card database (default: unified_card_database.db)'
    )
    
    parser.add_argument(
        '--max-workers',
        type=int,
        default=4,
        help='Number of parallel workers (default: 4)'
    )
    
    args = parser.parse_args()
    
    setup_logging(args.verbose)
    
    print("\n" + "="*80)
    print("MAGIC: THE GATHERING - GOOGLE DRIVE BATCH SCANNER")
    print("="*80)
    print(f"Mode: Batch Processing")
    print(f"Source: Google Drive Folder")
    print(f"Database: {args.db_path}")
    print()
    
    try:
        # Initialize scanner
        print("[*] Initializing card scanner...")
        scanner = OptimizedCardScanner(
            db_path=args.db_path,
            max_workers=args.max_workers,
            cache_enabled=True,
            enable_collection=True
        )
        print("[+] Card scanner initialized successfully")
        
        # Initialize batch processor
        print("[*] Setting up batch processor...")
        batch = BatchGoogleDriveScanner(
            scanner,
            download_dir=args.download_dir
        )
        
        # Handle authentication
        if args.force_reauth:
            print("[*] Forcing re-authentication...")
            batch.gdrive_client.authenticate(force_reauth=True)
        
        # Scan folder
        results, stats = batch.scan_folder(
            folder_url_or_id=args.folder_url,
            threshold=args.threshold,
            top_n=args.top_n,
            output_csv=args.output_csv,
            output_json=args.output_json,
            review_report_csv=args.error_report_csv,
            review_report_json=args.error_report_json,
            manual_review_confidence_threshold=args.manual_review_threshold,
            set_filter=args.set_filter,
            foil_type_filter=args.foil_filter,
            rarity_filter=args.rarity_filter,
            keep_local_copies=args.keep_local
        )
        
        # Exit with status
        if stats.get('status') in ('authentication_failed', 'invalid_folder_id', 'no_images_found'):
            print(f"\n[!] Scan failed: {stats['status']}")
            return 1
        
        if results:
            print("\n[+] Scan completed successfully!")
            return 0
        else:
            print("\n[!] No results returned")
            return 1
        
    except KeyboardInterrupt:
        print("\n\n[!] Scan interrupted by user")
        return 130
    except Exception as e:
        print(f"\n[!] Fatal error: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == '__main__':
    sys.exit(main())
