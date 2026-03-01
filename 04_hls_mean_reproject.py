#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# 04_hls_mean_reproject.py
# Pipeline Step 04 (mean_flat): Compute per-pixel temporal mean for each VI,
#   reproject to TARGET_CRS, write Cloud-Optimised GeoTIFFs.
#
# Reads PROCESSED_VIS from env and processes ALL listed VIs in one run.
# Files are processed in parallel across tiles using ProcessPoolExecutor.
# Uses dask.config.set(scheduler='synchronous') inside each worker to prevent
# nested thread pools competing for CPU cores.
#
# Author:  Stephen Conklin <stephenconklin@gmail.com>
#          https://github.com/stephenconklin
# License: MIT

import xarray as xr
import rioxarray          # noqa: F401 — activates .rio accessor
import rasterio
import os
import glob
import warnings
import numpy as np
from concurrent.futures import ProcessPoolExecutor, as_completed
from hls_utils import filter_by_configured_tiles, get_valid_range, detect_crs, reproject_resolution

warnings.filterwarnings("ignore", category=rasterio.errors.NotGeoreferencedWarning)

# --- CONFIGURATION FROM ENV ---
INPUT_FOLDER  = os.environ.get("NETCDF_DIR",      "")
OUTPUT_FOLDER = os.environ.get("REPROJECTED_DIR", "")
TARGET_CRS    = os.environ.get("TARGET_CRS",      "EPSG:6350")
PROCESSED_VIS = os.environ.get("PROCESSED_VIS",  "NDVI EVI2 NIRv").split()
N_WORKERS     = int(os.environ.get("NUM_WORKERS", 4))

if not INPUT_FOLDER or not OUTPUT_FOLDER:
    raise ValueError("NETCDF_DIR or REPROJECTED_DIR not set.")

os.makedirs(OUTPUT_FOLDER, exist_ok=True)


def process_file(args):
    """
    Worker: compute temporal mean for one (nc_path, vi_type) pair,
    reproject to TARGET_CRS, and write a GeoTIFF.
    """
    import dask
    nc_path, vi_type = args
    try:
        filename    = os.path.basename(nc_path)
        safe_crs    = TARGET_CRS.replace(':', '')
        output_name = filename.replace(".nc", f"_average_{vi_type}_{safe_crs}.tif")
        output_path = os.path.join(OUTPUT_FOLDER, output_name)

        if os.path.exists(output_path):
            return f"Skipped (Exists): {vi_type} / {filename}"

        ds = xr.open_dataset(nc_path, chunks='auto')

        if vi_type in ds.data_vars:
            da = ds[vi_type]
        else:
            candidates = [v for v in ds.data_vars if vi_type.lower() in v.lower()]
            if not candidates:
                ds.close()
                return f"Error: {vi_type} not found in {filename}"
            da = ds[candidates[0]]

        source_crs = detect_crs(ds, da)
        if source_crs is None:
            ds.close()
            return f"WARNING: No CRS in {filename}. Skipping."

        vmin, vmax = get_valid_range(vi_type)
        valid_data = da.where((da >= vmin) & (da <= vmax))

        # dask.config.set replaces xr.set_options(scheduler=...) which was
        # removed in xarray 2024.x. Forces synchronous execution inside each
        # worker process to prevent nested thread pools competing for cores.
        with dask.config.set(scheduler='synchronous'):
            mean_val = valid_data.mean(dim='time', skipna=True, keep_attrs=True).compute()

        mean_val.rio.write_crs(source_crs, inplace=True)
        reprojected = mean_val.rio.reproject(TARGET_CRS, resolution=reproject_resolution(TARGET_CRS), nodata=np.nan)
        reprojected.rio.to_raster(
            output_path, compress='LZW', tiled=True, dtype='float32', nodata=np.nan
        )
        ds.close()
        return f"OK: {vi_type} / {filename}"

    except Exception as e:
        return f"Error ({vi_type} / {os.path.basename(nc_path)}): {e}"


def main():
    print(f"--- Step 04: Temporal Mean + Reproject to {TARGET_CRS} ---")
    print(f"VIs: {PROCESSED_VIS}  |  Workers: {N_WORKERS}")
    for vi in PROCESSED_VIS:
        vmin, vmax = get_valid_range(vi)
        print(f"  Valid range  {vi}: [{vmin}, {vmax}]")

    all_nc_files = glob.glob(os.path.join(INPUT_FOLDER, "**", "*.nc"), recursive=True)
    all_nc_files = filter_by_configured_tiles(all_nc_files)
    if not all_nc_files:
        print(f"[ERROR] No NetCDF files found in: {INPUT_FOLDER}")
        return

    # Build (nc_path, vi_type) work items — match each file to its VI by name
    work_items = []
    for nc_path in all_nc_files:
        fname = os.path.basename(nc_path)
        for vi in PROCESSED_VIS:
            if vi in fname:
                work_items.append((nc_path, vi))
                break

    print(f"Found {len(all_nc_files)} NetCDF files → {len(work_items)} work items "
          f"across {PROCESSED_VIS}.")

    if not work_items:
        print("No matching files. Check PROCESSED_VIS and NETCDF_DIR.")
        return

    completed, total = 0, len(work_items)
    with ProcessPoolExecutor(max_workers=N_WORKERS) as executor:
        futures = {executor.submit(process_file, item): item for item in work_items}
        for future in as_completed(futures):
            completed += 1
            result = future.result()
            if result.startswith("OK"):
                if completed % 5 == 0 or completed == total:
                    print(f"  [{completed}/{total}] {result}")
            else:
                print(f"  [{completed}/{total}] {result}")

    print(f"Step 04 complete. Processed {total} work items.")


if __name__ == "__main__":
    main()
