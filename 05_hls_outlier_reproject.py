#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# 05_hls_outlier_reproject.py
# Pipeline Step 05 (outlier_flat): Extract outlier pixels (VI outside [-1,1])
#   for each VI. Compute temporal mean + count, reproject, write GeoTIFFs.
#
# Reads PROCESSED_VIS from env and processes ALL listed VIs in one run.
# Produces two output GeoTIFFs per tile per VI:
#   *_outlier_mean_{VI}_{CRS}.tif   — float32, temporal mean of outlier values
#   *_outlier_count_{VI}_{CRS}.tif  — uint16, count of outlier observations
#
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
INPUT_FOLDER  = os.environ.get("NETCDF_DIR",               "")
OUTPUT_FOLDER = os.environ.get("REPROJECTED_DIR_OUTLIERS", "")
TARGET_CRS    = os.environ.get("TARGET_CRS",               "EPSG:6350")
PROCESSED_VIS = os.environ.get("PROCESSED_VIS",           "NDVI EVI2 NIRv").split()
N_WORKERS     = int(os.environ.get("NUM_WORKERS",          4))

if not INPUT_FOLDER or not OUTPUT_FOLDER:
    raise ValueError("NETCDF_DIR or REPROJECTED_DIR_OUTLIERS not set.")

os.makedirs(OUTPUT_FOLDER, exist_ok=True)


def process_file(args):
    """
    Worker: extract outlier pixels for one (nc_path, vi_type) pair,
    compute temporal mean + count, reproject, write two GeoTIFFs.
    """
    import dask
    nc_path, vi_type = args
    try:
        filename = os.path.basename(nc_path)
        safe_crs = TARGET_CRS.replace(':', '')

        mean_path  = os.path.join(OUTPUT_FOLDER,
                        filename.replace(".nc", f"_outlier_mean_{vi_type}_{safe_crs}.tif"))
        count_path = os.path.join(OUTPUT_FOLDER,
                        filename.replace(".nc", f"_outlier_count_{vi_type}_{safe_crs}.tif"))

        if os.path.exists(mean_path) and os.path.exists(count_path):
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

        # Remove NetCDF fill values before testing for outlier range
        clean_da = da.where(da < 1e30)

        # Pixels outside the VI-specific valid range are outliers.
        # Bounds are read from VALID_RANGE_{VI} in config.env.
        vmin, vmax   = get_valid_range(vi_type)
        outlier_data = clean_da.where((clean_da < vmin) | (clean_da > vmax))

        # dask.config.set replaces xr.set_options(scheduler=...) which was
        # removed in xarray 2024.x.
        with dask.config.set(scheduler='synchronous'):
            n_outliers = outlier_data.count().compute().item()

        if n_outliers == 0:
            ds.close()
            return f"Skipped (No outliers): {vi_type} / {filename}"

        # --- Outlier mean ---
        with dask.config.set(scheduler='synchronous'):
            outlier_mean = outlier_data.mean(dim='time', skipna=True).compute()

        outlier_mean.rio.write_crs(source_crs, inplace=True)
        reproj_mean = outlier_mean.rio.reproject(TARGET_CRS, resolution=reproject_resolution(TARGET_CRS))
        reproj_mean.encoding.clear()
        reproj_mean.rio.to_raster(mean_path, compress='LZW', tiled=True,
                                   dtype='float32', nodata=np.nan)

        # --- Outlier count ---
        with dask.config.set(scheduler='synchronous'):
            outlier_count = outlier_data.count(dim='time').compute()

        outlier_count.rio.write_crs(source_crs, inplace=True)
        reproj_count = outlier_count.rio.reproject(TARGET_CRS, resolution=reproject_resolution(TARGET_CRS))
        reproj_count = reproj_count.fillna(0).astype('uint16')
        reproj_count.rio.write_nodata(0, encoded=True, inplace=True)
        reproj_count.encoding.clear()
        reproj_count.rio.to_raster(count_path, compress='LZW', tiled=True, dtype='uint16')

        ds.close()
        return f"OK: {vi_type} / {filename}"

    except Exception as e:
        return f"Error ({vi_type} / {os.path.basename(nc_path)}): {e}"


def main():
    print(f"--- Step 05: Outlier Extraction + Reproject to {TARGET_CRS} ---")
    print(f"VIs: {PROCESSED_VIS}  |  Workers: {N_WORKERS}")
    for vi in PROCESSED_VIS:
        vmin, vmax = get_valid_range(vi)
        print(f"  Outlier threshold  {vi}: < {vmin} or > {vmax}")

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

    print(f"Step 05 complete. Processed {total} work items.")


if __name__ == "__main__":
    main()
