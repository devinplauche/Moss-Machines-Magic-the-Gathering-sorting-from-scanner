#!/usr/bin/env python3
"""
Batch card-image cropper.

Trims the white scanner-paper background from every image in an input directory
and saves the result to an output directory.  The crop boundary is derived from
the reference image Color16410001.jpg (full image 855×1158, card region
782×1125 starting at approx x=22, y=14).

Usage
-----
  python crop_cards.py
      [--input  google_drive_downloads]
      [--output google_drive_downloads_cropped]
      [--ref    google_drive_downloads/Color16410001.jpg]
      [--pad    6]
      [--threshold 244]
      [--overwrite]
"""
import argparse
import os
import sys
import glob
import json
from pathlib import Path

import cv2
import numpy as np


# ──────────────────────────────────────────────────────────────────────────────
# Core trim logic
# ──────────────────────────────────────────────────────────────────────────────

def trim_white_background(image: np.ndarray, white_threshold: int = 244, padding: int = 6) -> tuple:
    """Return (cropped_image, crop_rect) where crop_rect = (x1, y1, x2, y2).

    Finds the bounding box of all pixels that are *not* near-white.
    If the image is almost entirely white (>97% white) the original is returned
    unchanged with rect = (0, 0, w-1, h-1).
    """
    if image is None or image.size == 0:
        return image, None

    h, w = image.shape[:2]

    if image.ndim == 2:
        non_white = image < int(white_threshold)
    else:
        non_white = np.any(image < int(white_threshold), axis=2)

    ys, xs = np.where(non_white)
    if len(xs) == 0:
        return image, (0, 0, w - 1, h - 1)

    x1, x2 = int(xs.min()), int(xs.max())
    y1, y2 = int(ys.min()), int(ys.max())

    crop_w = x2 - x1 + 1
    crop_h = y2 - y1 + 1
    if (crop_w * crop_h) / float(w * h) > 0.97:
        return image, (0, 0, w - 1, h - 1)

    pad = max(0, int(padding))
    x1 = max(0, x1 - pad)
    y1 = max(0, y1 - pad)
    x2 = min(w - 1, x2 + pad)
    y2 = min(h - 1, y2 + pad)

    return image[y1:y2 + 1, x1:x2 + 1], (x1, y1, x2, y2)


def derive_reference_crop(ref_path: str, pad: int = 6, threshold: int = 244):
    """Compute crop boundary from the reference image and return its stats."""
    img = cv2.imread(ref_path)
    if img is None:
        raise FileNotFoundError(f"Reference image not found: {ref_path}")

    cropped, rect = trim_white_background(img, white_threshold=threshold, padding=pad)
    x1, y1, x2, y2 = rect
    orig_h, orig_w = img.shape[:2]
    crop_h, crop_w = cropped.shape[:2]

    print(f"[reference] {Path(ref_path).name}:")
    print(f"  full image : {orig_w} x {orig_h}")
    print(f"  card region: {crop_w} x {crop_h}  @  ({x1},{y1})→({x2},{y2})")
    print(f"  margins    : left={x1} top={y1} right={orig_w-1-x2} bottom={orig_h-1-y2}")
    print(f"  aspect ratio: {crop_w/crop_h:.4f}  (standard MTG ≈ 0.714)")

    return {
        'orig_w': orig_w,
        'orig_h': orig_h,
        'crop_w': crop_w,
        'crop_h': crop_h,
        'x1': x1, 'y1': y1, 'x2': x2, 'y2': y2,
    }


# ──────────────────────────────────────────────────────────────────────────────
# Batch processing
# ──────────────────────────────────────────────────────────────────────────────

IMAGE_EXTS = {'.jpg', '.jpeg', '.png', '.bmp', '.tiff', '.webp'}


def batch_crop(
    input_dir: str,
    output_dir: str,
    white_threshold: int = 244,
    padding: int = 6,
    overwrite: bool = False,
) -> dict:
    in_path = Path(input_dir)
    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    images = [p for p in in_path.iterdir() if p.suffix.lower() in IMAGE_EXTS and p.is_file()]
    images.sort()

    stats = {'total': len(images), 'cropped': 0, 'skipped': 0, 'failed': 0}
    crop_log = []

    for idx, img_path in enumerate(images, 1):
        out_file = out_path / img_path.name
        if out_file.exists() and not overwrite:
            stats['skipped'] += 1
            print(f"  [{idx}/{len(images)}] skip (exists): {img_path.name}")
            continue

        img = cv2.imread(str(img_path))
        if img is None:
            stats['failed'] += 1
            print(f"  [{idx}/{len(images)}] FAIL (unreadable): {img_path.name}")
            continue

        cropped, rect = trim_white_background(img, white_threshold=white_threshold, padding=padding)
        x1, y1, x2, y2 = rect

        ok = cv2.imwrite(str(out_file), cropped)
        if not ok:
            stats['failed'] += 1
            print(f"  [{idx}/{len(images)}] FAIL (write error): {img_path.name}")
            continue

        orig_h, orig_w = img.shape[:2]
        new_h, new_w = cropped.shape[:2]
        crop_log.append({
            'file': img_path.name,
            'orig': f'{orig_w}x{orig_h}',
            'cropped': f'{new_w}x{new_h}',
            'rect': [x1, y1, x2, y2],
        })
        stats['cropped'] += 1
        if idx % 50 == 0 or idx == len(images):
            print(f"  [{idx}/{len(images)}] cropped {img_path.name}  {orig_w}x{orig_h} → {new_w}x{new_h}")

    # Write a JSON summary
    log_path = out_path / '_crop_log.json'
    with open(log_path, 'w', encoding='utf-8') as f:
        json.dump({'stats': stats, 'crops': crop_log}, f, indent=2)

    return stats


# ──────────────────────────────────────────────────────────────────────────────
# CLI entry point
# ──────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description='Crop white scanner margins from card photos')
    parser.add_argument('--input',     default='google_drive_downloads',          help='Input directory containing raw scanner photos')
    parser.add_argument('--output',    default='google_drive_downloads_cropped',  help='Output directory for trimmed images')
    parser.add_argument('--ref',       default=None,                              help='Reference image path (default: auto-detect Color16410001.jpg in input dir)')
    parser.add_argument('--pad',       type=int, default=6,                       help='Padding in pixels around the crop boundary (default: 6)')
    parser.add_argument('--threshold', type=int, default=244,                     help='Pixel brightness threshold for white detection (default: 244)')
    parser.add_argument('--overwrite', action='store_true',                       help='Re-crop images that already exist in output directory')
    args = parser.parse_args()

    print('=' * 70)
    print('CARD IMAGE BATCH CROPPER')
    print('=' * 70)
    print(f'Input  : {args.input}')
    print(f'Output : {args.output}')

    # Derive reference dimensions
    ref_path = args.ref
    if ref_path is None:
        candidates = sorted(Path(args.input).glob('*Color16410001.jpg'))
        if not candidates:
            candidates = sorted(Path(args.input).glob('*.jpg'))[:1]
        ref_path = str(candidates[0]) if candidates else None

    if ref_path:
        print()
        derive_reference_crop(ref_path, pad=args.pad, threshold=args.threshold)
    else:
        print('[!] No reference image found; proceeding with auto-detect only.')

    print()
    print(f'[*] Cropping images in: {args.input}')
    stats = batch_crop(args.input, args.output, white_threshold=args.threshold, padding=args.pad, overwrite=args.overwrite)

    print()
    print('=' * 70)
    print('SUMMARY')
    print('=' * 70)
    print(f"  Total  : {stats['total']}")
    print(f"  Cropped: {stats['cropped']}")
    print(f"  Skipped: {stats['skipped']}")
    print(f"  Failed : {stats['failed']}")
    print(f"  Log    : {Path(args.output) / '_crop_log.json'}")


if __name__ == '__main__':
    main()
