# Configuration Reference

All pipeline parameters are defined in `config.env` at the repository root.
`hls_pipeline.sh` sources this file before dispatching each step; Python scripts
read values via `os.environ.get()` with per-parameter fallback defaults.

---

## Paths

| Parameter | Default | Description |
|-----------|---------|-------------|
| `BASE_DIR` | *(required)* | Root directory for all pipeline inputs and outputs |
| `LOG_DIR` | `${BASE_DIR}/0_Logs` | Directory for pipeline log files |

---

## Output Directories

All paths are relative to `BASE_DIR` by default.

| Parameter | Default path | Description |
|-----------|-------------|-------------|
| `RAW_HLS_DIR` | `${BASE_DIR}/1_Raw` | Downloaded raw HLS band and Fmask GeoTIFFs (step 01 output) |
| `VI_OUTPUT_DIR` | `${BASE_DIR}/2_Interim/1_VI_Products` | Per-granule VI GeoTIFFs (step 02 output) |
| `NETCDF_DIR` | `${BASE_DIR}/2_Interim/2_NetCDF` | Per-tile NetCDF time-series files (step 03 output) |
| `REPROJECTED_DIR` | `${BASE_DIR}/2_Interim/3_VI_Mean_Tiles` | Reprojected temporal mean tiles (step 04 output) |
| `REPROJECTED_DIR_OUTLIERS` | `${BASE_DIR}/2_Interim/4_VI_Outlier_Tiles` | Reprojected outlier mean + count tiles (step 05 output) |
| `MOSAIC_DIR` | `${BASE_DIR}/3_Out/1_Mosaic` | Study-area-wide mosaic GeoTIFFs (steps 06–09 output) |
| `TIMESLICE_OUTPUT_DIR` | `${BASE_DIR}/3_Out/2_TimeSeries` | Multi-band time-window stacks (step 10 output) |
| `OUTLIER_GPKG_DIR` | `${BASE_DIR}/3_Out/3_Outlier_Points` | Outlier point GeoPackage files (step 11 output) |

---

## Processing Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `NUM_WORKERS` | `8` | Parallel worker processes for compute-intensive steps (02, 04, 05, 09, 10, 11) |
| `CHUNK_SIZE` | `10` | Tiles loaded per dask chunk during xarray processing (steps 04, 05, 09, 10) |
| `TARGET_CRS` | `EPSG:6350` | Output CRS for all reprojected and mosaicked products (steps 04–11). `EPSG:6350` is NAD83 Conus Albers at 30 m resolution |

---

## Vegetation Indices

```bash
PROCESSED_VIS="NDVI EVI2 NIRv"
```

Space-separated list of vegetation indices to process end-to-end. All listed VIs
flow through every active pipeline step.

| Value | Formula | Typical range | Band requirements |
|-------|---------|--------------|------------------|
| `NDVI` | `(NIR − Red) / (NIR + Red)` | −1.0 to 1.0 | B05/B8A, B04, Fmask |
| `EVI2` | `2.5 × (NIR − Red) / (NIR + 2.4×Red + 1)` | −1.0 to 2.0 | B05/B8A, B04, Fmask |
| `NIRv` | `NDVI × NIR` | −0.5 to 1.0 | B05/B8A, B04, Fmask |

---

## Pipeline Step Control

```bash
STEPS="all"
```

Controls which pipeline stages run. Accepts named steps (space-separated, any
order, any combination) or convenience aliases.

### Named steps

| Value | Step | Script | Description |
|-------|------|--------|-------------|
| `download` | 01 | `01_hls_download_query.sh` | Query NASA CMR API; download raw HLS bands and Fmask |
| `vi_calc` | 02 | `02_hls_vi_calc.py` | Compute VI GeoTIFFs from raw bands; apply Fmask masking |
| `netcdf` | 03 | `03_hls_netcdf_build.py` | Aggregate per-granule VI GeoTIFFs into per-tile CF-1.8 NetCDF time-series |
| `mean_flat` | 04 | `04_hls_mean_reproject.py` | Temporal mean per tile; reproject to `TARGET_CRS` |
| `outlier_flat` | 05 | `05_hls_outlier_reproject.py` | Outlier-aware mean + valid count per tile; reproject |
| `mean_mosaic` | 06 | `06_hls_mean_mosaic.py` | Mosaic per-tile means into a single study-area-wide GeoTIFF |
| `outlier_mosaic` | 07 | `07_hls_outlier_mean_mosaic.py` | Mosaic outlier-filtered mean tiles |
| `outlier_counts` | 08 | `08_hls_outlier_count_mosaic.py` | Mosaic outlier pixel count tiles |
| `count_valid_mosaic` | 09 | `09_hls_count_valid_mosaic.py` | Count valid observations per pixel across all download cycles; mosaic result |
| `timeseries` | 10 | `10_hls_timeseries_mosaic.py` | Multi-band time-window stacks defined by `TIMESLICE_WINDOWS` |
| `outlier_gpkg` | 11 | `11_hls_outlier_gpkg.py` | Export per-pixel outlier observations to a GeoPackage point vector file |

### Convenience aliases

| Alias | Expands to | Use case |
|-------|-----------|---------|
| `all` | Steps 01–11 | Full pipeline from scratch |
| `products` | Steps 02–11 | Raw data already downloaded |
| `build_nc` | Steps 01–03 | Download through NetCDF only |
| `mosaics` | Steps 06–08 | Re-mosaic only (tiles already reprojected) |
| `outliers` | Steps 05+07+08+11 | Re-run the full outlier chain |

### Examples

```bash
STEPS="all"                                          # Full pipeline from scratch
STEPS="products"                                     # Raw data exists, build everything
STEPS="build_nc"                                     # Download through NetCDF only
STEPS="timeseries"                                   # Re-run only the time-series step
STEPS="mosaics"                                      # Re-mosaic after fixing a tile
STEPS="outliers"                                     # Re-run full outlier chain
STEPS="outlier_gpkg"                                 # Export outlier points only
STEPS="count_valid_mosaic"                           # CountValid mosaic only
STEPS="netcdf mean_flat mean_mosaic"                 # NetCDF through mean mosaic only
STEPS="mean_flat outlier_flat mosaics timeseries"    # Add a new VI (NetCDFs exist)
```

---

## Space Saver Options

These options only fire per tile when **step 03 (`netcdf`) is active** in the
current run. Both flags are safe to enable together.

| Parameter | Values | Default | Description |
|-----------|--------|---------|-------------|
| `SPACE_SAVER_REMOVE_RAW` | `TRUE` / `FALSE` | `FALSE` | Delete downloaded HLS band + Fmask files from `RAW_HLS_DIR` after each tile's NetCDF is built |
| `SPACE_SAVER_REMOVE_VI` | `TRUE` / `FALSE` | `FALSE` | Delete per-granule VI GeoTIFFs from `VI_OUTPUT_DIR` after each tile's NetCDF is built |

---

## Download Approval

Before any data is downloaded, the pipeline prints a storage estimate and prompts for confirmation. To bypass this prompt in automated or non-interactive contexts:

| Parameter | Values | Default | Description |
|-----------|--------|---------|-------------|
| `SKIP_APPROVAL` | `TRUE` / `FALSE` | `FALSE` | Bypass the interactive download approval prompt. Set `TRUE` for automated or non-interactive runs |

---

## Download Settings

| Parameter | Default | Description |
|-----------|---------|-------------|
| `CLOUD_COVERAGE_MAX` | `75` | Maximum cloud coverage percentage for CMR API granule filtering (0–100) |
| `SPATIAL_COVERAGE_MIN` | `0` | Minimum spatial coverage percentage for CMR API granule filtering (0–100) |

---

## Band Selection

Defines which bands to download for each HLS sensor. `Fmask` is always required
for quality masking.

| Parameter | Default | Description |
|-----------|---------|-------------|
| `L30_BANDS` | `B05 B04 Fmask` | Landsat bands: NIR (B05), Red (B04), quality mask |
| `S30_BANDS` | `B8A B04 Fmask` | Sentinel-2 bands: NIR narrow (B8A), Red (B04), quality mask |

**Full band reference:**

| Sensor | Band | Wavelength | Role |
|--------|------|-----------|------|
| L30 | B04 | Red | Required by NDVI, EVI2, NIRv |
| L30 | B05 | NIR | Required by NDVI, EVI2, NIRv |
| L30 | B02 | Blue | Only needed for 3-band EVI (not currently used) |
| S30 | B04 | Red | Required by NDVI, EVI2, NIRv |
| S30 | B8A | NIR narrow | Required by NDVI, EVI2, NIRv |
| S30 | B02 | Blue | Only needed for 3-band EVI (not currently used) |

The pipeline validates at startup that all bands required for the selected
`PROCESSED_VIS` are present in these lists before any step executes.

---

## Fmask Quality Masking

Individual bit flags for the HLS Fmask quality band. Set `TRUE` to mask
(exclude) pixels with the corresponding condition.

| Parameter | Fmask bit | Default | Description |
|-----------|----------|---------|-------------|
| `MASK_CIRRUS` | Bit 0 | `TRUE` | Mask cirrus cloud pixels |
| `MASK_CLOUD` | Bit 1 | `TRUE` | Mask cloud pixels |
| `MASK_ADJACENT_CLOUD` | Bit 2 | `TRUE` | Mask pixels adjacent to cloud |
| `MASK_CLOUD_SHADOW` | Bit 3 | `TRUE` | Mask cloud shadow pixels |
| `MASK_SNOW_ICE` | Bit 4 | `TRUE` | Mask snow and ice pixels |
| `MASK_WATER` | Bit 5 | `TRUE` | Mask open water pixels |
| `MASK_AEROSOL_MODE` | Bits 6–7 | `MODERATE` | Aerosol masking threshold (see below) |

**Aerosol modes:**

| Mode | Behavior |
|------|---------|
| `HIGH` | Mask only high-aerosol pixels (general use) |
| `MODERATE` | Mask high + moderate aerosol **(recommended for VIs)** |
| `LOW` | Mask all non-zero aerosol pixels |
| `NONE` | No aerosol masking |

:::{note}
`HLS_SCALE_FACTOR=0.0001` is the HLS surface reflectance scale factor applied
during VI calculation (step 02). This value reflects the NASA HLS v2.0 data
specification and should not be changed.
:::

---

## Valid Range Bounds

Pixels outside these bounds are treated as outliers in steps 05, 07, 08, 09,
10, and 11. Format: `"min,max"` (no spaces).

| Parameter | Default | Scientific basis |
|-----------|---------|-----------------|
| `VALID_RANGE_NDVI` | `"-1,1"` | Bounded by definition — ratio of two bands of equal magnitude at the extremes |
| `VALID_RANGE_EVI2` | `"-1,2"` | Captures all physically plausible values while rejecting noise; EVI2 can exceed 1.0 over bright or noisy surfaces |
| `VALID_RANGE_NIRv` | `"-0.5,1"` | Rejects implausible negative values while preserving all legitimate high-vegetation values (dense tropical canopy ~0.5–0.6) |

Adjust these thresholds if your study region has atypical surface conditions
(e.g., snow/ice, salt flats, open water).

---

## Tile List

```bash
HLS_TILES="17TNE 17TNF 17TPE"
```

Space-separated list of MGRS tile IDs to process. Enforced uniformly across all
11 pipeline steps — step 01 uses it for CMR API queries; steps 02–11 filter all
file globs against it immediately after each glob call.

If `HLS_TILES` is unset or empty, no tile filtering is applied and all
discovered files are processed.

---

## Download Cycles

```bash
DOWNLOAD_CYCLES="2020-01-01|2020-12-31 2021-01-01|2021-12-31"
```

Space-separated list of date ranges in `YYYY-MM-DD|YYYY-MM-DD` format. Step 01
queries and downloads each range as a separate cycle. Multiple cycles allow
non-contiguous time periods (e.g., winter-only seasons across multiple years).

---

## Time-Series Windows

Controls step 10 (`timeseries`), which produces multi-band composite stacks
where each band represents one named time window.

| Parameter | Values | Default | Description |
|-----------|--------|---------|-------------|
| `TIMESLICE_ENABLED` | `TRUE` / `FALSE` | `FALSE` | Must be `TRUE` for step 10 to produce output |
| `TIMESLICE_STAT` | `mean` | `mean` | Statistic computed per pixel per window |

```bash
TIMESLICE_WINDOWS="label:YYYY-MM-DD|YYYY-MM-DD ..."
```

Space-separated list of named date windows. Each token is `label:start|end` where:

- **label** — alphanumeric + underscores only; becomes the band description in the output stack
- **start / end** — inclusive date bounds (`YYYY-MM-DD`); start must be ≤ end

**Examples:**

```bash
# Wet / dry seasons
TIMESLICE_WINDOWS="wet_2020:2020-11-01|2021-04-30 dry_2021:2021-05-01|2021-10-31"

# Monthly slices (outlier forensics)
TIMESLICE_WINDOWS="jan_2021:2021-01-01|2021-01-31 feb_2021:2021-02-01|2021-02-28"

# Annual composites
TIMESLICE_WINDOWS="yr_2016:2016-01-01|2016-12-31 yr_2017:2017-01-01|2017-12-31"
```
