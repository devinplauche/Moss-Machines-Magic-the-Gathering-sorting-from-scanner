#!/usr/bin/env python3
"""
Google Drive Card Scanner Integration
Scans images from a Google Drive folder and processes them through the card recognition system
"""
import os
import json
import time
import threading
import difflib
from pathlib import Path
from typing import List, Dict, Optional, Tuple
import pickle

try:
    import cv2
except Exception:
    cv2 = None

try:
    from google.auth.transport.requests import Request
    from google.oauth2.service_account import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from google.auth.transport.requests import Request as GoogleRequest
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaIoBaseDownload
    HAS_GOOGLE_API = True
except ImportError:
    HAS_GOOGLE_API = False
    print("[!] Google API client not installed. Run: pip install google-auth-oauthlib google-auth-httplib2 google-api-python-client")

import io


class GoogleDriveClient:
    """Manages authentication and communication with Google Drive API"""
    
    SCOPES = ['https://www.googleapis.com/auth/drive.readonly']
    TOKEN_FILE = 'google_drive_token.pickle'
    CREDENTIALS_FILE = 'credentials.json'
    
    def __init__(self, credentials_path: Optional[str] = None):
        """
        Initialize Google Drive client
        
        Args:
            credentials_path: Path to credentials.json file. If None, uses default location.
        """
        self.service = None
        self.credentials = None
        self.credentials_path = credentials_path or self.CREDENTIALS_FILE
        
        if not HAS_GOOGLE_API:
            raise ImportError("Google API client libraries not installed")
    
    def authenticate(self, force_reauth: bool = False) -> bool:
        """
        Authenticate with Google Drive
        
        Args:
            force_reauth: If True, force re-authentication even if token exists
            
        Returns:
            True if authentication successful, False otherwise
        """
        try:
            # Try to load existing token
            if not force_reauth and os.path.exists(self.TOKEN_FILE):
                with open(self.TOKEN_FILE, 'rb') as token:
                    self.credentials = pickle.load(token)
                    
            # If no valid credentials, auth flow
            if not self.credentials or not self.credentials.valid:
                if self.credentials and self.credentials.expired and self.credentials.refresh_token:
                    self.credentials.refresh(GoogleRequest())
                else:
                    # New auth flow
                    if not os.path.exists(self.credentials_path):
                        print(f"[!] Credentials file not found: {self.credentials_path}")
                        print("[*] To set up authentication:")
                        print("    1. Go to https://developers.google.com/drive/api/quickstart/python")
                        print("    2. Click 'Enable the Google Drive API'")
                        print("    3. Create OAuth 2.0 credentials (Desktop application)")
                        print("    4. Download the credentials JSON file")
                        print(f"    5. Save it as '{self.credentials_path}' in the Current-version folder")
                        return False
                    
                    flow = InstalledAppFlow.from_client_secrets_file(
                        self.credentials_path, self.SCOPES)
                    self.credentials = flow.run_local_server(port=0)
                    
                    # Save token for future use
                    with open(self.TOKEN_FILE, 'wb') as token:
                        pickle.dump(self.credentials, token)
            
            # Build service
            self.service = build('drive', 'v3', credentials=self.credentials)
            print("[+] Successfully authenticated with Google Drive")
            return True
            
        except Exception as e:
            print(f"[!] Authentication failed: {e}")
            return False
    
    def get_folder_id_from_url(self, folder_url: str) -> Optional[str]:
        """
        Extract folder ID from Google Drive URL
        
        Args:
            folder_url: Google Drive folder URL or folder ID
            
        Returns:
            Folder ID or None if invalid
        """
        # If it's already just an ID
        if len(folder_url) == 33 and all(c.isalnum() or c in '-_' for c in folder_url):
            return folder_url
        
        # Extract from URL
        import re
        match = re.search(r'/folders/([a-zA-Z0-9-_]+)', folder_url)
        if match:
            return match.group(1)
        
        return None
    
    def list_images_in_folder(self, folder_id: str, image_extensions: List[str] = None, recursive: bool = True) -> List[Dict]:
        """
        List all images in a Google Drive folder (recursively searches subfolders)
        
        Args:
            folder_id: Google Drive folder ID
            image_extensions: List of file extensions to search for (default: jpg, jpeg, png, bmp, gif)
            recursive: If True, search subfolders as well (default: True)
            
        Returns:
            List of file metadata dictionaries with 'id', 'name', 'mimeType', etc.
        """
        if not self.service:
            return []
        
        if image_extensions is None:
            image_extensions = ['jpg', 'jpeg', 'png', 'bmp', 'gif', 'tiff']
        
        try:
            results = []
            
            # First, get direct images in this folder
            extension_query = " or ".join([f"name contains '.{ext}'" for ext in image_extensions])
            query = f"'{folder_id}' in parents and ({extension_query}) and trashed = false"
            
            page_token = None
            while True:
                response = self.service.files().list(
                    q=query,
                    spaces='drive',
                    fields='nextPageToken, files(id, name, mimeType, size, createdTime, modifiedTime)',
                    pageSize=100,
                    pageToken=page_token
                ).execute()
                
                results.extend(response.get('files', []))
                page_token = response.get('nextPageToken')
                
                if not page_token:
                    break
            
            # If recursive, find subfolders and search them too
            if recursive:
                query_folders = f"'{folder_id}' in parents and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
                page_token = None
                subfolders = []
                
                while True:
                    response = self.service.files().list(
                        q=query_folders,
                        spaces='drive',
                        fields='nextPageToken, files(id, name)',
                        pageSize=100,
                        pageToken=page_token
                    ).execute()
                    
                    subfolders.extend(response.get('files', []))
                    page_token = response.get('nextPageToken')
                    
                    if not page_token:
                        break
                
                # Search each subfolder
                for subfolder in subfolders:
                    subfolder_id = subfolder['id']
                    subfolder_name = subfolder['name']
                    print(f"  [*] Searching subfolder: {subfolder_name}")
                    sub_results = self.list_images_in_folder(subfolder_id, image_extensions, recursive=False)
                    results.extend(sub_results)
            
            print(f"[+] Found {len(results)} images in Google Drive folder (including subfolders)")
            return results
            
        except Exception as e:
            print(f"[!] Error listing folder contents: {e}")
            return []
    
    def download_file(self, file_id: str, output_path: str) -> bool:
        """
        Download a file from Google Drive
        
        Args:
            file_id: Google Drive file ID
            output_path: Local path to save file
            
        Returns:
            True if download successful, False otherwise
        """
        if not self.service:
            return False
        
        try:
            request = self.service.files().get_media(fileId=file_id)
            fh = io.FileIO(output_path, 'wb')
            downloader = MediaIoBaseDownload(fh, request)
            
            done = False
            while not done:
                status, done = downloader.next_chunk()
                if status:
                    percent = int(status.progress() * 100)
                    print(f"    Downloaded {percent}%...", end='\r')
            
            print(f"    Downloaded {output_path}")
            return True
            
        except Exception as e:
            print(f"[!] Download failed: {e}")
            return False


class BatchGoogleDriveScanner:
    """Batch processor for scanning images from Google Drive folder"""
    
    def __init__(self, scanner, download_dir: str = "google_drive_downloads"):
        """
        Initialize batch scanner
        
        Args:
            scanner: OptimizedCardScanner instance
            download_dir: Directory to store downloaded images
        """
        self.scanner = scanner
        self.download_dir = Path(download_dir)
        self.download_dir.mkdir(parents=True, exist_ok=True)
        
        self.gdrive_client = GoogleDriveClient()
        
        # Tracking
        self.results = []
        self.processed_files = set()
        self.failed_downloads = []
        self.failed_scans = []

    @staticmethod
    def _safe_int(value, default=0):
        try:
            return int(value)
        except Exception:
            return default

    @staticmethod
    def _sanitize_filename(name: str) -> str:
        if not name:
            return "unnamed_file"
        # Keep names portable on Windows and preserve extension when possible.
        bad = '<>:"/\\|?*'
        cleaned = ''.join('_' if c in bad else c for c in str(name)).strip()
        return cleaned or "unnamed_file"

    def _build_local_cache_path(self, file_id: str, file_name: str) -> Path:
        # Prefixing with file_id avoids collisions when different folders contain same filename.
        safe_name = self._sanitize_filename(file_name)
        return self.download_dir / f"{file_id}_{safe_name}"

    def _should_reuse_cached_file(self, path: Path, expected_size: int) -> bool:
        if not path.exists() or not path.is_file():
            return False
        if expected_size <= 0:
            return True
        try:
            return path.stat().st_size == expected_size
        except Exception:
            return False

    @staticmethod
    def _to_float(value, default=0.0):
        try:
            return float(value)
        except Exception:
            return float(default)

    def _collect_manual_review_items(self, confidence_threshold: float) -> List[Dict]:
        review_items = []
        for result in self.results:
            if result.get('status') != 'success' or not result.get('match_found'):
                continue

            confidence = self._to_float(result.get('confidence'), 0.0)
            matched_name = str(result.get('matched_name') or '').strip()
            ocr_resolved = str(result.get('ocr_resolved_name') or '').strip()
            name_source = str(result.get('name_source') or '').strip()

            reasons = []
            if confidence < float(confidence_threshold):
                reasons.append('low_confidence')
            if name_source.startswith('ocr_'):
                reasons.append('ocr_override_applied')
            if matched_name and ocr_resolved:
                similarity = difflib.SequenceMatcher(None, matched_name.lower(), ocr_resolved.lower()).ratio()
                if similarity < 0.6:
                    reasons.append('scanner_ocr_disagreement')

            if not reasons:
                continue

            review_items.append({
                'source_file': result.get('source_file'),
                'local_path': result.get('local_path'),
                'card_name': result.get('card_name'),
                'matched_name': result.get('matched_name'),
                'ocr_top_name': result.get('ocr_top_name'),
                'ocr_resolved_name': result.get('ocr_resolved_name'),
                'name_source': result.get('name_source'),
                'confidence': result.get('confidence'),
                'distance': result.get('distance'),
                'set': result.get('set'),
                'number': result.get('number'),
                'rarity': result.get('rarity'),
                'foil_type': result.get('foil_type'),
                'review_reasons': ';'.join(reasons),
                'top_alternatives': result.get('all_matches'),
            })
        return review_items

    def _save_manual_review_reports(self, review_items: List[Dict], csv_path: Optional[str], json_path: Optional[str]):
        if not review_items:
            print("[*] Manual review report: no flagged entries")
            return

        if csv_path:
            try:
                import csv
                path = Path(csv_path)
                path.parent.mkdir(parents=True, exist_ok=True)
                fieldnames = sorted({k for row in review_items for k in row.keys()})
                with open(path, 'w', newline='', encoding='utf-8') as f:
                    writer = csv.DictWriter(f, fieldnames=fieldnames)
                    writer.writeheader()
                    for row in review_items:
                        flat = {}
                        for key, value in row.items():
                            if isinstance(value, (list, dict)):
                                flat[key] = json.dumps(value)
                            else:
                                flat[key] = value
                        writer.writerow(flat)
                print(f"[+] Manual review CSV saved: {csv_path}")
            except Exception as e:
                print(f"[!] Failed to save manual review CSV: {e}")

        if json_path:
            try:
                path = Path(json_path)
                path.parent.mkdir(parents=True, exist_ok=True)
                payload = {
                    'timestamp': time.strftime('%Y-%m-%d %H:%M:%S'),
                    'flagged_count': len(review_items),
                    'items': review_items,
                }
                with open(path, 'w', encoding='utf-8') as f:
                    json.dump(payload, f, indent=2, ensure_ascii=False)
                print(f"[+] Manual review JSON saved: {json_path}")
            except Exception as e:
                print(f"[!] Failed to save manual review JSON: {e}")
        
    def scan_folder(self, 
                   folder_url_or_id: str,
                   threshold: int = 40,
                   top_n: int = 10,
                   output_csv: Optional[str] = None,
                   output_json: Optional[str] = None,
                   review_report_csv: Optional[str] = None,
                   review_report_json: Optional[str] = None,
                   manual_review_confidence_threshold: float = 75.0,
                   metadata_hints: Optional[Dict] = None,
                   set_filter: Optional[str] = None,
                   foil_type_filter: Optional[str] = None,
                   rarity_filter: Optional[str] = None,
                   game_filter: Optional[str] = None,
                   max_workers: int = 4,
                   keep_local_copies: bool = False) -> Tuple[List[Dict], Dict]:
        """
        Scan all images in a Google Drive folder
        
        Args:
            folder_url_or_id: Google Drive folder URL or folder ID
            threshold: Match threshold for card recognition
            top_n: Number of top matches to return
            output_csv: Path to save results as CSV
            output_json: Path to save results as JSON
            review_report_csv: Path to save low-confidence/disagreement review CSV
            review_report_json: Path to save low-confidence/disagreement review JSON
            manual_review_confidence_threshold: Confidence threshold for review queue
            metadata_hints: Optional metadata hints for OCR
            set_filter: Filter by card set
            foil_type_filter: Filter by foil type
            rarity_filter: Filter by rarity
            game_filter: Filter by game
            max_workers: Number of parallel download/process threads
            keep_local_copies: If True, keep downloaded images; if False, delete after processing
            
        Returns:
            Tuple of (results list, stats dictionary)
        """
        print("\n" + "="*80)
        print("GOOGLE DRIVE FOLDER SCANNER")
        print("="*80)
        
        # Authenticate
        if not self.gdrive_client.authenticate():
            return [], {'status': 'authentication_failed'}
        
        # Get folder ID
        folder_id = self.gdrive_client.get_folder_id_from_url(folder_url_or_id)
        if not folder_id:
            print(f"[!] Invalid folder URL/ID: {folder_url_or_id}")
            return [], {'status': 'invalid_folder_id'}
        
        print(f"[*] Folder ID: {folder_id}")
        
        # List images
        files = self.gdrive_client.list_images_in_folder(folder_id)
        if not files:
            print("[!] No images found in folder")
            return [], {'status': 'no_images_found'}
        
        print(f"[*] Processing {len(files)} images...")
        print(f"[*] Download directory: {self.download_dir}")
        
        # Reset tracking
        self.results = []
        self.processed_files = set()
        self.failed_downloads = []
        self.failed_scans = []
        
        start_time = time.time()
        
        reused_local_files = 0

        # Process files
        for idx, file_info in enumerate(files, 1):
            file_id = file_info['id']
            file_name = file_info['name']
            file_size = self._safe_int(file_info.get('size', 0), 0)
            
            print(f"\n[{idx}/{len(files)}] Processing: {file_name} ({file_size} bytes)")
            
            # Download file (or reuse cached copy)
            local_path = self._build_local_cache_path(file_id, file_name)
            legacy_local_path = self.download_dir / file_name

            reused = False
            if self._should_reuse_cached_file(local_path, file_size):
                reused = True
            elif self._should_reuse_cached_file(legacy_local_path, file_size):
                # Backward compatibility for files downloaded before file_id prefixing.
                local_path = legacy_local_path
                reused = True

            if reused:
                reused_local_files += 1
                print(f"    Reusing local file: {local_path}")
            else:
                if not self.gdrive_client.download_file(file_id, str(local_path)):
                    self.failed_downloads.append(file_name)
                    continue
            
            # Scan file
            try:
                print(f"    Scanning card...")

                ocr_top_name = None
                ocr_resolved_name = None
                if cv2 is not None and hasattr(self.scanner, '_extract_top_band_ocr_hints'):
                    try:
                        preview = cv2.imread(str(local_path))
                        if preview is not None:
                            ocr_hints = self.scanner._extract_top_band_ocr_hints(preview) or {}
                            ocr_top_name = str(ocr_hints.get('name') or '').strip() or None
                            if ocr_top_name and hasattr(self.scanner, '_resolve_ocr_name_candidate'):
                                try:
                                    ocr_resolved_name = self.scanner._resolve_ocr_name_candidate(ocr_top_name)
                                except Exception:
                                    ocr_resolved_name = None
                    except Exception:
                        ocr_top_name = None
                        ocr_resolved_name = None

                matches = self.scanner.scan_from_file(
                    str(local_path),
                    threshold=threshold,
                    top_n=top_n,
                    set_filter=set_filter,
                    foil_type_filter=foil_type_filter,
                    rarity_filter=rarity_filter,
                    game_filter=game_filter,
                    metadata_hints=metadata_hints
                )
                
                if matches and matches[0]:
                    top_match = matches[0][0]  # First match from first result

                    matched_name = str(top_match.get('name') or '').strip() or None
                    card_name = matched_name
                    name_source = 'scanner_match'

                    # Prioritize OCR top-name when scanner confidence is low and names disagree.
                    try:
                        confidence = float(top_match.get('confidence') or 0)
                    except Exception:
                        confidence = 0.0

                    if ocr_top_name:
                        if not matched_name:
                            if ocr_resolved_name:
                                card_name = ocr_resolved_name
                                name_source = 'ocr_resolved_no_match_name'
                            else:
                                card_name = ocr_top_name
                                name_source = 'ocr_top_band_no_match_name'
                        else:
                            ocr_compare_name = ocr_resolved_name or ocr_top_name
                            similarity = difflib.SequenceMatcher(
                                None,
                                matched_name.lower(),
                                str(ocr_compare_name).lower(),
                            ).ratio()
                            # Only override with OCR when OCR can be resolved to a canonical card name.
                            if confidence < 75.0 and similarity < 0.55 and ocr_resolved_name:
                                card_name = ocr_resolved_name
                                name_source = 'ocr_resolved_override_low_confidence'

                    result = {
                        'source_file': file_name,
                        'local_path': str(local_path),
                        'status': 'success',
                        'match_found': True,
                        'card_name': card_name,
                        'matched_name': matched_name,
                        'ocr_top_name': ocr_top_name,
                        'ocr_resolved_name': ocr_resolved_name,
                        'name_source': name_source,
                        'set': top_match.get('set'),
                        'product_id': top_match.get('product_id'),
                        'distance': top_match.get('distance'),
                        'confidence': top_match.get('confidence'),
                        'rarity': top_match.get('rarity'),
                        'foil_type': top_match.get('foil_type'),
                        'number': top_match.get('number'),
                        'all_matches': matches[0][:min(3, len(matches[0]))]  # Top 3 matches
                    }
                    print(
                        f"    ✓ Match: {card_name} ({top_match.get('set')}) - "
                        f"Confidence: {top_match.get('confidence'):.1f}% [{name_source}]"
                    )
                else:
                    result = {
                        'source_file': file_name,
                        'local_path': str(local_path),
                        'status': 'success',
                        'match_found': False,
                        'match_found': False,
                        'ocr_top_name': ocr_top_name,
                    }
                    print(f"    ✗ No match found")
                
                self.results.append(result)
                self.processed_files.add(file_name)
                
            except Exception as e:
                print(f"    [!] Scan failed: {e}")
                self.failed_scans.append((file_name, str(e)))
                result = {
                    'source_file': file_name,
                    'local_path': str(local_path),
                    'status': 'error',
                    'error': str(e)
                }
                self.results.append(result)
            
            # Clean up local file if requested
            if not keep_local_copies and local_path.exists():
                try:
                    local_path.unlink()
                except Exception as e:
                    print(f"    [!] Failed to delete local file: {e}")
        
        elapsed = time.time() - start_time
        
        # Summary stats
        stats = {
            'total_files': len(files),
            'processed': len(self.processed_files),
            'successful_scans': sum(1 for r in self.results if r.get('status') == 'success'),
            'matches_found': sum(1 for r in self.results if r.get('match_found')),
            'reused_local_files': reused_local_files,
            'failed_downloads': len(self.failed_downloads),
            'failed_scans': len(self.failed_scans),
            'manual_review_threshold': float(manual_review_confidence_threshold),
            'elapsed_time': elapsed,
            'avg_time_per_scan': elapsed / max(len(self.processed_files), 1)
        }
        
        # Print summary
        print("\n" + "="*80)
        print("SCAN SUMMARY")
        print("="*80)
        print(f"Total files: {stats['total_files']}")
        print(f"Processed: {stats['processed']}")
        print(f"Successful scans: {stats['successful_scans']}")
        print(f"Matches found: {stats['matches_found']}")
        print(f"Reused local files: {stats['reused_local_files']}")
        print(f"Failed downloads: {stats['failed_downloads']}")
        print(f"Failed scans: {stats['failed_scans']}")
        print(f"Total time: {elapsed:.1f}s")
        print(f"Average time per scan: {stats['avg_time_per_scan']:.1f}s")

        # Build manual review list and persist a dedicated report.
        review_items = self._collect_manual_review_items(manual_review_confidence_threshold)
        stats['manual_review_items'] = len(review_items)
        print(f"Manual review items: {stats['manual_review_items']} (threshold < {manual_review_confidence_threshold:.1f}%)")
        
        # Save results
        if output_csv:
            self._save_csv(output_csv)
            print(f"[+] Results saved to: {output_csv}")
        
        if output_json:
            self._save_json(output_json)
            print(f"[+] Results saved to: {output_json}")

        if review_report_csv is None:
            base = Path(output_csv or output_json or 'scan_results')
            review_report_csv = str(base.with_name(f"{base.stem}_manual_review.csv"))
        if review_report_json is None:
            base = Path(output_json or output_csv or 'scan_results')
            review_report_json = str(base.with_name(f"{base.stem}_manual_review.json"))

        self._save_manual_review_reports(review_items, review_report_csv, review_report_json)
        
        return self.results, stats
    
    def _save_csv(self, output_path: str):
        """Save results to CSV file"""
        try:
            import csv
            from datetime import datetime
            
            csv_path = Path(output_path)
            csv_path.parent.mkdir(parents=True, exist_ok=True)
            
            with open(csv_path, 'w', newline='', encoding='utf-8') as f:
                if not self.results:
                    return
                
                # Determine all possible keys
                all_keys = set()
                for result in self.results:
                    all_keys.update(result.keys())
                
                fieldnames = sorted(list(all_keys))
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                
                for result in self.results:
                    # Flatten nested structures
                    flat_result = {}
                    for key, value in result.items():
                        if isinstance(value, (list, dict)):
                            flat_result[key] = json.dumps(value)
                        else:
                            flat_result[key] = value
                    writer.writerow(flat_result)
            
            print(f"[+] CSV saved: {output_path}")
        except Exception as e:
            print(f"[!] Failed to save CSV: {e}")
    
    def _save_json(self, output_path: str):
        """Save results to JSON file"""
        try:
            json_path = Path(output_path)
            json_path.parent.mkdir(parents=True, exist_ok=True)
            
            output_data = {
                'timestamp': time.strftime('%Y-%m-%d %H:%M:%S'),
                'results': self.results,
                'stats': {
                    'total_files': len([r for r in self.results]),
                    'successful_scans': sum(1 for r in self.results if r.get('status') == 'success'),
                    'matches_found': sum(1 for r in self.results if r.get('match_found'))
                }
            }
            
            with open(json_path, 'w', encoding='utf-8') as f:
                json.dump(output_data, f, indent=2, ensure_ascii=False)
            
            print(f"[+] JSON saved: {output_path}")
        except Exception as e:
            print(f"[!] Failed to save JSON: {e}")


if __name__ == "__main__":
    print("[!] This module should be imported, not run directly")
    print("[*] Usage:")
    print("    from google_drive_scanner import BatchGoogleDriveScanner")
    print("    from optimized_scanner import OptimizedCardScanner")
    print()
    print("    scanner = OptimizedCardScanner()")
    print("    batch = BatchGoogleDriveScanner(scanner)")
    print("    results, stats = batch.scan_folder('FOLDER_URL_OR_ID')")
