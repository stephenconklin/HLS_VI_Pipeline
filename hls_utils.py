#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# hls_utils.py
# Shared utilities for the HLS_VI_Pipeline.
# All pipeline steps (02–11) import from here.
#
# Author:  Stephen Conklin <stephenconklin@gmail.com>
#          https://github.com/stephenconklin
# License: MIT

import os


# ---------------------------------------------------------------------------
# Tile filtering
# ---------------------------------------------------------------------------

def get_configured_tiles():
    """Return the set of tile IDs from HLS_TILES env var, or empty set.

    An empty set means no tile filter is active (all tiles are processed),
    which preserves backward-compatible behaviour when HLS_TILES is unset.
    """
    raw = os.environ.get("HLS_TILES", "").strip()
    return set(raw.split()) if raw else set()


def tile_id_from_path(filepath):
    """Extract the bare MGRS tile ID (e.g. '34HBH') from any HLS pipeline filename.

    Handles two filename conventions used by the pipeline:

      Format 1 — raw HLS bands and VI GeoTIFFs (dot-separated):
        HLS.L30.T34HBH.2023001T154931.v2.0.NDVI.tif
        → parts[2] = 'T34HBH' → '34HBH'

      Format 2 — NetCDF time-series and reprojected GeoTIFFs (underscore-separated):
        T34HBH_NDVI.nc
        T34HBH_NDVI_average_NDVI_EPSG6350.tif
        → first '_'-token = 'T34HBH' → '34HBH'

    Returns None if the tile ID cannot be determined.
    """
    name = os.path.basename(filepath)

    # Format 1: HLS.L30.T34HBH.…
    parts = name.split('.')
    if len(parts) >= 3 and parts[2].startswith('T'):
        return parts[2][1:]          # strip leading 'T'

    # Format 2: T34HBH_…
    tok = name.split('_')[0]
    if tok.startswith('T') and len(tok) > 1:
        return tok[1:]               # strip leading 'T'

    return None


def filter_by_configured_tiles(filepaths):
    """Return only the paths whose tile ID appears in HLS_TILES.

    If HLS_TILES is not set or empty, all paths are returned unchanged so that
    the pipeline behaves identically to how it did before this filter was added.
    """
    configured = get_configured_tiles()
    if not configured:
        return filepaths
    return [f for f in filepaths if tile_id_from_path(f) in configured]


# ---------------------------------------------------------------------------
# VI valid-range lookup
# ---------------------------------------------------------------------------

def get_valid_range(vi_type: str) -> tuple:
    """Return (vmin, vmax) for a vegetation index from VALID_RANGE_{VI} env var.

    Config keys: VALID_RANGE_NDVI, VALID_RANGE_EVI2, VALID_RANGE_NIRv
    Format:      "min,max"  e.g. "-1,1"

    Falls back to conservative per-VI defaults when the variable is absent or
    cannot be parsed, and prints a warning in that case.
    """
    defaults = {"NDVI": (-1.0, 1.0), "EVI2": (-1.0, 2.0), "NIRv": (-0.5, 1.0)}
    raw = os.environ.get(f"VALID_RANGE_{vi_type}", "")
    if raw:
        try:
            parts = raw.split(",")
            return float(parts[0]), float(parts[1])
        except (ValueError, IndexError):
            print(f"  [WARN] Could not parse VALID_RANGE_{vi_type}='{raw}'. "
                  f"Using default {defaults.get(vi_type, (-1.0, 1.0))}.")
    return defaults.get(vi_type, (-1.0, 1.0))


# ---------------------------------------------------------------------------
# CRS detection (xarray / rioxarray datasets)
# ---------------------------------------------------------------------------

def detect_crs(ds, da):
    """Try multiple CRS detection paths on an xarray Dataset/DataArray pair.

    Checks, in order:
      1. da.rio.crs          (rioxarray spatial_ref coordinate)
      2. ds.attrs['crs']     (global NetCDF attribute written by step 03)
      3. data-var attributes 'crs_wkt' / 'spatial_ref'

    Returns the first CRS found, or None if none is detected.
    Requires rioxarray to be imported in the calling module (noqa import).
    """
    crs = da.rio.crs
    if crs:
        return crs
    crs = ds.attrs.get('crs')
    if crs:
        return crs
    for var in ds.data_vars:
        crs = ds[var].attrs.get('crs_wkt') or ds[var].attrs.get('spatial_ref')
        if crs:
            return crs
    return None
