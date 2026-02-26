# Changelog

All notable changes to this project are documented here.

---

## 2026-02-26

### Added
- Read the Docs configuration and Sphinx documentation scaffold (`docs/`)
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
