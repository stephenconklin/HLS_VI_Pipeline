#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# 11_hls_outlier_gpkg.py
# Pipeline Step 11 (outlier_gpkg): Extract per-pixel outlier observations
#   from VI NetCDF time-series and write to a GeoPackage point vector file.
#
# Reads each per-tile NetCDF from NETCDF_DIR, identifies pixels whose VI
# value falls outside the per-VI valid range (same thresholds as step 05),
# and records one point feature per outlier pixel-date observation.
#
# Output: one GeoPackage per VI in OUTLIER_GPKG_DIR, e.g.:
#   HLS_outliers_NDVI.gpkg
#
# Feature attributes: tile_id, vi_type, sensor, date, vi_value
# Geometry: Point (WGS84 / EPSG:4326), one point per pixel centroid
#
# Memory-efficient design:
#   - Sequential tile processing — only one tile's data in RAM at a time
#   - Time-axis chunked loading (TIME_CHUNK slices per iteration) — avoids
#     loading the full 3-D array into memory at once
#   - Streaming fiona writes — each chunk is written and freed immediately,
#     no cross-tile accumulation in the main process
#
# Author:  Stephen Conklin <stephenconklin@gmail.com>
#          https://github.com/stephenconklin
# License: MIT

import os
import gc
import glob
import warnings
import fiona
import numpy as np
import pandas as pd
import netCDF4 as nc4
from pyproj import Transformer
from hls_utils import filter_by_configured_tiles, get_valid_range

warnings.filterwarnings("ignore")

# --- CONFIGURATION FROM ENV ---
INPUT_FOLDER  = os.environ.get("NETCDF_DIR",        "")
OUTPUT_FOLDER = os.environ.get("OUTLIER_GPKG_DIR",  "")
PROCESSED_VIS = os.environ.get("PROCESSED_VIS",     "NDVI EVI2 NIRv").split()

# Number of time slices loaded per iteration — lower values use less memory.
# 10 is a safe default for large tiles; raise to 20–50 if RAM allows.
TIME_CHUNK = 10

GPKG_SCHEMA = {
    "geometry": "Point",
    "properties": {
        "tile_id":  "str",
        "vi_type":  "str",
        "sensor":   "str",
        "date":     "date",
        "vi_value": "float",
    },
}

EPOCH = pd.Timestamp("1970-01-01")


def _decode_sensor(s) -> str:
    """Decode a netCDF4 S3 byte string to a plain Python string."""
    if hasattr(s, "tobytes"):
        return s.tobytes().decode("utf-8").rstrip("\x00").strip()
    return str(s).strip()


def iter_tile_chunks(nc_path, vi_type, vmin, vmax):
    """
    Generator: open one NetCDF tile, load VI data TIME_CHUNK slices at a
    time, and yield a list of fiona feature dicts for each chunk that
    contains outliers.

    Keeps only one time-chunk in memory at a time. The NetCDF dataset stays
    open across yields and is closed when the generator is exhausted or
    garbage-collected.

    Parameters
    ----------
    nc_path       : str   — path to the NetCDF file
    vi_type       : str   — VI variable name (e.g. "NDVI")
    vmin, vmax    : float — outlier bounds (values outside → outlier)

    Yields
    ------
    list[dict] — fiona feature dicts for one chunk of outliers
    """
    filename = os.path.basename(nc_path)
    tile_id  = filename.split("_")[0]

    with nc4.Dataset(nc_path, "r") as ds:
        if vi_type not in ds.variables:
            return  # VI absent — caller sees empty iteration

        # CRS detection — same two locations step 03 writes it
        crs_wkt = None
        if "crs" in ds.ncattrs():
            crs_wkt = ds.getncattr("crs")
        if not crs_wkt and "spatial_ref" in ds.variables:
            crs_wkt = ds.variables["spatial_ref"].getncattr("spatial_ref")
        if not crs_wkt:
            print(f"  WARN: No CRS found in {filename}. Skipping.")
            return

        time_vals   = ds.variables["time"][:]    # int32 days since 1970-01-01, shape (T,)
        x_vals      = ds.variables["x"][:]       # native CRS metres, shape (W,)
        y_vals      = ds.variables["y"][:]       # native CRS metres, shape (H,)
        sensor_vals = ds.variables["sensor"][:]  # S3, shape (T,)
        n_times     = len(time_vals)

        transformer = Transformer.from_crs(crs_wkt, "EPSG:4326", always_xy=True)

        for t_start in range(0, n_times, TIME_CHUNK):
            t_end = min(t_start + TIME_CHUNK, n_times)

            # Load one chunk of the VI variable — shape (chunk, H, W)
            raw_chunk  = ds.variables[vi_type][t_start:t_end, :, :]
            data_chunk = (
                raw_chunk.filled(np.nan) if hasattr(raw_chunk, "filled")
                else np.array(raw_chunk, dtype=float)
            )
            del raw_chunk

            # Outlier: finite value outside [vmin, vmax]
            outlier_mask = (
                np.isfinite(data_chunk) & ((data_chunk < vmin) | (data_chunk > vmax))
            )
            if not outlier_mask.any():
                del data_chunk, outlier_mask
                gc.collect()
                continue

            ct_idx, y_idx, x_idx = np.where(outlier_mask)
            t_global = ct_idx + t_start   # chunk-local → global time index

            native_x = x_vals[x_idx]
            native_y = y_vals[y_idx]
            lon, lat = transformer.transform(native_x, native_y)

            features = []
            for i in range(len(ct_idx)):
                date_str = str(
                    (EPOCH + pd.Timedelta(days=int(time_vals[t_global[i]]))).date()
                )
                features.append({
                    "geometry": {
                        "type": "Point",
                        "coordinates": (float(lon[i]), float(lat[i])),
                    },
                    "properties": {
                        "tile_id":  tile_id,
                        "vi_type":  vi_type,
                        "sensor":   _decode_sensor(sensor_vals[t_global[i]]),
                        "date":     date_str,
                        "vi_value": float(data_chunk[ct_idx[i], y_idx[i], x_idx[i]]),
                    },
                })

            yield features

            del data_chunk, outlier_mask, features, lon, lat
            gc.collect()


def main():
    if not INPUT_FOLDER or not OUTPUT_FOLDER:
        raise ValueError("NETCDF_DIR or OUTLIER_GPKG_DIR not set.")
    os.makedirs(OUTPUT_FOLDER, exist_ok=True)

    print("--- Step 11: Outlier GeoPackage Export ---")
    print(f"VIs: {PROCESSED_VIS}  |  Time chunk: {TIME_CHUNK} slices")
    for vi in PROCESSED_VIS:
        vmin, vmax = get_valid_range(vi)
        print(f"  Outlier threshold  {vi}: < {vmin} or > {vmax}")

    all_nc = glob.glob(os.path.join(INPUT_FOLDER, "**", "*.nc"), recursive=True)
    all_nc = filter_by_configured_tiles(all_nc)
    if not all_nc:
        print(f"[ERROR] No NetCDF files found in: {INPUT_FOLDER}")
        return

    for vi_type in PROCESSED_VIS:
        vmin, vmax = get_valid_range(vi_type)
        work_items = [
            nc_path for nc_path in all_nc
            if vi_type in os.path.basename(nc_path)
        ]
        if not work_items:
            print(f"\n  No NetCDF files matched for {vi_type}. Skipping.")
            continue

        out_path = os.path.join(OUTPUT_FOLDER, f"HLS_outliers_{vi_type}.gpkg")
        if os.path.exists(out_path):
            os.remove(out_path)

        print(f"\nProcessing {vi_type}: {len(work_items)} file(s)")

        total_outliers = 0
        n_tiles = len(work_items)

        # Open one GPKG file for the whole VI; each tile's chunks stream into it.
        with fiona.open(
            out_path, "w", driver="GPKG", schema=GPKG_SCHEMA, crs="EPSG:4326"
        ) as dst:
            for tile_idx, nc_path in enumerate(work_items, 1):
                filename   = os.path.basename(nc_path)
                n_tile_out = 0

                print(f"  [{tile_idx}/{n_tiles}] {filename}", flush=True)

                try:
                    for chunk_features in iter_tile_chunks(nc_path, vi_type, vmin, vmax):
                        dst.writerecords(chunk_features)
                        n_tile_out += len(chunk_features)
                except Exception as e:
                    print(f"    ERROR: {e}", flush=True)
                    continue

                total_outliers += n_tile_out
                if n_tile_out > 0:
                    print(
                        f"    OK: {n_tile_out:,} outliers"
                        f"  (running total: {total_outliers:,})",
                        flush=True,
                    )
                else:
                    print(f"    no outliers", flush=True)

        if total_outliers > 0:
            print(f"  Wrote: {out_path}  ({total_outliers:,} features)")
        else:
            print(f"  No outliers found for {vi_type}.")
            if os.path.exists(out_path):
                os.remove(out_path)

    print("\nStep 11 complete.")


if __name__ == "__main__":
    main()
