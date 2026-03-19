# Changelog

All notable changes to this project are documented here.

---

## 2026-03-18 (3)

### Added
- **Structured logging across all Python steps and `hls_pipeline.sh`** — all ten
  Python pipeline scripts (steps 02–11) now use Python's `logging` module via a
  shared `setup_logging(step_name)` helper in `src/hls_utils.py`. Every log line
  carries a timestamp, level, and bracketed step label:
  `2026-03-18 20:55:49  INFO      [04_mean_reproject]  message`. Previously all
  diagnostic output used bare `print()` calls with no timestamps or severity levels.
  Key design points:
  - Single implementation in `hls_utils.py`; no logging boilerplate duplicated
    across scripts.
  - Root logger handler guard (`if not root.handlers`) makes `setup_logging`
    idempotent — calling it in `multiprocessing.Pool` or `ProcessPoolExecutor`
    child processes does not produce duplicate output.
  - Worker functions (steps 02–05, 09–10) are unchanged; they return status
    strings/dicts to the main process, which performs all `logger.*()` calls.
  - `StreamHandler` targets `sys.stdout` so `2>&1 | tee -a "$LOGFILE"` in the
    shell captures all output.
  - `hls_pipeline.sh` gains `log_info`, `log_warn`, and `log_error` helper
    functions that emit the same timestamp + level + `[pipeline]` format, making
    combined shell/Python log output visually consistent.

---

## 2026-03-18 (2)

### Fixed
- **Step 03 — southern hemisphere CRS stored as UTM North** — HLS v2.0 GeoTIFFs
  for tiles south of the equator embed a UTM North zone (EPSG:326xx) with negative
  northings instead of the standard UTM South convention (EPSG:327xx,
  false_northing=10,000,000). All southern Africa (BioSCape) and other southern
  hemisphere tiles were affected. `HLSNetCDFAggregator.run()` in
  `src/03_hls_netcdf_build.py` now detects this case after reading the first
  GeoTIFF: if `pyproj.to_epsg(min_confidence=20)` returns a UTM North code
  (32601–32660) and the pixel-center y mean is negative, the CRS WKT is replaced
  with the UTM South equivalent (EPSG + 100, e.g. 32634 → 32734) and
  y-coordinates are shifted by +10,000,000 m. This correction is applied before
  chunk dicts are built, so both single-chunk and merged tiles are written with
  the correct EPSG:327xx CRS and positive UTM South northings. Previously rebuilt
  tiles will need to be regenerated with step 03 to pick up the corrected CRS and
  coordinates; downstream steps 04–11 that reproject to `TARGET_CRS` are not
  affected because they perform a full reprojection from the source CRS.

---

## 2026-03-18

### Fixed
- **Step 03 — `_FillValue` lost in `merge_chunks`** — `process_netcdf_chunk` correctly
  creates the VI variable with `fill_value=np.nan`, but `merge_chunks` recreated the
  same variable without a `fill_value` argument. netCDF4 therefore fell back to its
  built-in default sentinel (`9.969209968386869e+36`) for all missing cells in merged
  files, and the `_FillValue` attribute was absent from the output. Any tile requiring
  chunk merging (virtually all multi-year tiles with more acquisitions than `CHUNK_SIZE`)
  was affected. Fixed by adding `fill_value=np.nan` to the `createVariable` call in
  `merge_chunks` (`src/03_hls_netcdf_build.py` line 231). Newly rebuilt tiles will store
  missing data as `NaN` and carry a proper `_FillValue = NaN` attribute.

---

## 2026-03-12

### Changed
- **Pipeline scripts moved to `src/`** — all 11 step scripts
  (`01_hls_download_query.sh` – `11_hls_outlier_gpkg.py`) and `hls_utils.py`
  relocated from the repository root into `src/`. `hls_pipeline.sh` remains at
  the root. All invocation paths in `hls_pipeline.sh`, `CLAUDE.md`, `README.md`,
  and `docs/` updated accordingly. Python `import hls_utils` statements are
  unaffected (Python resolves the import from the script's own directory).
- **Step 03 — improved CF-1.8 CRS metadata in NetCDF output** — the
  `spatial_ref` grid-mapping variable now carries both `crs_wkt` (CF-1.8
  standard) and `spatial_ref` (GDAL / rioxarray compatibility) attributes, plus
  `grid_mapping_name` (derived via pyproj) and `long_name`. The `x`/`y`
  coordinate variables now include `standard_name`, `long_name`, and `axis`
  attributes; the `time` variable now includes `standard_name`, `calendar`, and
  `axis`. A global `Conventions = "CF-1.8"` attribute is now written. The
  `merge_chunks` path mirrors all the same attributes. These changes make
  `da.rio.crs` (rioxarray path 1 in `detect_crs()`) reliably resolve without
  falling back to the global `crs` attribute. Existing NetCDF files built with
  the prior format remain readable via the `detect_crs()` fallback chain.
- **Step 03 — CRS WKT stored as pyproj WKT2 instead of GDAL WKT1** —
  `HLSNetCDFAggregator.run()` now generates the CRS WKT string via
  `ProjCRS.from_user_input(crs).to_wkt()` (pyproj WKT2) instead of
  rasterio's `crs.to_wkt()` (GDAL WKT1). GDAL WKT1 for some HLS tiles
  lacks a top-level `AUTHORITY["EPSG","XXXXX"]` node, causing
  `pyproj.CRS.from_wkt(wkt).to_epsg()` to return `None`. Downstream
  consumers that group tiles by EPSG code (e.g. cross-CRS reprojection
  checks) would treat same-zone tiles as different CRS groups. The pyproj
  WKT2 output always includes a resolvable authority node. Existing NetCDF
  files retain their original WKT; rebuilding with step 03 is recommended
  for tiles where EPSG grouping matters downstream.

---

## 2026-02-28

### Added
- **`NETCDF_COMPLEVEL`** — configurable zlib compression level (0–9, default `1`)
  for NetCDF time-series files written by step 03. Threaded through
  `HLSNetCDFAggregator` into `chunk_info` dicts (worker) and `merge_chunks`.
- **`GEOTIFF_COMPRESS`** — configurable compression codec (default `LZW`) for all
  GeoTIFF outputs in steps 02 and 04–10. Accepts any codec supported by the
  local GDAL build (`LZW`, `DEFLATE`, `ZSTD`, `NONE`).
- **`GEOTIFF_BLOCK_SIZE`** — configurable internal tile block dimension in pixels
  (default `512`) for all tiled GeoTIFF outputs in steps 04–10. `512` is
  standard for desktop GIS; `256` is preferred for Cloud-Optimized GeoTIFFs.
- **`reproject_resolution()` in `hls_utils.py`** — CRS-unit-aware resolution
  helper replacing all hardcoded `resolution=30` calls in steps 04, 05, 09, 10.
  Returns metres unchanged for projected CRS; converts to approximate degrees
  for geographic CRS and logs a warning.

### Fixed
- Steps 04, 05, 09, and 10 produced a 1×1 pixel output with no valid data when
  `TARGET_CRS` was set to a geographic CRS (e.g. `EPSG:4148`) because
  `resolution=30` was interpreted as 30 degrees per pixel instead of 30 metres.

---

## 2026-02-26

### Added
- Read the Docs configuration and Sphinx documentation scaffold (`docs/`)
- `docs/overview.md`: comprehensive pipeline guide (full user documentation)

### Changed
- README.md restructured as a GitHub landing page (elevator pitch, outputs
  table, key features, quick start, and link to RTD); full documentation
  moved to `docs/overview.md`
- `docs/index.md` updated to a hub toctree (overview, configuration,
  changelog); no longer uses `{include}` to pull README content

### Fixed
- System requirements table in README: added `gdalinfo` (called directly by
  step 01 for GeoTIFF validation; provided by the conda environment via
  rasterio's GDAL dependency); clarified that conda is required not just for
  Python packages but because it supplies native geospatial libraries (GDAL,
  PROJ, HDF5, GEOS)
- Per-file download validation with retry logic in step 01

### Changed
- `NUM_WORKERS` restored to `8` in `config.env`

### Removed
- Bulk download mode retired; tile-by-tile is now the only download mode,
  reducing peak disk usage to roughly one tile's worth of raw data at a time

---

## 2026-02-25

### Added
- **Step 09 — CountValid mosaic**: counts valid (unmasked, in-range) observations
  per pixel across all download cycles and mosaics the result into a single
  study-area-wide GeoTIFF. Reads from NetCDF files (step 03); independent of
  `TIMESLICE_WINDOWS` and the time-series step.

### Changed
- Steps renumbered to reflect execution order:
  - Former step 09 (time-series) → **Step 10**
  - Former step 10 (outlier GeoPackage) → **Step 11**

---

## 2026-02-22

### Added
- Initial release of the HLS Vegetation Index Pipeline
- 11-step end-to-end workflow: download → VI calculation → NetCDF →
  reprojection → mosaics → time-series → outlier export
- Support for NDVI, EVI2, and NIRv vegetation indices
- Bitwise Fmask quality masking with independently configurable flags for
  cirrus, cloud, adjacent cloud, shadow, snow/ice, water, and aerosol mode
- Tile-by-tile orchestration for steps 01–03, with optional space-saver
  flags to remove raw and/or VI intermediate files after each tile's NetCDF
  is built
- Configurable parallel processing via `NUM_WORKERS`
- Per-VI valid range outlier detection with configurable bounds
  (`VALID_RANGE_NDVI`, `VALID_RANGE_EVI2`, `VALID_RANGE_NIRv`)
- Multi-band seasonal composite stacks via `TIMESLICE_WINDOWS` (step 10)
- GeoPackage export of per-pixel outlier observations with WGS84 coordinates
  (step 11)
- Pre-flight band validation: the orchestrator checks that all bands required
  for the selected VIs are configured before any step executes
- `SKIP_APPROVAL` flag for automated / non-interactive pipeline runs
