#!/usr/bin/env python3
"""Debug script to list all files in Google Drive folder"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from google_drive_scanner import GoogleDriveClient

def main():
    print("[*] Debugging Google Drive folder contents...")
    
    client = GoogleDriveClient()
    if not client.authenticate():
        print("[!] Authentication failed")
        return 1
    
    folder_id = "1g_WOPFaYGWfiKbtLqO3aZAOEQOWnxJLc"
    print(f"[*] Folder ID: {folder_id}")
    print()
    
    # List ALL files (not just images)
    try:
        query = f"'{folder_id}' in parents and trashed = false"
        results = []
        page_token = None
        
        while True:
            response = client.service.files().list(
                q=query,
                spaces='drive',
                fields='nextPageToken, files(id, name, mimeType, size)',
                pageSize=1000,
                pageToken=page_token
            ).execute()
            
            results.extend(response.get('files', []))
            page_token = response.get('nextPageToken')
            
            if not page_token:
                break
        
        print(f"[+] Total items in folder: {len(results)}")
        print()
        
        if results:
            print("File listing:")
            print("-" * 80)
            for file in results:
                size_mb = file.get('size', 0)
                if isinstance(size_mb, str):
                    size_mb = int(size_mb) / (1024*1024)
                else:
                    size_mb = size_mb / (1024*1024)
                mime = file.get('mimeType', 'unknown')
                name = file['name']
                print(f"  • {name}")
                print(f"    MIME: {mime}")
                print(f"    Size: {size_mb:.1f} MB")
                print()
        else:
            print("[!] Folder appears to be empty or inaccessible")
            print()
            print("[*] Possible issues:")
            print("    1. Folder is empty")
            print("    2. You don't have access to the folder")
            print("    3. Folder ID is incorrect")
            print("    4. All files are in subfolders (not supported yet)")
        
        return 0
        
    except Exception as e:
        print(f"[!] Error: {e}")
        import traceback
        traceback.print_exc()
        return 1

if __name__ == '__main__':
    sys.exit(main())
