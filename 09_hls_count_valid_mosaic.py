#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# 09_hls_count_valid_mosaic.py
# Pipeline Step 09 (count_valid_mosaic): Count valid observations per pixel
#   across the full downloaded period and mosaic into a study-area-wide GeoTIFF.
#
# For each VI in PROCESSED_VIS:
#   1. Reads per-tile NetCDF time-series from NETCDF_DIR
#   2. Counts pixels with valid (non-NaN, within VALID_RANGE_{VI}) values
#      across ALL time steps in each tile
#   3. Reprojects each tile to TARGET_CRS at 30 m
#   4. Mosaics all tiles into a single-band uint16 GeoTIFF:
#        HLS_Mosaic_CountValid_{VI}_{safe_crs}.tif
#
# The temporal scope is implicitly defined by DOWNLOAD_CYCLES — since only
# data within those cycles is present in the NetCDF files, no explicit date
# filtering is required; all observations in the NetCDF are within scope.
#
# Run independently once NetCDF files exist (Step 03 must have run):
#   STEPS="count_valid_mosaic" bash hls_pipeline.sh
#
# Author:  Stephen Conklin <stephenconklin@gmail.com>
#          https://github.com/stephenconklin
# License: MIT

import os
import glob
import warnings
import tempfile
import numpy as np
import xarray as xr
import rioxarray         # noqa: F401 — activates .rio accessor
import rasterio
from rasterio.merge import merge as rasterio_merge
from concurrent.futures import ProcessPoolExecutor, as_completed
from hls_utils import filter_by_configured_tiles, get_valid_range, detect_crs, reproject_resolution

warnings.filterwarnings("ignore", category=rasterio.errors.NotGeoreferencedWarning)

# =============================================================================
# CONFIGURATION FROM ENV
# =============================================================================
NETCDF_DIR    = os.environ.get("NETCDF_DIR",   "")
MOSAIC_DIR    = os.environ.get("MOSAIC_DIR",   "")
TARGET_CRS    = os.environ.get("TARGET_CRS",   "EPSG:6350")
PROCESSED_VIS      = os.environ.get("PROCESSED_VIS",      "NDVI EVI2 NIRv").split()
N_WORKERS          = int(os.environ.get("NUM_WORKERS",     4))
GEOTIFF_COMPRESS   = os.environ.get("GEOTIFF_COMPRESS",   "LZW").upper()
GEOTIFF_BLOCK_SIZE = int(os.environ.get("GEOTIFF_BLOCK_SIZE", 512))

if not NETCDF_DIR or not MOSAIC_DIR:
    raise ValueError("NETCDF_DIR or MOSAIC_DIR not set in environment.")

os.makedirs(MOSAIC_DIR, exist_ok=True)


# =============================================================================
# PER-TILE WORKER
# =============================================================================

def _process_tile(args: dict) -> dict:
    """
    Worker: open one tile's NetCDF, count valid observations across all time
    steps, reproject to TARGET_CRS, write a temp GeoTIFF.
    """
    import dask

    nc_path    = args['nc_path']
    vi_type    = args['vi_type']
    target_crs = args['target_crs']
    temp_dir   = args['temp_dir']

    filename = os.path.basename(nc_path)
    tile_id  = filename.split('_')[0] if '_' in filename else filename.replace('.nc', '')

    try:
        ds = xr.open_dataset(nc_path, chunks='auto')

        if vi_type in ds.data_vars:
            da = ds[vi_type]
        else:
            candidates = [v for v in ds.data_vars if vi_type.lower() in v.lower()]
            if not candidates:
                ds.close()
                return {'status': 'skip',
                        'message': f"Variable {vi_type} not found in {filename}"}
            da = ds[candidates[0]]

        source_crs = detect_crs(ds, da)
        if source_crs is None:
            ds.close()
            return {'status': 'skip', 'message': f"No CRS in {filename}"}

        n_obs = int(ds['time'].size)
        if n_obs == 0:
            ds.close()
            return {'status': 'skip', 'message': f"No time steps in {filename}"}

        vmin, vmax = get_valid_range(vi_type)
        valid = da.where((da >= vmin) & (da <= vmax))

        # dask.config.set prevents nested thread pools inside worker processes.
        with dask.config.set(scheduler='synchronous'):
            count_valid = valid.count(dim='time').compute()

        count_valid.rio.write_crs(source_crs, inplace=True)
        reproj_count = count_valid.rio.reproject(target_crs, resolution=reproject_resolution(target_crs), nodata=0)
        reproj_count = reproj_count.fillna(0).astype('uint16')

        count_tmp = os.path.join(temp_dir, f"{tile_id}_{vi_type}_count.tif")
        reproj_count.encoding.clear()
        reproj_count.rio.write_nodata(0, encoded=True, inplace=True)
        reproj_count.rio.to_raster(count_tmp, compress=GEOTIFF_COMPRESS,
                                   blockxsize=GEOTIFF_BLOCK_SIZE, blockysize=GEOTIFF_BLOCK_SIZE,
                                   dtype='uint16')

        ds.close()
        return {
            'status':     'ok',
            'count_path': count_tmp,
            'message':    f"OK ({n_obs} obs): {tile_id}",
        }

    except Exception as e:
        return {'status': 'error', 'message': f"Error ({tile_id}): {e}"}


# =============================================================================
# MOSAIC HELPER
# =============================================================================

def _mosaic_tiles(tile_paths: list, nodata, dtype: str) -> tuple:
    """Stream-merge temp tile GeoTIFFs. Returns (array, transform, crs)."""
    src_files = []
    try:
        src_files = [rasterio.open(p) for p in tile_paths]
        mosaic, transform = rasterio_merge(src_files, nodata=nodata)
        crs = src_files[0].crs
        return mosaic[0].astype(dtype), transform, crs
    finally:
        for src in src_files:
            src.close()


# =============================================================================
# MAIN ORCHESTRATION
# =============================================================================

def build_count_valid_mosaic(processed_vis: list):
    safe_crs = TARGET_CRS.replace(':', '')

    all_nc = glob.glob(os.path.join(NETCDF_DIR, "**", "*.nc"), recursive=True)
    all_nc = filter_by_configured_tiles(all_nc)
    if not all_nc:
        print(f"[ERROR] No NetCDF files found in {NETCDF_DIR}")
        return

    print(f"Found {len(all_nc)} NetCDF files across all tiles/VIs.")
    print(f"VIs:     {processed_vis}")
    print(f"Workers: {N_WORKERS}")
    for vi in processed_vis:
        vmin, vmax = get_valid_range(vi)
        print(f"  Valid range  {vi}: [{vmin}, {vmax}]")
    print()

    for vi in processed_vis:
        output_path = os.path.join(MOSAIC_DIR,
                                   f"HLS_Mosaic_CountValid_{vi}_{safe_crs}.tif")

        vi_nc_files = [f for f in all_nc if vi in os.path.basename(f)]
        if not vi_nc_files:
            print(f"[{vi}] No NetCDF files found — skipping.")
            continue

        if os.path.exists(output_path):
            print(f"[{vi}] Output already exists — skipping: "
                  f"{os.path.basename(output_path)}")
            continue

        print(f"[{vi}] {len(vi_nc_files)} tile file(s)  →  "
              f"{os.path.basename(output_path)}")

        with tempfile.TemporaryDirectory(prefix=f"hls_{vi}_countvalid_") as tmp_dir:
            worker_args = [
                {
                    'nc_path': nc, 'vi_type': vi,
                    'target_crs': TARGET_CRS, 'temp_dir': tmp_dir,
                }
                for nc in vi_nc_files
            ]

            count_tile_paths = []
            n_skipped = n_errors = 0

            with ProcessPoolExecutor(max_workers=N_WORKERS) as executor:
                futures = {executor.submit(_process_tile, a): a
                           for a in worker_args}
                for future in as_completed(futures):
                    result = future.result()
                    if result['status'] == 'ok':
                        count_tile_paths.append(result['count_path'])
                        print(f"  ✓ {result['message']}")
                    elif result['status'] == 'skip':
                        n_skipped += 1
                        print(f"  [skip] {result['message']}")
                    else:
                        n_errors += 1
                        print(f"  [error] {result['message']}")

            if not count_tile_paths:
                print(f"  [!] No tiles produced for {vi} — skipping mosaic.")
                continue

            print(f"  Mosaicking {len(count_tile_paths)} tile(s) "
                  f"({n_skipped} skipped, {n_errors} errors)...")

            try:
                mosaic, transform, crs = _mosaic_tiles(
                    count_tile_paths, nodata=0, dtype='uint16'
                )
                profile = {
                    'driver':    'GTiff',
                    'dtype':     'uint16',
                    'nodata':    0,
                    'width':     mosaic.shape[1],
                    'height':    mosaic.shape[0],
                    'count':     1,
                    'crs':       crs,
                    'transform': transform,
                    'compress':   GEOTIFF_COMPRESS,
                    'tiled':      True,
                    'blockxsize': GEOTIFF_BLOCK_SIZE,
                    'blockysize': GEOTIFF_BLOCK_SIZE,
                    'predictor':  2,
                }
                with rasterio.open(output_path, 'w', **profile) as dst:
                    dst.write(mosaic, 1)
                    dst.set_band_description(1, "CountValid_AllDownloadCycles")

                size_mb = os.path.getsize(output_path) / (1024 ** 2)
                print(f"  [{vi}] ✓ Written: {os.path.basename(output_path)}"
                      f"  ({size_mb:.1f} MB)")
            except Exception as e:
                print(f"  [{vi}] ✗ Mosaic failed: {e}")
        print()


# =============================================================================
# ENTRY POINT
# =============================================================================

def main():
    print("=" * 65)
    print(" Step 09: CountValid Mosaic (All Download Cycles)")
    print("=" * 65)
    print(f"NetCDF dir: {NETCDF_DIR}")
    print(f"Mosaic dir: {MOSAIC_DIR}")
    print(f"CRS:        {TARGET_CRS}")
    print()

    build_count_valid_mosaic(PROCESSED_VIS)

    print("=" * 65)
    print(" Step 09 complete.")
    print(f" Output directory: {MOSAIC_DIR}")
    print("=" * 65)


if __name__ == "__main__":
    main()
