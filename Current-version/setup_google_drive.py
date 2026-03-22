#!/usr/bin/env python3
"""
Google Drive Scanner - Setup & Verification Script
Checks dependencies and helps with initial configuration
"""
import sys
import os
from pathlib import Path
import subprocess


def check_python_version():
    """Check Python version"""
    version = sys.version_info
    if version.major < 3 or (version.major == 3 and version.minor < 7):
        print("[!] Python 3.7+ required")
        return False
    print(f"[+] Python {version.major}.{version.minor}.{version.micro}")
    return True


def check_dependencies():
    """Check required packages"""
    required = {
        'google': 'google-auth-oauthlib',
        'googleapiclient': 'google-api-python-client',
        'google.auth': 'google-auth-httplib2',
    }
    
    missing = []
    print("\n[*] Checking dependencies...")
    
    for import_name, package_name in required.items():
        try:
            __import__(import_name)
            print(f"[+] {package_name}")
        except ImportError:
            print(f"[!] {package_name} - MISSING")
            missing.append(package_name)
    
    return missing


def check_local_dependencies():
    """Check local module dependencies"""
    required_files = {
        'optimized_scanner.py': 'Main scanner module',
        'card_filter.py': 'Card filtering module',
        'card_collection_manager.py': 'Collection manager',
        'scanner_modules/image_preprocessing.py': 'Image preprocessing',
    }
    
    print("\n[*] Checking local modules...")
    missing = []
    
    for filename, description in required_files.items():
        path = Path(filename)
        if path.exists():
            print(f"[+] {description}: {filename}")
        else:
            print(f"[!] {description}: {filename} - MISSING")
            missing.append(filename)
    
    return missing


def check_credentials():
    """Check for Google credentials"""
    print("\n[*] Checking Google Drive credentials...")
    
    if Path('credentials.json').exists():
        print("[+] credentials.json found")
        return True
    else:
        print("[!] credentials.json not found")
        print("\n    To set up credentials:")
        print("    1. Visit: https://console.cloud.google.com/")
        print("    2. Create a new project")
        print("    3. Enable Google Drive API")
        print("    4. Create OAuth 2.0 Desktop credentials")
        print("    5. Download and save as 'credentials.json' in this directory")
        return False


def check_database():
    """Check for card database"""
    print("\n[*] Checking card database...")
    
    if Path('unified_card_database.db').exists():
        size_mb = Path('unified_card_database.db').stat().st_size / (1024 * 1024)
        print(f"[+] unified_card_database.db found ({size_mb:.1f} MB)")
        return True
    else:
        print("[!] unified_card_database.db not found")
        print("    The scanner will attempt to download it on first run")
        return False


def install_dependencies(packages):
    """Install missing packages"""
    if not packages:
        print("\n[+] All dependencies installed!")
        return True
    
    print(f"\n[*] Installing {len(packages)} missing package(s)...")
    try:
        for package in packages:
            print(f"    Installing {package}...")
            subprocess.check_call([sys.executable, '-m', 'pip', 'install', package])
        print("[+] Installation complete")
        return True
    except subprocess.CalledProcessError as e:
        print(f"[!] Installation failed: {e}")
        return False


def main():
    print("="*80)
    print("GOOGLE DRIVE SCANNER - SETUP & VERIFICATION")
    print("="*80)
    
    # Check Python version
    if not check_python_version():
        return 1
    
    # Check Python dependencies
    missing_packages = check_dependencies()
    
    # Check local files
    missing_local = check_local_dependencies()
    if missing_local:
        print(f"\n[!] Missing {len(missing_local)} local module(s)")
        print("    Make sure you're running this from the Current-version/ directory")
        return 1
    
    # Check credentials
    has_credentials = check_credentials()
    
    # Check database
    has_database = check_database()
    
    # Summary
    print("\n" + "="*80)
    print("SETUP SUMMARY")
    print("="*80)
    
    if missing_packages:
        print(f"\n[!] Missing {len(missing_packages)} package(s)")
        print("\nWould you like to install them? (y/n): ", end='')
        response = input().strip().lower()
        if response == 'y':
            if not install_dependencies(missing_packages):
                return 1
        else:
            print("[*] Skipping installation. You can install manually with:")
            print(f"    pip install {' '.join(missing_packages)}")
    else:
        print("[+] All Python packages installed")
    
    print()
    if not has_credentials:
        print("[!] Google Drive credentials not configured")
        print("    See GOOGLE_DRIVE_SETUP.md for setup instructions")
    else:
        print("[+] Google Drive credentials configured")
    
    if not has_database:
        print("[*] Card database not found (will download on first run)")
    else:
        print("[+] Card database found")
    
    # Test import
    print("\n[*] Testing imports...")
    try:
        from optimized_scanner import OptimizedCardScanner
        print("[+] OptimizedCardScanner imported successfully")
        
        from google_drive_scanner import BatchGoogleDriveScanner, GoogleDriveClient
        print("[+] Google Drive modules imported successfully")
        
        # Check if Google API is available
        try:
            from google.oauth2.service_account import Credentials
            print("[+] Google API libraries available")
        except ImportError:
            print("[!] Google API libraries not available (will install if needed)")
        
    except ImportError as e:
        print(f"[!] Import failed: {e}")
        return 1
    
    # Ready
    print("\n" + "="*80)
    if has_credentials and not missing_packages:
        print("[+] SETUP COMPLETE - Ready to scan!")
        print("\nNext step: Run a scan")
        print("  python scan_google_drive.py --folder-url '<FOLDER_URL>'")
        return 0
    elif not missing_packages:
        print("[*] Setup almost complete!")
        print("\nNext steps:")
        if not has_credentials:
            print("  1. Set up Google Drive credentials (see GOOGLE_DRIVE_SETUP.md)")
        print("  2. Run: python scan_google_drive.py --folder-url '<FOLDER_URL>'")
        return 0
    else:
        print("[!] Please resolve issues above and try again")
        return 1


if __name__ == '__main__':
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n\n[!] Setup cancelled")
        sys.exit(130)
    except Exception as e:
        print(f"\n[!] Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
