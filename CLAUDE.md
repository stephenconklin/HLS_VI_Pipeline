# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Environment Setup

```bash
conda env create -f HLS_VI_Pipeline/environment.yml
conda activate hls_pipeline
export PYTHONUNBUFFERED=1
```

## Running the Pipeline

```bash
cd HLS_VI_Pipeline
bash hls_pipeline.sh
```

The `STEPS` variable in `config.env` controls which steps run. Valid values:
- Named steps: `download`, `vi_calc`, `netcdf`, `mean_flat`, `outlier_flat`, `mean_mosaic`, `outlier_mosaic`, `outlier_counts`, `timeseries`, `outlier_gpkg`
- Aliases: `all` (steps 01–10), `products` (02–10), `mosaics` (06–08), `outliers` (05+07+08+10)

## Configuration

All pipeline parameters live in `HLS_VI_Pipeline/config.env`. Key sections:
- **Paths**: `BASE_DIR`, `LOG_DIR`, `RAW_HLS_DIR`, `VI_OUTPUT_DIR`, `NETCDF_DIR`, `REPROJECTED_DIR`, `REPROJECTED_DIR_OUTLIERS`, `MOSAIC_DIR`, `TIMESLICE_OUTPUT_DIR`, `OUTLIER_GPKG_DIR`
- **Processing**: `NUM_WORKERS`, `CHUNK_SIZE`, `TARGET_CRS` (default `EPSG:6350`)
- **VI selection**: `PROCESSED_VIS` — space-separated list of `NDVI`, `EVI2`, `NIRv`
- **Fmask masking**: Individual boolean flags for cirrus, cloud, adjacent cloud, shadow, snow/ice, water, and aerosol mode (`NONE`/`HIGH`/`MODERATE`/`LOW`)
- **Valid ranges**: Per-VI outlier bounds via `VALID_RANGE_NDVI`, `VALID_RANGE_EVI2`, `VALID_RANGE_NIRv` (format: `"min,max"`, e.g., `"-1,1"`)
- **Tile list** (`HLS_TILES`): Space-separated MGRS tile IDs enforced across all steps (02–10); if unset, no filter is applied
- **Download cycles**: Date ranges
- **Time-series windows**: Named ranges in `label:start|end` format (e.g., `Winter_2015_2016:2015-12-01|2016-03-31`)

Python scripts read all configuration via `os.environ.get()` with fallback defaults — `config.env` is sourced by `hls_pipeline.sh` before dispatching each step.

## Pipeline Architecture

The pipeline is a 10-step sequential workflow for processing HLS (Harmonized Landsat-Sentinel 2) satellite imagery into vegetation index (VI) products:

| Step | Script | Purpose |
|------|--------|---------|
| 01 | `01_hls_download.sh` + `01a_hls_download_query.sh` | Query NASA CMR API; download raw HLS granules (L30/S30 bands + Fmask) |
| 02 | `02_hls_vi_calc.py` | Compute VI GeoTIFFs from raw bands; apply bitwise Fmask masking |
| 03 | `03_hls_netcdf_build.py` | Aggregate per-granule GeoTIFFs into CF-1.8 compliant NetCDF time-series per tile |
| 04 | `04_hls_mean_reproject.py` | Temporal mean per tile; reproject to `TARGET_CRS` |
| 05 | `05_hls_outlier_reproject.py` | Outlier-aware mean + valid count per tile; reproject |
| 06 | `06_hls_mean_mosaic.py` | Mosaic per-tile means into a single GeoTIFF |
| 07 | `07_hls_outlier_mean_mosaic.py` | Mosaic outlier-filtered means |
| 08 | `08_hls_outlier_count_mosaic.py` | Mosaic valid-observation counts |
| 09 | `09_hls_timeseries_mosaic.py` | Multi-band time-window stacks (seasonal composites) |
| 10 | `10_hls_outlier_gpkg.py` | Export per-pixel outlier observations (value, date, location) to a GeoPackage point vector file |

`hls_pipeline.sh` is the master orchestrator: it sources `config.env`, validates that required bands are configured for each requested VI before any step runs, then dispatches the appropriate scripts.

## Shared Utilities

**`hls_utils.py`** — shared utility module imported by all Python pipeline steps (02–10).

**Tile filtering** (used by all steps):
- `get_configured_tiles()` — returns `set` of tile IDs from `HLS_TILES` env var, or empty set (no filter)
- `tile_id_from_path(filepath)` — extracts bare MGRS tile ID from any HLS filename (handles both dot-separated raw/VI GeoTIFF names and underscore-separated NetCDF/reprojected names)
- `filter_by_configured_tiles(filepaths)` — filters a file list to only those matching `HLS_TILES`; pass-through if `HLS_TILES` is unset

**VI valid ranges** (used by steps 04, 05, 09, 10):
- `get_valid_range(vi_type)` — returns `(vmin, vmax)` from `VALID_RANGE_{VI}` env var; falls back to per-VI defaults and prints a warning if the variable is missing or unparseable

**CRS detection** (used by steps 04, 05, 09):
- `detect_crs(ds, da)` — tries `da.rio.crs`, then `ds.attrs['crs']`, then per-variable `crs_wkt`/`spatial_ref` attributes; returns first match or `None`

Add future shared helpers here rather than duplicating across scripts.

## Key Patterns

**Tile enforcement**: `HLS_TILES` in `config.env` is enforced at every processing step. Steps 02–10 call `filter_by_configured_tiles()` immediately after each glob so only configured tiles are processed. Step 01 (download) uses `HLS_TILES` natively via CMR API queries.

**Parallelism**: Step 02 uses `multiprocessing.Pool`; steps 04, 05, 09, and 10 use `ProcessPoolExecutor`. Worker functions must be defined at module top level for pickling. `NUM_WORKERS` in `config.env` controls pool size.

**Chunked spatial processing**: Steps 04, 05, and 09 use xarray + dask (`CHUNK_SIZE` tiles) to avoid loading full rasters into memory.

**Fmask masking**: Step 02 applies bitwise decode of the Fmask band. Aerosol masking has four modes — `NONE`, `HIGH` (masks high only), `MODERATE` (masks moderate + high), `LOW` (masks all non-zero aerosol levels).

**Outlier handling**: "Outliers" are valid (unmasked) pixels that fall outside per-VI min/max bounds. Steps 05/07/08 produce raster summaries (mean + count); step 10 produces a point vector record for every individual outlier pixel-date observation, with coordinates reprojected to WGS84 (EPSG:4326) for GeoPackage compatibility. Uses `geopandas` + `pyproj` + `shapely`.

**Temporal storage**: NetCDF files store dates as integer "days since 1970-01-01". Step 09 parses named time windows from `TIMESLICE_WINDOWS` to produce per-window multi-band mosaics.

**Band requirements**: `hls_pipeline.sh` contains a pre-flight validation block that checks that all bands needed for each requested VI are present in the L30 and S30 band lists before executing any step.
