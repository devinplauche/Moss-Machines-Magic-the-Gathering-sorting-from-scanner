# One-time Scripts

This folder contains scripts and artifacts used for manual debugging, migration, and investigation.

These files are intentionally separated from runtime scanner code so junior engineers can quickly identify what is safe to run in production flows.

Current contents:

- `check_db.py`
- `check_real_db.py`
- `debug_hisokas.py`
- `explore_db.py`
- `debug_scan_with_custom.json`
- `debug_scan_without_custom.json`

Guideline:

- If a script is only used for a one-off task or local debugging, place it here.
- If a script is imported by runtime code or CI tests, keep it in a permanent runtime location.
