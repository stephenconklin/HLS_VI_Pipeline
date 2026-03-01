# Changelog

All notable changes to this project are documented here.

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
  for geographic CRS and prints a `[WARN]`.

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
