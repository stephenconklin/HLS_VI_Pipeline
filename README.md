# HLS Vegetation Index Pipeline

[![Python](https://img.shields.io/badge/python-3.10--3.12-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Platform](https://img.shields.io/badge/platform-linux%20%7C%20macOS-lightgrey.svg)]()
[![Data: HLS v2.0](https://img.shields.io/badge/data-HLS%20v2.0-brightgreen.svg)](https://hls.gsfc.nasa.gov/)

A production-ready, 10-step processing pipeline for computing and analyzing vegetation indices from NASA's Harmonized Landsat and Sentinel-2 (HLS) surface reflectance data. Designed for researchers across ecology, agriculture, land management, and remote sensing who need reproducible, time-series vegetation analysis at scale.

---

## Table of Contents

- [Overview](#overview)
- [Key Features](#key-features)
- [Pipeline Architecture](#pipeline-architecture)
- [Prerequisites](#prerequisites)
- [Installation](#installation)
- [Configuration](#configuration)
- [Quick Start](#quick-start)
- [Running the Pipeline](#running-the-pipeline)
- [Step Reference](#step-reference)
- [Output Products](#output-products)
- [Advanced Usage](#advanced-usage)
- [Troubleshooting](#troubleshooting)
- [Credits & Acknowledgments](#credits--acknowledgments)
- [License](#license)

---

## Overview

The **HLS Vegetation Index Pipeline** automates the full workflow from raw HLS satellite imagery to analysis-ready vegetation index products.

**Input:** NASA HLS v2.0 granules (Landsat-8/9 L30 and Sentinel-2 S30) queried directly from the NASA Common Metadata Repository (CMR) API.

**Output:**
- Cloud-masked, quality-filtered vegetation index GeoTIFFs per granule
- Per-tile CF-1.8 compliant NetCDF time-series stacks with sensor metadata
- Temporal mean raster mosaics reprojected to a user-specified CRS
- Outlier-flagged pixel summaries (raster mean + count) and point-vector GeoPackages
- Multi-band seasonal composite stacks with user-defined, named time windows

**Supported Vegetation Indices:**

| Index | Formula | Typical Range |
|-------|---------|---------------|
| NDVI | `(NIR − Red) / (NIR + Red)` | −1.0 to 1.0 |
| EVI2 | `2.5 × (NIR − Red) / (NIR + 2.4×Red + 1)` | −1.0 to 2.0 |
| NIRv | `NDVI × NIR` | −0.5 to 1.0 |

**Data Source:** [NASA Harmonized Landsat and Sentinel-2 (HLS)](https://hls.gsfc.nasa.gov/) — 30 m spatial resolution, ~2–3 day combined revisit time.

---

## Key Features

- **End-to-end automation** — a single `bash hls_pipeline.sh` command runs all 10 steps sequentially
- **Flexible step control** — run the full pipeline or any subset using named step identifiers or built-in aliases
- **Quality masking** — bitwise Fmask decode with independently configurable cloud, cloud shadow, snow/ice, water, and aerosol modes
- **Outlier detection** — identifies and exports pixels outside per-VI valid ranges as raster summaries and searchable point-vector GeoPackages
- **Seasonal composites** — user-defined, named time windows produce multi-band stacks for phenological or climatological analysis, with window labels embedded in band metadata
- **Parallel processing** — multiprocessing across configurable worker counts for all compute-intensive steps
- **Memory-efficient** — dask-chunked xarray processing and streaming rasterio mosaic merges scale to large study extents without out-of-memory failures
- **Consistent tile filtering** — `HLS_TILES` enforces a fixed MGRS tile set uniformly across all 10 steps
- **Cloud-optimized output** — all GeoTIFF outputs use LZW compression, internal tiling, and predictor settings appropriate to their data type
- **Pre-flight validation** — the pipeline validates that all bands required for the selected VIs are configured before any step executes

---

## Pipeline Architecture

```
NASA CMR API
     │
     ▼
┌─────────────────────────────────────────────────────────────────────┐
│  Step 01 · download                                                 │
│  01_hls_download.sh + 01a_hls_download_query.sh                     │
│  Query CMR API, estimate storage, download raw L30/S30 bands        │
└──────────────────────────────┬──────────────────────────────────────┘
                               │  Raw GeoTIFF bands (B04, B05/B8A, Fmask)
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│  Step 02 · vi_calc                                                  │
│  02_hls_vi_calc.py                                                  │
│  Apply Fmask quality masking; compute NDVI / EVI2 / NIRv            │
└──────────────────────────────┬──────────────────────────────────────┘
                               │  Per-granule VI GeoTIFFs
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│  Step 03 · netcdf                                                   │
│  03_hls_netcdf_build.py                                             │
│  Aggregate GeoTIFFs → CF-1.8 NetCDF time-series per tile            │
└──────────────────────────────┬──────────────────────────────────────┘
                               │  Per-tile NetCDF time-series
                    ┌──────────┴──────────┐
                    ▼                     ▼
        ┌───────────────────┐   ┌──────────────────────┐
        │  Steps 04 + 06    │   │  Steps 05 + 07 + 08   │
        │  Mean products    │   │  Outlier products     │
        └────────┬──────────┘   └──────────┬───────────┘
                 │                         │
                 ▼                         ├──────────────────────┐
        ┌────────────────┐                 ▼                      ▼
        │  Mean mosaic   │        ┌─────────────────┐   ┌──────────────────┐
        └────────┬───────┘        │  Outlier rasters│   │  Step 10 · GPKG  │
                 │                └─────────────────┘   └──────────────────┘
                 ▼
        ┌────────────────┐
        │  Step 09       │
        │  Time-series   │
        │  stacks        │
        └────────────────┘
```

### Step Summary

| Step | Script | Step Name | Description |
|------|--------|-----------|-------------|
| 01 | `01_hls_download.sh` | `download` | Query NASA CMR API; download raw HLS granules (L30/S30 bands + Fmask) |
| 02 | `02_hls_vi_calc.py` | `vi_calc` | Compute VI GeoTIFFs; apply configurable bitwise Fmask quality masking |
| 03 | `03_hls_netcdf_build.py` | `netcdf` | Aggregate per-granule GeoTIFFs into CF-1.8 compliant NetCDF time-series per tile |
| 04 | `04_hls_mean_reproject.py` | `mean_flat` | Temporal mean per tile; reproject to `TARGET_CRS` |
| 05 | `05_hls_outlier_reproject.py` | `outlier_flat` | Outlier-aware mean + valid count per tile; reproject |
| 06 | `06_hls_mean_mosaic.py` | `mean_mosaic` | Mosaic per-tile means into a study-area-wide GeoTIFF |
| 07 | `07_hls_outlier_mean_mosaic.py` | `outlier_mosaic` | Mosaic outlier-filtered means |
| 08 | `08_hls_outlier_count_mosaic.py` | `outlier_counts` | Mosaic valid-observation counts |
| 09 | `09_hls_timeseries_mosaic.py` | `timeseries` | Multi-band seasonal composite stacks with user-defined time windows |
| 10 | `10_hls_outlier_gpkg.py` | `outlier_gpkg` | Export per-pixel outlier observations (value, date, location) to GeoPackage |

---

## Prerequisites

### 1. NASA Earthdata Account

HLS data is hosted on NASA Earthdata. You must:

1. Register for a free account at [https://urs.earthdata.nasa.gov/](https://urs.earthdata.nasa.gov/)
2. Accept the required End User License Agreements (EULAs) for HLS data products on the Earthdata website
3. Create a `~/.netrc` file with your credentials:

```
machine urs.earthdata.nasa.gov login <your_username> password <your_password>
```

4. Secure the file:

```bash
chmod 600 ~/.netrc
```

### 2. System Requirements

| Tool | Minimum Version | Notes |
|------|----------------|-------|
| conda or mamba | any | For environment management |
| bash | 3.2+ | Uses standard POSIX-compatible syntax; tested on macOS (ZSH) and Linux |
| wget or curl | any | Used by download scripts |

### 3. MGRS Tile Identification

HLS data is organized by [MGRS (Military Grid Reference System)](https://hls.gsfc.nasa.gov/products-description/tiling-system/) tiles. You need the MGRS tile IDs covering your area of interest before configuring the pipeline. Useful tools:

- [NASA Earthdata Search](https://search.earthdata.nasa.gov/) — interactive map with MGRS tile overlays
- [MGRS Mapper](https://mgrs-mapper.com/) — web-based tile lookup by location
- QGIS with the HLS tiling shapefile

### 4. Storage Estimate

The download script provides an interactive storage estimate before any data are downloaded. As a rough guide for planning:

| Product | Approximate size per granule |
|---------|------------------------------|
| Raw bands (per granule) | ~60 MB |
| VI GeoTIFF (per VI) | ~54 MB |
| NetCDF (per VI) | ~54 MB |

A 10-tile study area with 5 years of data (bi-weekly acquisitions) can require **100–300+ GB** of storage depending on the number of VIs computed.

---

## Installation

### 1. Clone the Repository

```bash
git clone https://github.com/stephenconklin/HLS_VI_Pipeline.git
cd HLS_VI_Pipeline
```

### 2. Create the Conda Environment

```bash
conda env create -f environment.yml
conda activate hls_pipeline
```

> **Tip:** [Mamba](https://mamba.readthedocs.io/) resolves conda environments significantly faster than conda:
>
> ```bash
> mamba env create -f environment.yml
> ```

### 3. Verify the Installation

```bash
python -c "import rasterio, xarray, rioxarray, geopandas; print('Environment OK')"
```

---

## Configuration

All pipeline parameters are set in `config.env`. This file is sourced by `hls_pipeline.sh` before each step — no environment variables need to be exported manually.

### Paths

```bash
BASE_DIR="/path/to/your/project"            # Root output directory — edit this first
LOG_DIR="${BASE_DIR}/0_Logs"
RAW_HLS_DIR="${BASE_DIR}/1_Raw"             # Downloaded HLS granules
VI_OUTPUT_DIR="${BASE_DIR}/2_Interim/1_VI_Products"
NETCDF_DIR="${BASE_DIR}/2_Interim/2_NetCDF"
REPROJECTED_DIR="${BASE_DIR}/2_Interim/3_VI_Mean_Tiles"
REPROJECTED_DIR_OUTLIERS="${BASE_DIR}/2_Interim/4_VI_Outlier_Tiles"
MOSAIC_DIR="${BASE_DIR}/3_Out/1_Mosaic"
TIMESLICE_OUTPUT_DIR="${BASE_DIR}/3_Out/2_TimeSeries"
OUTLIER_GPKG_DIR="${BASE_DIR}/3_Out/3_Outlier_Points"
```

### Vegetation Indices

```bash
PROCESSED_VIS="NDVI EVI2 NIRv"   # Space-separated; compute all three
PROCESSED_VIS="NDVI"              # Compute NDVI only
```

### Step Control

```bash
STEPS="all"                                # Run all 10 steps
STEPS="products"                           # Steps 02–10 (skip download)
STEPS="mosaics"                            # Steps 06–08 only
STEPS="outliers"                           # Steps 05, 07, 08, 10
STEPS="vi_calc netcdf mean_flat"           # Named steps, space-separated
```

**Step aliases:**

| Alias | Expands To |
|-------|-----------|
| `all` | Steps 01–10 |
| `products` | Steps 02–10 |
| `mosaics` | Steps 06–08 |
| `outliers` | Steps 05, 07, 08, 10 |

**Named step identifiers:**
`download` · `vi_calc` · `netcdf` · `mean_flat` · `outlier_flat` · `mean_mosaic` · `outlier_mosaic` · `outlier_counts` · `timeseries` · `outlier_gpkg`

### Tile List

```bash
HLS_TILES="17TNE 17TNF 17TNG 17TPE 17TPF"   # Space-separated MGRS tile IDs
```

Tile filtering is applied at every step. If `HLS_TILES` is unset, all tiles found in each input directory are processed.

### Download Settings

```bash
DOWNLOAD_CYCLES="2015-12-01|2016-03-31 2016-12-01|2017-03-31"   # Space-separated date ranges
CLOUD_COVERAGE_MAX=75     # Maximum cloud cover % accepted by CMR query
SPATIAL_COVERAGE_MIN=0    # Minimum spatial coverage % accepted by CMR query
```

### Band Selection

Specifies which bands to download for each HLS sensor:

```bash
L30_BANDS="B05 B04 Fmask"   # Landsat:    NIR (B05), Red (B04), Quality (Fmask)
S30_BANDS="B8A B04 Fmask"   # Sentinel-2: NIR narrow (B8A), Red (B04), Quality (Fmask)
```

> **Note:** The pipeline validates that the bands required for each VI in `PROCESSED_VIS` are present in the band lists before any step runs and will warn you if something is missing.

### Fmask Quality Masking

Control which pixel categories are excluded. Masked pixels are set to NaN and excluded from all downstream computations.

```bash
MASK_CIRRUS="TRUE"
MASK_CLOUD="TRUE"
MASK_ADJACENT_CLOUD="TRUE"
MASK_CLOUD_SHADOW="TRUE"
MASK_SNOW_ICE="TRUE"
MASK_WATER="TRUE"
MASK_AEROSOL_MODE="MODERATE"   # NONE | HIGH | MODERATE | LOW
HLS_SCALE_FACTOR=0.0001        # HLS surface reflectance scale factor
```

**Aerosol masking modes:**

| Mode | Behaviour |
|------|-----------|
| `NONE` | No aerosol masking applied |
| `HIGH` | Mask high-aerosol pixels only |
| `MODERATE` | Mask moderate and high aerosol pixels |
| `LOW` | Mask all non-zero aerosol levels |

### Valid Ranges (Outlier Detection)

Pixels within the valid range contribute to the temporal mean. Pixels outside the valid range are treated as outliers and tracked separately through Steps 05, 07, 08, and 10.

```bash
VALID_RANGE_NDVI="-1,1"
VALID_RANGE_EVI2="-1,2"
VALID_RANGE_NIRv="-0.5,1"
```

### Time-Series Windows

Define named time windows for seasonal composite stack generation (Step 09):

```bash
TIMESLICE_ENABLED="TRUE"
TIMESLICE_STAT="mean"
TIMESLICE_WINDOWS="Winter_2015_2016:2015-12-01|2016-03-31 \
                   Summer_2016:2016-06-01|2016-08-31 \
                   Winter_2016_2017:2016-12-01|2017-03-31"
```

Format: `label:YYYY-MM-DD|YYYY-MM-DD`, space-separated. Labels must be alphanumeric with underscores only. Each label becomes the band description in the output stack.

### Processing Performance

```bash
NUM_WORKERS=8            # Parallel worker processes — set to available CPU cores
CHUNK_SIZE=10            # NetCDF time slices per dask chunk
TARGET_CRS="EPSG:6350"  # Output CRS (default: NAD83 Conus Albers Equal Area)
```

---

## Quick Start

A minimal example to process a single summer season for two tiles:

**1. Edit `config.env`:**

```bash
BASE_DIR="/path/to/your/output"
HLS_TILES="18TVL 18TVM"
PROCESSED_VIS="NDVI"
DOWNLOAD_CYCLES="2020-06-01|2020-08-31"
STEPS="all"
NUM_WORKERS=4
```

**2. Activate the environment and run:**

```bash
conda activate hls_pipeline
bash hls_pipeline.sh
```

The pipeline will print a run summary, prompt you to confirm the storage estimate before downloading, then execute each step sequentially, logging all output to `${BASE_DIR}/0_Logs/`.

---

## Running the Pipeline

### Full Run

```bash
bash hls_pipeline.sh
```

The pipeline prints a run summary showing active steps, VIs, tile count, worker count, and target CRS, then logs all step output to a timestamped file in `LOG_DIR`.

### Partial Runs

Use the `STEPS` variable to reprocess specific stages without rerunning the entire pipeline:

```bash
# In config.env
STEPS="mean_mosaic outlier_mosaic outlier_counts"

# Or inline as a one-off override
STEPS="mean_mosaic" bash hls_pipeline.sh
```

### Resuming After Failure

Each step skips output files that already exist. If a run is interrupted mid-step, simply rerun the pipeline — completed output files will not be regenerated.

---

## Step Reference

### Step 01 — Download

Downloads HLS granules via the NASA CMR API with an interactive storage estimate and user approval gate.

- **Inputs:** NASA CMR API (date ranges, tile IDs, cloud/spatial coverage thresholds, band list)
- **Outputs:** Raw GeoTIFFs in `RAW_HLS_DIR`, organized by sensor/year/tile hierarchy
- **Key feature:** Estimates total storage (raw + VI + NetCDF) and requires user confirmation before downloading
- **Credentials required:** `~/.netrc` with NASA Earthdata login

### Step 02 — VI Calculation

Applies Fmask quality masking and computes vegetation indices from raw surface reflectance bands.

- **Inputs:** Raw L30/S30 band GeoTIFFs from `RAW_HLS_DIR`
- **Outputs:** One GeoTIFF per granule per VI in `VI_OUTPUT_DIR`
- **Masking:** Configurable bitwise Fmask decode (cloud, shadow, snow/ice, water, aerosol)
- **Parallelism:** `multiprocessing.Pool` with `NUM_WORKERS`

### Step 03 — NetCDF Build

Aggregates per-granule VI GeoTIFFs into per-tile time-series files.

- **Inputs:** VI GeoTIFFs from `VI_OUTPUT_DIR`
- **Outputs:** `T{TILE}_{VI}.nc` in `NETCDF_DIR` — CF-1.8 compliant with `days since 1970-01-01` time encoding and sensor (L30/S30) metadata per observation
- **Parallelism:** Chunked writes with `ProcessPoolExecutor`

### Step 04 — Temporal Mean + Reproject

Computes the pixel-wise temporal mean for each tile across the full date range and reprojects to `TARGET_CRS`.

- **Inputs:** NetCDF time-series from `NETCDF_DIR`
- **Outputs:** `T{TILE}_{VI}_average_{VI}_{CRS}.tif` in `REPROJECTED_DIR` (30 m, Cloud-Optimized GeoTIFF)
- **Note:** Valid-range filtering is applied before computing the mean; outlier pixels do not contribute to the average

### Step 05 — Outlier Extraction + Reproject

Identifies pixels with unmasked values outside the per-VI valid range and summarizes them per tile.

- **Inputs:** NetCDF time-series from `NETCDF_DIR`
- **Outputs:** Two GeoTIFFs per tile per VI in `REPROJECTED_DIR_OUTLIERS`:
  - Outlier mean: temporal mean of out-of-range values
  - Outlier count: number of time slices with an outlier at each pixel

### Steps 06–08 — Mosaics

Merge all per-tile rasters into study-area-wide mosaics using streaming merge (memory-efficient, handles large tile counts).

| Step | Output Filename | Notes |
|------|----------------|-------|
| 06 | `HLS_Mosaic_{VI}_{CRS}.tif` | Temporal mean mosaic |
| 07 | `HLS_Mosaic_Outlier_Mean_{VI}_{CRS}.tif` | Outlier mean mosaic |
| 08 | `HLS_Mosaic_Outlier_Count_{VI}_{CRS}.tif` | Outlier count mosaic (uint16, nodata=0) |

### Step 09 — Time-Series Stacks

Builds multi-band seasonal composite GeoTIFFs from user-defined time windows.

- **Inputs:** NetCDF time-series + `TIMESLICE_WINDOWS` definitions from `config.env`
- **Outputs:** Two multi-band stacks per VI in `TIMESLICE_OUTPUT_DIR`:
  - `HLS_TimeSeries_{VI}_Mean_{CRS}.tif` — temporal mean per window (one band per window)
  - `HLS_TimeSeries_{VI}_CountValid_{CRS}.tif` — valid-pixel count per window (one band per window)
- **Band descriptions:** Window labels are embedded in band metadata and are visible in QGIS, GDAL, and rasterio

### Step 10 — Outlier GeoPackage

Exports every individual outlier pixel-date observation as a point feature, enabling spatial and temporal exploration of anomalies.

- **Inputs:** NetCDF time-series from `NETCDF_DIR`
- **Outputs:** `HLS_outliers_{VI}.gpkg` in `OUTLIER_GPKG_DIR` (WGS84 / EPSG:4326 points)
- **Feature attributes:** `tile_id`, `vi_type`, `sensor`, `date`, `vi_value`, `geometry`
- **Use cases:** Sensor artifact investigation, data quality review, anomaly mapping

---

## Output Products

### Directory Structure

```
${BASE_DIR}/
├── 0_Logs/
│   └── hls_pipeline_YYYYMMDD_HHMMSS.log
├── 1_Raw/
│   ├── L30/YYYY/HH/T/T/T/
│   │   └── HLS.L30.T{TILE}.{DATE}.v2.0.{Band}.tif
│   └── S30/YYYY/HH/T/T/T/
│       └── HLS.S30.T{TILE}.{DATE}.v2.0.{Band}.tif
├── 2_Interim/
│   ├── 1_VI_Products/
│   │   └── HLS.{L30|S30}.T{TILE}.{DATE}.v2.0.{VI}.tif
│   ├── 2_NetCDF/
│   │   └── T{TILE}_{VI}.nc
│   ├── 3_VI_Mean_Tiles/
│   │   └── T{TILE}_{VI}_average_{VI}_{CRS}.tif
│   └── 4_VI_Outlier_Tiles/
│       ├── T{TILE}_{VI}_outlier_mean_{VI}_{CRS}.tif
│       └── T{TILE}_{VI}_outlier_count_{VI}_{CRS}.tif
└── 3_Out/
    ├── 1_Mosaic/
    │   ├── HLS_Mosaic_{VI}_{CRS}.tif
    │   ├── HLS_Mosaic_Outlier_Mean_{VI}_{CRS}.tif
    │   └── HLS_Mosaic_Outlier_Count_{VI}_{CRS}.tif
    ├── 2_TimeSeries/
    │   ├── HLS_TimeSeries_{VI}_Mean_{CRS}.tif       (N bands — one per window)
    │   └── HLS_TimeSeries_{VI}_CountValid_{CRS}.tif (N bands — one per window)
    └── 3_Outlier_Points/
        └── HLS_outliers_{VI}.gpkg
```

### File Format Reference

| Product | Format | Dtype | Nodata | Compression |
|---------|--------|-------|--------|-------------|
| VI GeoTIFF (step 02) | GeoTIFF | float32 | NaN | LZW |
| NetCDF time-series (step 03) | NetCDF-4 | float32 | NaN | zlib |
| Mean tile (step 04) | COG GeoTIFF | float32 | NaN | LZW + predictor 3 |
| Outlier mean tile (step 05) | GeoTIFF | float32 | NaN | LZW + predictor 3 |
| Outlier count tile (step 05) | GeoTIFF | uint16 | 0 | LZW + predictor 2 |
| Mean / outlier mosaics (steps 06–07) | GeoTIFF | float32 | NaN | LZW |
| Count mosaic (step 08) | GeoTIFF | uint16 | 0 | LZW |
| Time-series stacks (step 09) | BigTIFF | float32 / uint16 | NaN / 0 | LZW |
| Outlier GeoPackage (step 10) | GeoPackage | — | — | — |

> **Nodata note:** A value of `0` in count products means "no outliers observed at this pixel," not missing data.

---

## Advanced Usage

### Running a Subset of Steps

```bash
# In config.env — rerun mosaics after adding more tiles
STEPS="mean_mosaic outlier_mosaic outlier_counts"

# One-off override without editing config.env
STEPS="outlier_gpkg" bash hls_pipeline.sh
```

### Computing Multiple VIs

```bash
PROCESSED_VIS="NDVI EVI2 NIRv"
```

All steps loop over each VI in `PROCESSED_VIS`. Ensure all required bands are listed in `L30_BANDS` and `S30_BANDS`. For NDVI, EVI2, and NIRv, the required bands are `B04` (Red), `B05`/`B8A` (NIR), and `Fmask`.

### Custom Time Windows

Time windows can span any date range and do not need to follow calendar boundaries:

```bash
TIMESLICE_WINDOWS="Peak_Green_2020:2020-06-15|2020-07-31 \
                   Late_Summer_2020:2020-08-01|2020-09-15 \
                   Dormant_2020_2021:2020-11-15|2021-03-15"
```

The output stack will have one band per window. Band descriptions (window labels) are stored in the GeoTIFF metadata and are visible in QGIS, ArcGIS Pro, and when reading with rasterio or GDAL.

### Tuning Valid Ranges

Default valid ranges are broad. Narrow them for domain-specific applications:

```bash
VALID_RANGE_NDVI="0.1,0.9"   # Forest canopy: exclude bare soil and sparse cover
VALID_RANGE_EVI2="-0.5,1.5"  # Wider range for agricultural areas
```

Any unmasked pixel outside these bounds is flagged as an outlier and routed to Steps 05, 07, 08, and 10.

### Optimising Worker Count

Set `NUM_WORKERS` based on available physical CPU cores. A safe starting point is (total cores − 2) to leave headroom for the OS and dask overhead:

```bash
NUM_WORKERS=14   # Example for a 16-core workstation
```

Reduce this value if you encounter out-of-memory errors during Steps 04, 05, or 09.

### Changing the Output CRS

The default CRS is `EPSG:6350` (NAD83 Conus Albers Equal Area). Change `TARGET_CRS` to any EPSG code supported by PROJ:

```bash
TARGET_CRS="EPSG:32618"   # UTM Zone 18N (WGS84)
TARGET_CRS="EPSG:3857"    # Web Mercator
```

All reprojected outputs and mosaics (Steps 04–09) will use the new CRS. The CRS code (dots stripped) is embedded in output filenames (e.g., `EPSG6350`).

---

## Troubleshooting

### Download fails with a 401 or authentication error

Verify your `~/.netrc` credentials, file permissions (`chmod 600 ~/.netrc`), and that you have accepted all required EULAs for HLS on the [NASA Earthdata](https://urs.earthdata.nasa.gov/) website.

### `syntax error` or unexpected token in `hls_pipeline.sh`

Verify that `config.env` is present in the repository root and that all required variables are set. The pipeline has been tested on macOS (ZSH) and Linux.

### Out-of-memory errors during Steps 04, 05, or 09

Reduce `NUM_WORKERS` and/or `CHUNK_SIZE` in `config.env`. Fewer simultaneous dask tasks significantly reduce peak RAM usage.

### A step finishes but output files are missing

Check the timestamped log file in `LOG_DIR` for error messages. Most steps print tile-level warnings when inputs are missing or when a tile is skipped.

### The mosaic is missing one or more tiles

Verify that the missing tiles are included in `HLS_TILES` and that their intermediate reprojected GeoTIFFs exist in `REPROJECTED_DIR` (for Steps 06–07) or `REPROJECTED_DIR_OUTLIERS` (for Steps 07–08).

### NetCDF time coordinate errors or wrong dates

HLS granule filenames must not be renamed. Step 03 parses acquisition dates directly from the standard HLS filename convention: `HLS.{L30|S30}.T{TILE}.{YYYYDDD}T{HHMMSS}.v2.0`.

### Step 09 time-series stack has fewer bands than expected

If `TIMESLICE_ENABLED` is not set to `"TRUE"`, Step 09 will skip processing. Also verify that at least one observation falls within each configured time window for the tiles in `HLS_TILES`.

---

## Credits & Acknowledgments

### Authors

**Stephen Conklin**, Geospatial Analyst — Pipeline architecture, orchestration, and all original code · [stephenconklin@gmail.com](mailto:stephenconklin@gmail.com) · [github.com/stephenconklin](https://github.com/stephenconklin)

**G. Burch Fisher, PhD**, Research Scientist — Conceptual guidance and original code adapted for:
- `02_hls_vi_calc.py` (VI calculation and Fmask quality masking logic)
- `03_hls_netcdf_build.py` (NetCDF time-series assembly)

### AI Assistance

This pipeline was developed with the assistance of [Google Gemini](https://gemini.google.com/) and [Anthropic Claude / Claude Code](https://claude.ai/code). These tools assisted with code generation and refinement under the direction and review of the authors.

### Adapted Code

**NASA HLS Download Script**
`01_hls_download.sh` is adapted from the NASA [`getHLS.sh`](https://github.com/nasa/HLS-Data-Resources/tree/main/bash/hls-bulk-download) script, published by the NASA HLS Data Resources Team under the Apache 2.0 License.

### HLS Data Citation

Users of this pipeline who publish results should cite the HLS datasets:

> Masek, J., Ju, J., Roger, J.-C., Skakun, S., Vermote, E., Claverie, M., Dungan, J., Yin, Z., Freitag, B., Justice, C. (2021). *HLS Operational Land Imager Surface Reflectance and TOA Brightness Daily Global 30m v2.0* [Data set]. NASA EOSDIS Land Processes DAAC. https://doi.org/10.5067/HLS/HLSL30.002

> Skakun, S., Ju, J., Roger, J.-C., Vermote, E., Masek, J., Justice, C. (2021). *HLS Sentinel-2 Multi-spectral Instrument Surface Reflectance Daily Global 30m v2.0* [Data set]. NASA EOSDIS Land Processes DAAC. https://doi.org/10.5067/HLS/HLSS30.002

### Python Libraries

This pipeline is built on the following open-source libraries:

| Library | Purpose | License |
|---------|---------|---------|
| [rasterio](https://rasterio.readthedocs.io/) | GeoTIFF I/O, reprojection, mosaicing | BSD-3 |
| [xarray](https://xarray.pydata.org/) | N-dimensional array and NetCDF processing | Apache 2.0 |
| [rioxarray](https://corteva.github.io/rioxarray/) | Spatial extensions for xarray | Apache 2.0 |
| [dask](https://dask.org/) | Parallel and chunked computation | BSD-3 |
| [geopandas](https://geopandas.org/) | GeoPackage vector I/O | BSD-3 |
| [numpy](https://numpy.org/) | Array mathematics | BSD-3 |
| [pandas](https://pandas.pydata.org/) | Tabular data handling | BSD-3 |
| [netCDF4](https://unidata.github.io/netcdf4-python/) | Low-level NetCDF I/O | MIT |
| [shapely](https://shapely.readthedocs.io/) | Point geometry construction | BSD-3 |
| [pyproj](https://pyproj4.github.io/pyproj/) | CRS transformations | MIT |

---

## License

This project is licensed under the [MIT License](LICENSE).

```
MIT License

Copyright (c) 2026 Stephen Conklin

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

`01_hls_download.sh` is adapted from NASA's [`getHLS.sh`](https://github.com/nasa/HLS-Data-Resources/tree/main/bash/hls-bulk-download), released by NASA under the [Apache 2.0 License](https://github.com/nasa/HLS-Data-Resources/blob/main/LICENSE).
