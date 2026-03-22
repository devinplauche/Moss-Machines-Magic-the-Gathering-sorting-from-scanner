# Google Drive Batch Scanning - Setup & Usage Guide

## Overview

This system integrates Google Drive with the MTG card scanner, allowing you to:
- Authenticate with Google Drive
- List all images in a specified folder
- Download images locally
- Scan each card automatically
- Export results to CSV/JSON

## Prerequisites

### 1. Install Required Python Packages

```bash
pip install google-auth-oauthlib google-auth-httplib2 google-api-python-client
```

### 2. Set Up Google Drive API Credentials

#### Step 1: Create a Google Cloud Project
1. Visit [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project or select an existing one
3. Enable the **Google Drive API**:
   - Click "APIs & Services" → "Library"
   - Search for "Google Drive API"
   - Click it and press "Enable"

#### Step 2: Create OAuth 2.0 Credentials
1. Go to "APIs & Services" → "Credentials"
2. Click "Create Credentials" → "OAuth 2.0 Client ID"
3. Choose "Desktop application"
4. Click "Create"
5. Click the download icon (appears when hovering over your credential)
6. Save the file as `credentials.json` in the `Current-version/` directory

#### Step 3: First Run Authentication
The first time you run a scan command, it will:
1. Open a browser window asking for Google Drive access
2. Create a local token file (`google_drive_token.pickle`) for future use
3. Proceed with scanning

## Quick Start

### Basic Usage

```bash
cd Current-version
python scan_google_drive.py --folder-url "https://drive.google.com/drive/folders/1g_WOPFaYGWfiKbtLqO3aZAOEQOWnxJLc"
```

### With Output Files

```bash
python scan_google_drive.py \
    --folder-url "https://drive.google.com/drive/folders/1g_WOPFaYGWfiKbtLqO3aZAOEQOWnxJLc" \
    --output-csv scan_results.csv \
    --output-json scan_results.json
```

### Keep Downloaded Images

```bash
python scan_google_drive.py \
    --folder-url "1g_WOPFaYGWfiKbtLqO3aZAOEQOWnxJLc" \
    --keep-local
```

### Using Filters

```bash
python scan_google_drive.py \
    --folder-url "1g_WOPFaYGWfiKbtLqO3aZAOEQOWnxJLc" \
    --set-filter "MIR" \
    --foil-filter "Non Foil" \
    --output-csv results.csv
```

## Command Line Options

```
--folder-url URL              Google Drive folder URL or folder ID [REQUIRED]
--threshold N                 Match threshold, 0-255 (default: 40, lower = stricter)
--top-n N                     Number of top matches to return (default: 10)
--output-csv PATH             Save results as CSV file
--output-json PATH            Save results as JSON file
--set-filter CODE             Filter by card set code (e.g., "MIR", "TSR")
--foil-filter TYPE            Filter by foil type (e.g., "Foil", "Non Foil")
--rarity-filter RARITY        Filter by rarity (e.g., "Rare", "Uncommon")
--download-dir PATH           Directory for downloaded images (default: google_drive_downloads)
--keep-local                  Keep downloaded images after scanning (default: delete)
--force-reauth                Force re-authentication to Google Drive
--max-workers N               Parallel workers for processing (default: 4)
--db-path PATH                Path to card database (default: unified_card_database.db)
--verbose                     Verbose output
--help                        Show all options
```

## Python API Usage

If you want to integrate this into your own Python code:

```python
from optimized_scanner import OptimizedCardScanner
from google_drive_scanner import BatchGoogleDriveScanner

# Initialize scanner
scanner = OptimizedCardScanner()

# Initialize batch processor
batch = BatchGoogleDriveScanner(scanner, download_dir="my_downloads")

# Force authentication on first run
# batch.gdrive_client.authenticate()

# Scan folder
folder_url = "https://drive.google.com/drive/folders/1g_WOPFaYGWfiKbtLqO3aZAOEQOWnxJLc"
results, stats = batch.scan_folder(
    folder_url_or_id=folder_url,
    threshold=40,
    output_csv="results.csv",
    output_json="results.json",
    keep_local_copies=False
)

# Access results
for result in results:
    if result.get('match_found'):
        print(f"Found: {result['card_name']} ({result['set']})")
    else:
        print(f"No match: {result['source_file']}")

# Access stats
print(f"Matches found: {stats['matches_found']} / {stats['processed']}")
```

## Output Format

### CSV Results
Columns include:
- `source_file`: Original filename from Google Drive
- `card_name`: Recognized card name
- `set`: Card set code
- `product_id`: Product ID from database
- `distance`: Hash distance (0 = exact match)
- `confidence`: Confidence percentage (0-100)
- `rarity`: Card rarity
- `foil_type`: Foil type (Foil/Non-Foil/etc)
- `number`: Card number in set
- `status`: success/error
- `all_matches`: Top 3 matching cards (JSON)

### JSON Results
```json
{
  "timestamp": "2026-03-21 14:30:45",
  "results": [
    {
      "source_file": "scan_001.jpg",
      "card_name": "Black Lotus",
      "set": "BET",
      "distance": 0,
      "confidence": 100.0,
      "status": "success"
    }
  ],
  "stats": {
    "total_files": 50,
    "successful_scans": 48,
    "matches_found": 42
  }
}
```

## Troubleshooting

### Authentication Issues

**Q: "Credentials file not found"**
- Make sure `credentials.json` is in the `Current-version/` directory
- See setup step 2 above for how to download credentials

**Q: "Token refresh failed"**
- Delete `google_drive_token.pickle` and run with `--force-reauth`
- You'll need to re-authenticate in the browser

### Scanning Issues

**Q: "No images found in folder"**
- Verify the folder URL/ID is correct
- Ensure the folder actually contains image files (jpg, png, bmp, etc)
- Check that sharing permissions allow your account to view the folder

**Q: "Downloaded image can't be scanned"**
- Verify the image is a valid card photo
- Try reducing the `--threshold` value for more lenient matching
- Check that the database file exists: `unified_card_database.db`

**Q: "Process is slow"**
- Increase `--max-workers` (be careful not to exceed API rate limits)
- Use `--set-filter` to narrow down the search space
- Consider reducing `--top-n` if you only need 1-2 matches

### Google Drive API Issues

**Q: "Rate limit exceeded"**
- Wait a few minutes before running again
- Reduce `--max-workers` to lower concurrency
- Google Drive API has strict rate limiting for free accounts

**Q: "Permission denied"**
- Verify you have access to the Google Drive folder
- Try removing `google_drive_token.pickle` and re-authenticating
- Check Google Cloud Console project permissions

## Advanced Usage

### Batch Processing Multiple Folders

```bash
#!/bin/bash
# Process multiple folders
for folder_id in "1g_WOPFaYGWfiKbtLqO3aZAOEQOWnxJLc" "2h_XXXxxxGWfiKbtLqO3aZAOEQOWnxJLd"; do
  python scan_google_drive.py \
    --folder-url "$folder_id" \
    --output-csv "results_${folder_id}.csv"
done
```

### Combining Results

```python
import pandas as pd
import glob

# Combine all CSV results
all_results = []
for csv_file in glob.glob("results_*.csv"):
    df = pd.read_csv(csv_file)
    all_results.append(df)

combined = pd.concat(all_results, ignore_index=True)
combined.to_csv("all_results_combined.csv", index=False)
print(f"Total cards scanned: {len(combined)}")
print(f"Matches found: {combined[combined['match_found'] == True].shape[0]}")
```

## Performance Tips

1. **Threshold Tuning**: 
   - Lower values (10-30): Stricter matching, fewer false positives
   - Higher values (60-100): More lenient, better for low-quality images

2. **Parallel Processing**:
   - Default `--max-workers 4` is usually safe
   - Increase to 8-16 for VPS/server environments
   - Keep ≤4 for local machines to avoid resource exhaustion

3. **Filtering**:
   - Use `--set-filter` when scanning specific sets
   - Dramatically speeds up search when you know the set

4. **Network**:
   - Faster internet = faster downloads
   - Run on same network as scanner if possible

## Support

For issues or questions:
1. Check the troubleshooting section above
2. Run with `--verbose` for detailed output
3. Check that all dependencies are installed: `pip list | grep google`

## Files

- `google_drive_scanner.py`: Core Google Drive integration library
- `scan_google_drive.py`: CLI command-line interface
- `credentials.json`: Your Google Drive API credentials (create in setup)
- `google_drive_token.pickle`: Cached authentication token (auto-created)
- `google_drive_downloads/`: Directory where images are downloaded (default)
