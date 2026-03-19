# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commit Style

Do NOT include `Co-Authored-By: Claude` trailers in any commit messages for this project.

## Environment Setup

```bash
conda env create -f environment.yml
conda activate hls_pipeline
export PYTHONUNBUFFERED=1
```

Step 01 (download) also requires:
- `~/.netrc` with NASA Earthdata credentials (`machine urs.earthdata.nasa.gov login <user> password <pass>`) — the script exits immediately if this file is absent.
- `wget` or `curl` available on `PATH` (the script probes for both and uses whichever is found).

## Running the Pipeline

```bash
conda activate hls_pipeline
bash hls_pipeline.sh
```

`hls_pipeline.sh` loads `config.env` first, then automatically loads `config.local.env` if it exists. Use `config.local.env` for project-specific or machine-specific overrides without modifying the committed `config.env` (it is gitignored). Any variable set in `config.local.env` overrides the same variable from `config.env`.

The `STEPS` variable in `config.env` controls which steps run. Valid values:
- Named steps: `download`, `vi_calc`, `netcdf`, `mean_flat`, `outlier_flat`, `mean_mosaic`, `outlier_mosaic`, `outlier_counts`, `count_valid_mosaic`, `timeseries`, `outlier_gpkg`
- Aliases: `all` (steps 01–11), `products` (02–11), `build_nc` (01–03), `mosaics` (06–08), `outliers` (05+07+08+11)

## Configuration

All pipeline parameters live in `config.env`. Key sections:
- **Paths**: `BASE_DIR`, `LOG_DIR`, `RAW_HLS_DIR`, `VI_OUTPUT_DIR`, `NETCDF_DIR`, `REPROJECTED_DIR`, `REPROJECTED_DIR_OUTLIERS`, `MOSAIC_DIR`, `TIMESLICE_OUTPUT_DIR`, `OUTLIER_GPKG_DIR`
- **Processing**: `NUM_WORKERS`, `CHUNK_SIZE`, `TARGET_CRS` (default `EPSG:6350` — NAD83 Conus Albers, 30 m output resolution; must be a projected CRS in metres)
- **Download filters**: `CLOUD_COVERAGE_MAX` (0–100, default `75`), `SPATIAL_COVERAGE_MIN` (0–100, default `0`) — CMR-side granule filters applied before download
- **Output format**: `NETCDF_COMPLEVEL` (int 0–9, default `1` — zlib level for step 03 NetCDF); `GEOTIFF_COMPRESS` (default `LZW` — codec for all GeoTIFF outputs, steps 02 + 04–10); `GEOTIFF_BLOCK_SIZE` (int, default `512` — tile block dimension for tiled GeoTIFFs, steps 04–10)
- **VI selection**: `PROCESSED_VIS` — space-separated list of `NDVI`, `EVI2`, `NIRv`
- **Fmask masking**: Individual boolean flags for cirrus, cloud, adjacent cloud, shadow, snow/ice, water, and aerosol mode (`NONE`/`HIGH`/`MODERATE`/`LOW`)
- **Valid ranges**: Per-VI outlier bounds via `VALID_RANGE_NDVI`, `VALID_RANGE_EVI2`, `VALID_RANGE_NIRv` (format: `"min,max"`; defaults: NDVI `"-1,1"`, EVI2 `"-1,2"`, NIRv `"-0.5,1"`)
- **Tile list** (`HLS_TILES`): Space-separated MGRS tile IDs enforced across all steps (02–11); if unset, no filter is applied
- **Download cycles**: Date ranges in `YYYY-MM-DD|YYYY-MM-DD` format
- **Time-series windows**: `TIMESLICE_WINDOWS` — space-separated `label:YYYY-MM-DD|YYYY-MM-DD` tokens (labels: alphanumeric + underscores, start ≤ end)
- **Space savers**: `SPACE_SAVER_REMOVE_RAW` and `SPACE_SAVER_REMOVE_VI` (`TRUE`/`FALSE`) delete raw HLS files and/or VI GeoTIFFs respectively after each tile's NetCDF is built (both default `FALSE`; only fires when `netcdf` is in `STEPS`)
- **Download approval**: `SKIP_APPROVAL` (`TRUE`/`FALSE`) — bypasses the interactive download confirmation prompt; use for automated/non-interactive runs (default `FALSE`)

Python scripts read all configuration via `os.environ.get()` with fallback defaults — `config.env` is sourced by `hls_pipeline.sh` before dispatching each step.

## Pipeline Architecture

The pipeline is an 11-step sequential workflow for processing HLS (Harmonized Landsat-Sentinel 2) satellite imagery into vegetation index (VI) products:

| Step | Script | Purpose |
|------|--------|---------|
| 01 | `src/01_hls_download_query.sh` | Query NASA CMR API; download raw HLS granules (L30/S30 bands + Fmask) |
| 02 | `src/02_hls_vi_calc.py` | Compute VI GeoTIFFs from raw bands; apply bitwise Fmask masking |
| 03 | `src/03_hls_netcdf_build.py` | Aggregate per-granule GeoTIFFs into CF-1.8 compliant NetCDF time-series per tile |
| 04 | `src/04_hls_mean_reproject.py` | Temporal mean per tile; reproject to `TARGET_CRS` |
| 05 | `src/05_hls_outlier_reproject.py` | Outlier-aware mean + valid count per tile; reproject |
| 06 | `src/06_hls_mean_mosaic.py` | Mosaic per-tile means into a single GeoTIFF |
| 07 | `src/07_hls_outlier_mean_mosaic.py` | Mosaic outlier-filtered means |
| 08 | `src/08_hls_outlier_count_mosaic.py` | Mosaic valid-observation counts |
| 09 | `src/09_hls_count_valid_mosaic.py` | Count valid observations per pixel across all download cycles; mosaic into a single-band study-area-wide GeoTIFF |
| 10 | `src/10_hls_timeseries_mosaic.py` | Multi-band time-window stacks (seasonal composites) |
| 11 | `src/11_hls_outlier_gpkg.py` | Export per-pixel outlier observations (value, date, location) to a GeoPackage point vector file |

`hls_pipeline.sh` is the master orchestrator: it sources `config.env`, validates that required bands are configured for each requested VI before any step runs, then dispatches the appropriate scripts.

### Data Flow

```
NASA CMR API → 01 (raw L30/S30 + Fmask)
→ 02 (VI GeoTIFFs per granule, Fmask-masked)
→ 03 (per-tile CF-1.8 NetCDF time-series)
├── → 04 (mean tiles) → 06 (mean mosaic)
├── → 05 (outlier mean + count tiles) → 07 (outlier mean mosaic)
│                                     → 08 (outlier count mosaic)
│                                     → 11 (outlier GeoPackages, WGS84 points)
├── → 09 (CountValid mosaic across all download cycles)
└── → 10 (per-window mean + CountValid stacks, TIMESLICE_WINDOWS)
```

## Shared Utilities

**`src/hls_utils.py`** — shared utility module imported by all Python pipeline steps (02–11).

**Logging** (used by all steps):
- `setup_logging(step_name)` — configures the root logger (once, idempotent via `if not root.handlers` guard) with a `StreamHandler` writing to `sys.stdout`, and returns a named logger for the calling script. Format: `2026-03-18 20:55:49  INFO      [step_name]  message`. Called at module level in each pipeline script; safe for child processes spawned by `multiprocessing.Pool` or `ProcessPoolExecutor`.

**Tile filtering** (used by all steps):
- `get_configured_tiles()` — returns `set` of tile IDs from `HLS_TILES` env var, or empty set (no filter)
- `tile_id_from_path(filepath)` — extracts bare MGRS tile ID from any HLS filename (handles both dot-separated raw/VI GeoTIFF names and underscore-separated NetCDF/reprojected names)
- `filter_by_configured_tiles(filepaths)` — filters a file list to only those matching `HLS_TILES`; pass-through if `HLS_TILES` is unset

**VI valid ranges** (used by steps 04, 05, 09, 10, 11):
- `get_valid_range(vi_type)` — returns `(vmin, vmax)` from `VALID_RANGE_{VI}` env var; falls back to per-VI defaults and logs a warning if the variable is missing or unparseable

**CRS detection** (used by steps 04, 05, 09, 10):
- `detect_crs(ds, da)` — tries `da.rio.crs`, then `ds.attrs['crs']`, then per-variable `crs_wkt`/`spatial_ref` attributes; returns first match or `None`

**Reproject resolution** (used by steps 04, 05, 09, 10):
- `reproject_resolution(target_crs, meters=30.0)` — returns the resolution to pass to `rio.reproject()` in target CRS units; handles projected CRS (returns `meters` unchanged) and geographic CRS (converts to decimal degrees and logs a warning; geographic CRS is not recommended for pixel-level VI analysis)

Add future shared helpers here rather than duplicating across scripts.

## Key Patterns

**Logging**: All Python steps (02–11) call `setup_logging(step_name)` from `hls_utils.py` at module level and log via `logger.*()` (never `print()`). Format matches the VI_Phenology style: `YYYY-MM-DD HH:MM:SS  LEVEL     [step_name]  message`. The root logger handler guard (`if not root.handlers`) makes `setup_logging` idempotent — safe to call in worker child processes without producing duplicate output. Workers (steps 02–05, 09–10) never call `logger` directly; they return status strings or dicts to the main process, which performs all logging. In child processes with no configured handlers, Python's `lastResort` handler still emits WARNING+ to stderr (captured by `2>&1 | tee`). Shell helpers `log_info`, `log_warn`, and `log_error` in `hls_pipeline.sh` use the same timestamp + level + `[pipeline]` format so mixed shell/Python log output is visually consistent. Formatted table blocks (storage estimate, PIPELINE COMPLETE banner) are left as plain `echo` to preserve column alignment.

**Tile enforcement**: `HLS_TILES` in `config.env` is enforced at every processing step. Steps 02–11 call `filter_by_configured_tiles()` immediately after each glob so only configured tiles are processed. Step 01 (download) uses `HLS_TILES` natively via CMR API queries.

**Parallelism**: Step 02 uses `multiprocessing.Pool` with `mp.set_start_method('fork', force=True)`; steps 04, 05, 09, and 10 use `ProcessPoolExecutor`. Worker functions must be defined at module top level (required for pickling). Workers set `dask.config.set(scheduler='synchronous')` internally to prevent nested thread pools. Step 11 processes tiles sequentially (no parallel executor) using a time-chunked generator.

**Chunked spatial processing**: Steps 04, 05, 09, and 10 use xarray + dask (`CHUNK_SIZE` tiles) to avoid loading full rasters into memory. `xr.open_dataset(nc_path, chunks='auto')` for lazy loading; `.compute()` inside worker processes.

**Fmask masking**: Step 02 applies bitwise decode of the Fmask band. Bit layout:
- Bits 0–5: Cirrus, Cloud, Adjacent cloud, Shadow, Snow/ice, Water (one flag each)
- Bits 6–7: Aerosol level (0=None, 1=Low, 2=Moderate, 3=High) — `MASK_AEROSOL_MODE` selects threshold
- Value 255: Fill/NoData

**VI formulas** (HLS surface reflectance bands already scaled by `HLS_SCALE_FACTOR = 0.0001`):
- `NDVI = (nir - red) / (nir + red)`
- `EVI2 = 2.5 * (nir - red) / (nir + 2.4 * red + 1)`
- `NIRv = ndvi * nir`

All `np.errstate(divide='ignore', invalid='ignore')` is used to suppress divide-by-zero warnings; inf/nan values are carried through and filtered downstream by valid-range logic.

**Worker error handling**: Workers never raise to the main process. Steps 02, 04, and 05 return status strings (e.g., `"OK: ..."`, `"Skipped (Exists): ..."`, `"ERROR: ..."`); the main loop checks the returned string prefix. Steps 09 and 10 return dicts (`{'status': 'ok'|'skip'|'error', 'message': ..., ...}`); the main loop checks `result['status']`. In both patterns, if an output file already exists the worker returns a skip result and does no computation. Step 11 has no worker — `iter_tile_chunks` is a generator that yields fiona feature dicts per time-chunk; the main loop streams writes directly to fiona and catches exceptions per tile with `try/except`.

**Outlier handling**: "Outliers" are valid (unmasked) pixels outside per-VI min/max bounds (`np.isfinite(data) & ((data < vmin) | (data > vmax))`). Steps 05/07/08 produce raster summaries (mean + count); step 11 produces a point vector record for every individual outlier pixel-date observation, with coordinates reprojected to WGS84 (EPSG:4326) via `pyproj.Transformer`.

**Southern hemisphere CRS correction (step 03)**: HLS v2.0 GeoTIFFs for tiles south of
the equator embed a UTM North zone (EPSG:326xx) with negative northings instead of the
standard UTM South convention (EPSG:327xx, false_northing=10,000,000). Step 03
(`src/03_hls_netcdf_build.py`) detects this case in `HLSNetCDFAggregator.run()` after
reading the first GeoTIFF's CRS: if `to_epsg(min_confidence=20)` returns a UTM North
code (32601–32660) AND the pixel-center y mean is negative, the CRS is replaced with the
UTM South equivalent (EPSG + 100) and y-coordinates are shifted by +10,000,000 m. The
corrected values flow into every chunk dict and the merged output. After the fix, southern
hemisphere tiles carry EPSG:327xx CRS with positive northings (6–9 million m range),
matching the standard UTM South convention expected by GIS tools and CF-1.8 validators.

**NetCDF fill value consistency**: Both `process_netcdf_chunk` and `merge_chunks` in
`src/03_hls_netcdf_build.py` must create the VI variable with `fill_value=np.nan`.
If `fill_value` is omitted from either call, netCDF4 silently uses its default sentinel
(`9.969209968386869e+36`) and writes no `_FillValue` attribute — making the file
ambiguous to any downstream tool that doesn't apply an explicit valid-range filter.

**Temporal storage**: NetCDF files store dates as integer "days since 1970-01-01". Step 10 parses named time windows from `TIMESLICE_WINDOWS` to produce per-window multi-band mosaics with window labels stored in band descriptions.

**Streaming mosaics** (steps 06, 07, 08, 09): Use `rasterio.merge.merge()` for memory-efficient tiling — peak RAM is one tile + output buffer, not all tiles simultaneously.

**Streaming GeoPackage writes** (step 11): `iter_tile_chunks` loads `TIME_CHUNK` (10) time slices at a time from the NetCDF, yields fiona feature dicts for any outliers found, and frees the chunk immediately. The main loop writes each batch directly to the open fiona dataset — no cross-tile accumulation in memory. Uses `fiona` directly (not `geopandas`/`shapely`) to avoid loading all features into a GeoDataFrame before writing.

**Band requirements**: `hls_pipeline.sh` contains a pre-flight validation block that checks that all bands needed for each requested VI are present in the L30 and S30 band lists before executing any step.

**Tile-by-tile orchestration** (steps 01–03): Steps 01–03 always run inside a tile loop in `hls_pipeline.sh`. The orchestrator temporarily exports `HLS_TILES=<single_tile>` before calling steps 02 and 03 (the Python scripts pick this up via `os.environ.get()` at call time — no script changes needed). Step 01 calls `src/01_hls_download_query.sh` in `HLS_MODE=batch` with a single-tile temp file. When `download` is active, a pre-flight estimate and approval prompt (bypassed by `SKIP_APPROVAL=TRUE`) run before the tile loop. The estimate covers all active steps: steps 01–03 costs scale by granule count (with a 1.5x per-tile coverage factor for conservative peak), and steps 04–11 costs scale by tile count, VI count, and (for step 10) window count. Space-saver deletion flags (`SPACE_SAVER_REMOVE_RAW`, `SPACE_SAVER_REMOVE_VI`) only fire per tile when the tile succeeded AND `netcdf` is active in `STEPS`; if `netcdf` is not active, no deletion fires and the estimate reflects full accumulation. `set +e` wraps the tile loop so failed tiles are skipped and logged in `TBT_FAILED_TILES`; `set -e` is restored before steps 04–11. `HLS_TILES` is restored to the full list after the loop.

## Filename Conventions

| Product | Pattern |
|---------|---------|
| Raw HLS band | `HLS.{L30\|S30}.T{TILE}.{YYYYDDD}T{HHMMSS}.v2.0.{BAND}.tif` |
| VI GeoTIFF | `HLS.{L30\|S30}.T{TILE}.{YYYYDDD}T{HHMMSS}.v2.0.{VI}.tif` |
| NetCDF time-series | `T{TILE}_{VI}.nc` |
| Reprojected mean tile | `T{TILE}_{VI}_average_{VI}_{safe_crs}.tif` |
| Outlier mean tile | `T{TILE}_{VI}_outlier_mean_{VI}_{safe_crs}.tif` |
| Outlier count tile | `T{TILE}_{VI}_outlier_count_{VI}_{safe_crs}.tif` |
| Mean mosaic | `HLS_Mosaic_{VI}_{safe_crs}.tif` |
| Outlier mean mosaic | `HLS_Mosaic_Outlier_Mean_{VI}_{safe_crs}.tif` |
| Outlier count mosaic | `HLS_Mosaic_Outlier_Count_{VI}_{safe_crs}.tif` |
| Time-series mean stack | `HLS_TimeSeries_{VI}_Mean_{safe_crs}.tif` |
| Time-series count stack | `HLS_TimeSeries_{VI}_CountValid_{safe_crs}.tif` |
| CountValid mosaic | `HLS_Mosaic_CountValid_{VI}_{safe_crs}.tif` |
| Outlier GeoPackage | `HLS_outliers_{VI}.gpkg` |

`safe_crs` = `TARGET_CRS.replace(':', '')` (e.g., `EPSG6350`).

## Output Data Types

| Product | Dtype | Nodata | LZW Predictor |
|---------|-------|--------|---------------|
| VI GeoTIFF | float32 | NaN | — |
| Mean / outlier mean tile | float32 | NaN | 3 (float differencing) |
| Outlier count tile | uint16 | 0 | 2 (int differencing) |
| Time-series mean band | float32 | NaN | 2 |
| Time-series count band | uint16 | 0 | 2 |
| CountValid mosaic | uint16 | 0 | 2 (int differencing) |
| NetCDF VI data | float32 | NaN | zlib complevel=1 |

All GeoTIFFs are tiled (512×512 blocks) with LZW compression.
