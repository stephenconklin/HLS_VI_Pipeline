#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# 10_hls_outlier_gpkg.py
# Pipeline Step 10 (outlier_gpkg): Extract per-pixel outlier observations
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
# Uses ProcessPoolExecutor — each worker handles one (nc_file, vi_type)
# pair and returns the extracted records to the main process for writing.
#
# Author:  Stephen Conklin <stephenconklin@gmail.com>
#          https://github.com/stephenconklin
# License: MIT

import os
import glob
import warnings
import numpy as np
import pandas as pd
import netCDF4 as nc4
import geopandas as gpd
from shapely.geometry import Point
from pyproj import Transformer
from concurrent.futures import ProcessPoolExecutor, as_completed
from hls_utils import filter_by_configured_tiles, get_valid_range

warnings.filterwarnings("ignore")

# --- CONFIGURATION FROM ENV ---
INPUT_FOLDER  = os.environ.get("NETCDF_DIR",        "")
OUTPUT_FOLDER = os.environ.get("OUTLIER_GPKG_DIR",  "")
PROCESSED_VIS = os.environ.get("PROCESSED_VIS",     "NDVI EVI2 NIRv").split()
N_WORKERS     = int(os.environ.get("NUM_WORKERS",    4))

if not INPUT_FOLDER or not OUTPUT_FOLDER:
    raise ValueError("NETCDF_DIR or OUTLIER_GPKG_DIR not set.")

os.makedirs(OUTPUT_FOLDER, exist_ok=True)

EPOCH = pd.Timestamp("1970-01-01")


def _decode_sensor(s) -> str:
    """Decode a netCDF4 S3 byte string to a plain Python string."""
    if hasattr(s, "tobytes"):
        return s.tobytes().decode("utf-8").rstrip("\x00").strip()
    return str(s).strip()


def extract_outliers(args: tuple):
    """
    Worker: open one NetCDF file, find all outlier pixel-date observations
    for the requested VI, and return a dict of columnar arrays.

    Parameters
    ----------
    args : (nc_path, vi_type)

    Returns
    -------
    On success : (dict_of_columns, n_outliers, filename)
    On skip    : "SKIP: ..." string
    On error   : "ERROR: ..." string
    """
    nc_path, vi_type = args
    try:
        filename = os.path.basename(nc_path)
        # Tile ID is the first segment of the filename (e.g. "T18TVL_NDVI.nc" → "T18TVL")
        tile_id = filename.split("_")[0]

        vmin, vmax = get_valid_range(vi_type)

        with nc4.Dataset(nc_path, "r") as ds:
            if vi_type not in ds.variables:
                return f"SKIP: {vi_type} not in {filename}"

            # Read coordinate and metadata arrays
            time_vals   = ds.variables["time"][:]    # int32 days since 1970-01-01, shape (T,)
            x_vals      = ds.variables["x"][:]       # native CRS metres, shape (W,)
            y_vals      = ds.variables["y"][:]       # native CRS metres, shape (H,)
            sensor_vals = ds.variables["sensor"][:]  # S3, shape (T,)

            # Read VI data; netCDF4 returns a masked array — fill with NaN
            raw = ds.variables[vi_type][:]
            data = raw.filled(np.nan) if hasattr(raw, "filled") else np.array(raw, dtype=float)

            # Detect CRS (WKT string) from the two locations step 03 writes it
            crs_wkt = None
            if "crs" in ds.ncattrs():
                crs_wkt = ds.getncattr("crs")
            if not crs_wkt and "spatial_ref" in ds.variables:
                crs_wkt = ds.variables["spatial_ref"].getncattr("spatial_ref")

        if not crs_wkt:
            return f"WARN: No CRS found in {filename}. Skipping."

        # --- Identify outliers ---
        # A pixel is an outlier if it has a finite value AND is outside [vmin, vmax]
        outlier_mask = np.isfinite(data) & ((data < vmin) | (data > vmax))
        n_out = int(outlier_mask.sum())
        if n_out == 0:
            return f"SKIP (no outliers): {filename}"

        # Flat indices → (time, y, x) index triplets
        t_idx, y_idx, x_idx = np.where(outlier_mask)

        # Native pixel-centre coordinates for each outlier
        native_x = x_vals[x_idx]
        native_y = y_vals[y_idx]

        # Reproject from native tile CRS → WGS84 (lon, lat)
        # always_xy=True: input is (easting, northing) → output is (lon, lat)
        transformer = Transformer.from_crs(crs_wkt, "EPSG:4326", always_xy=True)
        lon, lat = transformer.transform(native_x, native_y)

        # Convert integer time values to Python date objects
        dates = [
            (EPOCH + pd.Timedelta(days=int(t))).date()
            for t in time_vals[t_idx]
        ]

        # Decode fixed-length byte strings from the sensor variable
        sensors = [_decode_sensor(sensor_vals[i]) for i in t_idx]

        records = {
            "tile_id":  [tile_id]  * n_out,
            "vi_type":  [vi_type]  * n_out,
            "sensor":   sensors,
            "date":     dates,
            "vi_value": data[t_idx, y_idx, x_idx].tolist(),
            "lon":      lon.tolist(),
            "lat":      lat.tolist(),
        }

        return records, n_out, filename

    except Exception as e:
        return f"ERROR ({os.path.basename(nc_path)}): {e}"


def main():
    print(f"--- Step 10: Outlier GeoPackage Export ---")
    print(f"VIs: {PROCESSED_VIS}  |  Workers: {N_WORKERS}")
    for vi in PROCESSED_VIS:
        vmin, vmax = get_valid_range(vi)
        print(f"  Outlier threshold  {vi}: < {vmin} or > {vmax}")

    all_nc = glob.glob(os.path.join(INPUT_FOLDER, "**", "*.nc"), recursive=True)
    all_nc = filter_by_configured_tiles(all_nc)
    if not all_nc:
        print(f"[ERROR] No NetCDF files found in: {INPUT_FOLDER}")
        return

    for vi_type in PROCESSED_VIS:
        work_items = [
            (nc_path, vi_type)
            for nc_path in all_nc
            if vi_type in os.path.basename(nc_path)
        ]
        if not work_items:
            print(f"\n  No NetCDF files matched for {vi_type}. Skipping.")
            continue

        print(f"\nProcessing {vi_type}: {len(work_items)} file(s)")

        all_records = []
        total_outliers = 0
        completed, total = 0, len(work_items)

        with ProcessPoolExecutor(max_workers=N_WORKERS) as executor:
            futures = {executor.submit(extract_outliers, item): item for item in work_items}
            for future in as_completed(futures):
                completed += 1
                result = future.result()

                if isinstance(result, str):
                    # Skip or error message
                    if not result.startswith("SKIP"):
                        print(f"  [{completed}/{total}] {result}")
                    elif completed % 10 == 0 or completed == total:
                        print(f"  [{completed}/{total}] {result}")
                else:
                    records, n_out, fname = result
                    all_records.append(records)
                    total_outliers += n_out
                    if completed % 5 == 0 or completed == total:
                        print(f"  [{completed}/{total}] OK: {n_out:,} outliers in {fname}")

        if not all_records:
            print(f"  No outliers found for {vi_type}.")
            continue

        print(f"  Building GeoDataFrame ({total_outliers:,} total features)...")

        # Concatenate columnar records from all tiles into a single dict
        combined: dict = {k: [] for k in all_records[0]}
        for rec in all_records:
            for k, v in rec.items():
                combined[k].extend(v)

        df = pd.DataFrame(combined)
        df["date"] = pd.to_datetime(df["date"])

        geometry = [Point(lon, lat) for lon, lat in zip(df["lon"], df["lat"])]
        gdf = gpd.GeoDataFrame(
            df.drop(columns=["lon", "lat"]),
            geometry=geometry,
            crs="EPSG:4326",
        )

        out_path = os.path.join(OUTPUT_FOLDER, f"HLS_outliers_{vi_type}.gpkg")
        gdf.to_file(out_path, driver="GPKG")
        print(f"  Wrote: {out_path}  ({len(gdf):,} features)")

    print("\nStep 10 complete.")


if __name__ == "__main__":
    main()
