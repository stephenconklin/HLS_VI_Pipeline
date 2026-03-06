#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# 08_hls_outlier_count_mosaic.py
# Pipeline Step 08 (outlier_counts): Mosaic per-tile outlier COUNT GeoTIFFs
#   into continent-wide rasters.
#
# Reads PROCESSED_VIS from env and mosaics ALL listed VIs in one run.
# Uses streaming rasterio.merge() to avoid loading all tiles into RAM.
# dtype=uint16, nodata=0 (zero means "no outliers", not missing data).
# Output filenames: HLS_Mosaic_Outlier_Count_{VI}_{safe_crs}.tif
#
# Author:  Stephen Conklin <stephenconklin@gmail.com>
#          https://github.com/stephenconklin
# License: MIT

import rasterio
from rasterio.merge import merge as rasterio_merge
import os
import glob
import numpy as np
from hls_utils import filter_by_configured_tiles

# --- CONFIGURATION FROM ENV ---
INPUT_FOLDER  = os.environ.get("REPROJECTED_DIR_OUTLIERS", "")
MOSAIC_DIR    = os.environ.get("MOSAIC_DIR",               "")
TARGET_CRS    = os.environ.get("TARGET_CRS",               "EPSG:6350")
PROCESSED_VIS      = os.environ.get("PROCESSED_VIS",           "NDVI EVI2 NIRv").split()
GEOTIFF_COMPRESS   = os.environ.get("GEOTIFF_COMPRESS",        "LZW").upper()
GEOTIFF_BLOCK_SIZE = int(os.environ.get("GEOTIFF_BLOCK_SIZE",  512))

if not INPUT_FOLDER or not MOSAIC_DIR:
    raise ValueError("REPROJECTED_DIR_OUTLIERS or MOSAIC_DIR not set.")

os.makedirs(MOSAIC_DIR, exist_ok=True)


def mosaic_outlier_count(vi_type):
    """Find all outlier count tiles for vi_type, stream-merge, write mosaic."""
    safe_crs    = TARGET_CRS.replace(':', '')
    pattern     = os.path.join(INPUT_FOLDER, "**",
                               f"*_outlier_count_{vi_type}_{safe_crs}.tif")
    tif_files   = glob.glob(pattern, recursive=True)
    tif_files   = filter_by_configured_tiles(tif_files)
    output_file = os.path.join(MOSAIC_DIR,
                               f"HLS_Mosaic_Outlier_Count_{vi_type}_{safe_crs}.tif")

    if not tif_files:
        print(f"  [{vi_type}] No outlier count tiles found — skipping.")
        print(f"             Pattern: {pattern}")
        return

    if os.path.exists(output_file):
        print(f"  [{vi_type}] Mosaic already exists — skipping: "
              f"{os.path.basename(output_file)}")
        return

    print(f"  [{vi_type}] Merging {len(tif_files)} tile(s) → "
          f"{os.path.basename(output_file)}")

    src_files = []
    try:
        src_files = [rasterio.open(f) for f in tif_files]

        # nodata=0: zero count is semantically "no outliers", not a gap.
        mosaic, transform = rasterio_merge(src_files, nodata=0)

        profile = src_files[0].profile.copy()
        profile.update(
            driver     = 'GTiff',
            height     = mosaic.shape[1],
            width      = mosaic.shape[2],
            count      = 1,
            dtype      = 'uint16',
            crs        = src_files[0].crs,
            transform  = transform,
            nodata     = 0,
            compress   = GEOTIFF_COMPRESS,
            tiled      = True,
            blockxsize = GEOTIFF_BLOCK_SIZE,
            blockysize = GEOTIFF_BLOCK_SIZE,
            predictor  = 2,    # integer differencing — correct for uint16 count data
        )

        with rasterio.open(output_file, 'w', **profile) as dst:
            dst.write(mosaic[0].astype('uint16'), 1)

        print(f"  [{vi_type}] ✓ Written: {os.path.basename(output_file)}")

    except Exception as e:
        print(f"  [{vi_type}] ✗ Error: {e}")
        raise

    finally:
        for src in src_files:
            src.close()


def main():
    print(f"--- Step 08: Outlier Count Mosaic  |  Target CRS: {TARGET_CRS} ---")
    print(f"VIs to mosaic: {PROCESSED_VIS}")
    for vi in PROCESSED_VIS:
        mosaic_outlier_count(vi)
    print("Step 08 complete.")


if __name__ == "__main__":
    main()
