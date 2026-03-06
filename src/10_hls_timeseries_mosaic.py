#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# 10_hls_timeseries_mosaic.py
# Pipeline Step 10 (timeseries): Build custom time-window VI mosaic stacks.
#   Produces multi-band GeoTIFFs: one band per TIMESLICE_WINDOWS entry.
#
# For each VI in PROCESSED_VIS and each window defined in TIMESLICE_WINDOWS,
# this script:
#   1. Filters the per-tile NetCDF time-series to the window's date range
#   2. Computes per-pixel mean AND count_valid (observations with valid data)
#   3. Reprojects each tile to TARGET_CRS at 30m
#   4. Mosaics all tiles into a continent-wide raster
#   5. Appends the result as a new band in two multi-band GeoTIFF stacks:
#        HLS_TimeSeries_{VI}_Mean_{CRS}.tif       — N bands, float32
#        HLS_TimeSeries_{VI}_CountValid_{CRS}.tif — N bands, uint16
#
# Band descriptions are set to the window label so QGIS / rasterio can
# identify each time slice by name without an external lookup table.
#
# TIMESLICE_WINDOWS format (space-separated in config.env):
#   label:YYYY-MM-DD|YYYY-MM-DD  e.g.  wet_2021:2021-11-01|2022-04-30
#
# Author:  Stephen Conklin <stephenconklin@gmail.com>
#          https://github.com/stephenconklin
# License: MIT

import os
import re
import glob
import warnings
import numpy as np
import pandas as pd
import xarray as xr
import rioxarray         # noqa: F401 — activates .rio accessor
import rasterio
from rasterio.merge import merge as rasterio_merge
import tempfile
from concurrent.futures import ProcessPoolExecutor, as_completed
from hls_utils import filter_by_configured_tiles, get_valid_range, detect_crs, reproject_resolution

warnings.filterwarnings("ignore", category=rasterio.errors.NotGeoreferencedWarning)

# =============================================================================
# CONFIGURATION FROM ENV
# =============================================================================
NETCDF_DIR     = os.environ.get("NETCDF_DIR",          "")
OUTPUT_DIR     = os.environ.get("TIMESLICE_OUTPUT_DIR", "")
TARGET_CRS     = os.environ.get("TARGET_CRS",           "EPSG:6350")
PROCESSED_VIS  = os.environ.get("PROCESSED_VIS",        "NDVI EVI2 NIRv").split()
N_WORKERS          = int(os.environ.get("NUM_WORKERS",       4))
TIMESLICE_STAT     = os.environ.get("TIMESLICE_STAT",       "mean").lower()
_WINDOWS_RAW       = os.environ.get("TIMESLICE_WINDOWS",    "")
GEOTIFF_COMPRESS   = os.environ.get("GEOTIFF_COMPRESS",     "LZW").upper()
GEOTIFF_BLOCK_SIZE = int(os.environ.get("GEOTIFF_BLOCK_SIZE", 512))

if not NETCDF_DIR or not OUTPUT_DIR:
    raise ValueError("NETCDF_DIR or TIMESLICE_OUTPUT_DIR not set in environment.")

os.makedirs(OUTPUT_DIR, exist_ok=True)


# =============================================================================
# WINDOW PARSING
# =============================================================================

def parse_windows(windows_str: str) -> list:
    """
    Parse TIMESLICE_WINDOWS into a list of window dicts.

    Format (space-separated):  label:YYYY-MM-DD|YYYY-MM-DD

    Returns list of: { 'label': str, 'start': pd.Timestamp, 'end': pd.Timestamp }
    """
    if not windows_str.strip():
        raise ValueError(
            "TIMESLICE_WINDOWS is empty. Define at least one window.\n"
            "  Format: label:YYYY-MM-DD|YYYY-MM-DD\n"
            "  Example: wet_2021:2021-11-01|2022-04-30"
        )

    windows    = []
    seen_labels = set()

    for token in windows_str.split():
        token = token.strip()
        if not token:
            continue

        match = re.fullmatch(
            r'([A-Za-z0-9_]+):(\d{4}-\d{2}-\d{2})\|(\d{4}-\d{2}-\d{2})', token
        )
        if not match:
            raise ValueError(
                f"Invalid window token: '{token}'\n"
                f"  Expected: label:YYYY-MM-DD|YYYY-MM-DD\n"
                f"  Labels may only contain letters, digits, and underscores."
            )

        label, start_str, end_str = match.groups()

        if label in seen_labels:
            raise ValueError(f"Duplicate window label '{label}' in TIMESLICE_WINDOWS.")
        seen_labels.add(label)

        try:
            start = pd.Timestamp(start_str)
            end   = pd.Timestamp(end_str)
        except Exception as e:
            raise ValueError(f"Could not parse dates in window '{token}': {e}")

        if start > end:
            raise ValueError(
                f"Window '{label}': start ({start_str}) is after end ({end_str})."
            )

        windows.append({'label': label, 'start': start, 'end': end})

    if not windows:
        raise ValueError("No valid windows found in TIMESLICE_WINDOWS.")

    return windows


# =============================================================================
# PER-TILE WORKER
# =============================================================================

def _process_tile_window(args: dict) -> dict:
    """
    Worker: filter one tile NetCDF to a time window, compute mean +
    count_valid, reproject, write two temp GeoTIFFs.
    """
    import dask

    nc_path      = args['nc_path']
    vi_type      = args['vi_type']
    window_label = args['window_label']
    window_start = args['window_start']
    window_end   = args['window_end']
    target_crs   = args['target_crs']
    temp_dir     = args['temp_dir']
    stat         = args['stat']

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

        # --- Filter time dimension to window ---
        time_vals = pd.to_datetime(
            ds['time'].values, unit='D', origin='unix'
        ) if ds['time'].dtype.kind in ('i', 'u') else pd.DatetimeIndex(ds['time'].values)

        time_mask = (time_vals >= window_start) & (time_vals <= window_end)
        n_obs     = int(time_mask.sum())

        if n_obs == 0:
            ds.close()
            return {
                'status':  'skip',
                'message': f"No observations in [{window_start.date()} – "
                           f"{window_end.date()}] for {tile_id}",
            }

        da_window = da.isel(time=np.where(time_mask)[0])

        # Use per-VI valid range bounds from config.env.
        # Pixels outside the range are excluded from the mean and count_valid.
        vmin, vmax = get_valid_range(vi_type)
        valid      = da_window.where((da_window >= vmin) & (da_window <= vmax))

        # dask.config.set replaces xr.set_options(scheduler=...) which was
        # removed in xarray 2024.x. Prevents nested thread pools inside workers.
        with dask.config.set(scheduler='synchronous'):
            result      = valid.mean(dim='time', skipna=True).compute()
            count_valid = valid.count(dim='time').compute()

        result.rio.write_crs(source_crs, inplace=True)
        count_valid.rio.write_crs(source_crs, inplace=True)

        reproj_mean  = result.rio.reproject(target_crs, resolution=reproject_resolution(target_crs), nodata=np.nan)
        reproj_count = count_valid.rio.reproject(target_crs, resolution=reproject_resolution(target_crs), nodata=0)
        reproj_count = reproj_count.fillna(0).astype('uint16')

        safe_label = re.sub(r'[^A-Za-z0-9_]', '_', window_label)
        mean_tmp   = os.path.join(temp_dir, f"{tile_id}_{vi_type}_{safe_label}_mean.tif")
        count_tmp  = os.path.join(temp_dir, f"{tile_id}_{vi_type}_{safe_label}_count.tif")

        reproj_mean.encoding.clear()
        reproj_mean.rio.to_raster(mean_tmp, compress=GEOTIFF_COMPRESS,
                                   blockxsize=GEOTIFF_BLOCK_SIZE, blockysize=GEOTIFF_BLOCK_SIZE,
                                   dtype='float32', nodata=np.nan)

        reproj_count.encoding.clear()
        reproj_count.rio.write_nodata(0, encoded=True, inplace=True)
        reproj_count.rio.to_raster(count_tmp, compress=GEOTIFF_COMPRESS,
                                    blockxsize=GEOTIFF_BLOCK_SIZE, blockysize=GEOTIFF_BLOCK_SIZE,
                                    dtype='uint16')

        ds.close()
        return {
            'status':     'ok',
            'mean_path':  mean_tmp,
            'count_path': count_tmp,
            'message':    f"OK ({n_obs} obs): {tile_id} / {window_label}",
        }

    except Exception as e:
        return {'status': 'error', 'message': f"Error ({tile_id} / {window_label}): {e}"}


# =============================================================================
# MOSAIC HELPER
# =============================================================================

def _mosaic_temp_tiles(tile_paths: list, nodata, dtype: str) -> tuple:
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
# STACK WRITER
# =============================================================================

def _append_band_to_stack(stack_path: str, band_data: np.ndarray,
                           transform, crs, band_label: str,
                           dtype: str, nodata, predictor: int) -> int:
    """
    Create or append a band to a multi-band GeoTIFF stack.
    Band descriptions are set to band_label for self-documenting output.
    Uses atomic write-then-replace to keep the stack file always valid.
    Existing bands are copied via windowed streaming reads to avoid loading
    the entire stack into RAM (important for large stacks with many windows).
    """
    if not os.path.exists(stack_path):
        profile = {
            'driver': 'GTiff', 'dtype': dtype, 'nodata': nodata,
            'width': band_data.shape[1], 'height': band_data.shape[0],
            'count': 1, 'crs': crs, 'transform': transform,
            'compress': GEOTIFF_COMPRESS, 'tiled': True,
            'blockxsize': GEOTIFF_BLOCK_SIZE, 'blockysize': GEOTIFF_BLOCK_SIZE,
            'predictor': predictor,
            'BIGTIFF': 'YES',   # 64-bit offsets — required when stack exceeds 4 GB
        }
        with rasterio.open(stack_path, 'w', **profile) as dst:
            dst.write(band_data, 1)
            dst.update_tags(1, label=band_label)
            dst.set_band_description(1, band_label)
        return 1

    tmp_path = stack_path + '.tmp'
    try:
        with rasterio.open(stack_path, 'r') as src:
            existing_count = src.count
            existing_descs = list(src.descriptions)
            profile        = src.profile.copy()
            profile.update(count=existing_count + 1)
            profile['BIGTIFF'] = 'YES'   # Ensure .tmp is also BigTIFF

            with rasterio.open(tmp_path, 'w', **profile) as dst:
                # Stream existing bands block-by-block — no full-stack RAM load
                for b_idx in range(1, existing_count + 1):
                    for _, window in src.block_windows(b_idx):
                        dst.write(src.read(b_idx, window=window), b_idx, window=window)
                    dst.set_band_description(b_idx, existing_descs[b_idx - 1] or "")

                # Write the new band
                new_band = existing_count + 1
                dst.write(band_data, new_band)
                dst.update_tags(new_band, label=band_label)
                dst.set_band_description(new_band, band_label)

        os.replace(tmp_path, stack_path)

    except Exception:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise

    return existing_count + 1


# =============================================================================
# MAIN ORCHESTRATION
# =============================================================================

def build_timeseries_stacks(windows: list, processed_vis: list):
    safe_crs = TARGET_CRS.replace(':', '')

    all_nc = glob.glob(os.path.join(NETCDF_DIR, "**", "*.nc"), recursive=True)
    all_nc = filter_by_configured_tiles(all_nc)
    if not all_nc:
        print(f"[ERROR] No NetCDF files found in {NETCDF_DIR}")
        return

    print(f"Found {len(all_nc)} NetCDF files across all tiles/VIs.")
    print(f"Windows: {[w['label'] for w in windows]}")
    print(f"VIs:     {processed_vis}")
    print(f"Stat:    {TIMESLICE_STAT}  |  Workers: {N_WORKERS}")
    for vi in processed_vis:
        vmin, vmax = get_valid_range(vi)
        print(f"  Valid range  {vi}: [{vmin}, {vmax}]")
    print()

    for vi in processed_vis:
        mean_stack_path  = os.path.join(OUTPUT_DIR, f"HLS_TimeSeries_{vi}_Mean_{safe_crs}.tif")
        count_stack_path = os.path.join(OUTPUT_DIR, f"HLS_TimeSeries_{vi}_CountValid_{safe_crs}.tif")

        vi_nc_files = [f for f in all_nc if vi in os.path.basename(f)]
        if not vi_nc_files:
            print(f"[{vi}] No NetCDF files found — skipping.")
            continue

        print(f"[{vi}] {len(vi_nc_files)} tile file(s)  →  {len(windows)} window(s)")
        print(f"[{vi}] Mean stack:  {os.path.basename(mean_stack_path)}")
        print(f"[{vi}] Count stack: {os.path.basename(count_stack_path)}")

        # Remove any stale partial stacks so we always build fresh
        for p in [mean_stack_path, count_stack_path]:
            if os.path.exists(p):
                print(f"  [!] Removing existing stack: {os.path.basename(p)}")
                os.remove(p)

        for w_idx, window in enumerate(windows, 1):
            label = window['label']
            start = window['start']
            end   = window['end']
            print(f"  [{vi}] Window {w_idx}/{len(windows)}: {label}  "
                  f"({start.date()} – {end.date()})")

            with tempfile.TemporaryDirectory(prefix=f"hls_{vi}_{label}_") as tmp_dir:
                worker_args = [
                    {
                        'nc_path': nc, 'vi_type': vi,
                        'window_label': label, 'window_start': start,
                        'window_end': end, 'target_crs': TARGET_CRS,
                        'temp_dir': tmp_dir, 'stat': TIMESLICE_STAT,
                    }
                    for nc in vi_nc_files
                ]

                mean_tile_paths  = []
                count_tile_paths = []
                n_skipped = n_errors = 0

                with ProcessPoolExecutor(max_workers=N_WORKERS) as executor:
                    futures = {executor.submit(_process_tile_window, a): a
                               for a in worker_args}
                    for future in as_completed(futures):
                        result = future.result()
                        if result['status'] == 'ok':
                            mean_tile_paths.append(result['mean_path'])
                            count_tile_paths.append(result['count_path'])
                        elif result['status'] == 'skip':
                            n_skipped += 1
                            print(f"    [skip] {result['message']}")
                        else:
                            n_errors += 1
                            print(f"    [error] {result['message']}")

                if not mean_tile_paths:
                    print(f"    [!] No tiles produced for window '{label}' — skipping band.")
                    continue

                print(f"    Mosaicking {len(mean_tile_paths)} tile(s) "
                      f"({n_skipped} skipped, {n_errors} errors)...")

                try:
                    mean_mosaic, transform, crs = _mosaic_temp_tiles(
                        mean_tile_paths, nodata=np.nan, dtype='float32'
                    )
                    band_num = _append_band_to_stack(
                        mean_stack_path, mean_mosaic, transform, crs,
                        band_label=label, dtype='float32', nodata=np.nan, predictor=3
                    )
                    print(f"    ✓ Mean band {band_num} written: '{label}'")
                except Exception as e:
                    print(f"    ✗ Mean mosaic failed for '{label}': {e}")
                    continue

                try:
                    count_mosaic, transform, crs = _mosaic_temp_tiles(
                        count_tile_paths, nodata=0, dtype='uint16'
                    )
                    band_num = _append_band_to_stack(
                        count_stack_path, count_mosaic, transform, crs,
                        band_label=label, dtype='uint16', nodata=0, predictor=2
                    )
                    print(f"    ✓ CountValid band {band_num} written: '{label}'")
                except Exception as e:
                    print(f"    ✗ Count mosaic failed for '{label}': {e}")

        # Final stack summary
        for stack_path, stack_name in [(mean_stack_path, "Mean"),
                                        (count_stack_path, "CountValid")]:
            if os.path.exists(stack_path):
                with rasterio.open(stack_path) as src:
                    bands = src.count
                    descs = [src.descriptions[i] or f"band_{i+1}" for i in range(bands)]
                size_mb = os.path.getsize(stack_path) / (1024 ** 2)
                print(f"\n  [{vi}] {stack_name} stack: {bands} band(s), {size_mb:.1f} MB")
                for i, d in enumerate(descs, 1):
                    print(f"    Band {i:2d}: {d}")
        print()


# =============================================================================
# ENTRY POINT
# =============================================================================

def main():
    print("=" * 65)
    print(" Step 10: Custom Time-Window VI Mosaic Stacks")
    print("=" * 65)

    try:
        windows = parse_windows(_WINDOWS_RAW)
    except ValueError as e:
        print(f"[ERROR] {e}")
        exit(1)

    print(f"Parsed {len(windows)} window(s):")
    for w in windows:
        n_days = (w['end'] - w['start']).days + 1
        print(f"  {w['label']:30s}  {w['start'].date()} – {w['end'].date()}  ({n_days} days)")
    print()

    build_timeseries_stacks(windows, PROCESSED_VIS)

    print("=" * 65)
    print(" Step 10 complete.")
    print(f" Output directory: {OUTPUT_DIR}")
    print("=" * 65)


if __name__ == "__main__":
    main()
