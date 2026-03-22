# Project Structure

This repository is organized by runtime permanence and ownership.

## Core Runtime (Permanent)

- `Current-version/optimized_scanner.py`: Main scanner runtime and CLI.
- `Current-version/scanner_modules/image_preprocessing.py`: Image-path validation and reusable crop/warp helpers.
- `Current-version/card_filter.py`: Metadata pre-filtering logic.
- `Current-version/card_collection_manager.py`: Collection persistence/export.
- `Current-version/gui_interface_enhanced.py`: GUI controller and scanner orchestration.
- `Current-version/plugins/`: Runtime plugin interfaces and stubs.
- `Current-version/recognition_data/`: pHash databases and scan metadata assets.

## Test Suite (Permanent)

- `test_debug_crops_regression.py`: Regression tests for missing-file behavior, debug crop names, and `IMG_3490.jpg` handling.
- `test_card_filter.py`, `test_hisokas.py`, `test_scanner.py`, `test_system_complete.py`: Existing tests and integration checks.

## One-time and Exploratory Assets

- `scripts/one_time/`: ad hoc scripts and JSON snapshots used for investigation or manual maintenance.
- `Current-version/ai-slop/`: one-off data generation, diagnostics, and historical pHash maintenance scripts.

## Working Data

- `Collection/`: scanner input/output artifacts.
- `Current-version/debug_crops/`: diagnostic crops used for scanner regression and tuning.

## Naming and Placement Rules

- Put runtime logic used by scanner/GUI in `Current-version/`.
- Put reusable helpers in `Current-version/scanner_modules/`.
- Put throwaway scripts or migration jobs in `scripts/one_time/`.
- Add or update tests in the repository root as `test_*.py`.
