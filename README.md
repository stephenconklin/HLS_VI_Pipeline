# HLS Vegetation Index Pipeline

[![Python](https://img.shields.io/badge/python-3.10--3.12-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Platform](https://img.shields.io/badge/platform-linux%20%7C%20macOS-lightgrey.svg)]()
[![Data: HLS v2.0](https://img.shields.io/badge/data-HLS%20v2.0-brightgreen.svg)](https://hls.gsfc.nasa.gov/)
[![Docs](https://readthedocs.org/projects/hls-vi-pipeline/badge/?version=latest)](https://hls-vi-pipeline.readthedocs.io/en/latest/overview.html)

A production-ready, 11-step processing pipeline for computing and analyzing vegetation indices from NASA's Harmonized Landsat and Sentinel-2 (HLS) surface reflectance data. Built for researchers across ecology, agriculture, land management, and remote sensing who need reproducible, time-series vegetation analysis at scale.

---

## Documentation

Full documentation — prerequisites, configuration reference, step-by-step guide, output products, and troubleshooting — is available at:

**[https://hls-vi-pipeline.readthedocs.io/en/latest/overview.html](https://hls-vi-pipeline.readthedocs.io/en/latest/overview.html)**

---
<!--
<div style="display: flex; flex-wrap: wrap; gap: 10px; text-align: center;">

  <div style="flex: 1 1 45%;">
    <img src="https://via.placeholder.com/300x200" alt="Image 1" style="max-width: 100%;">
    <p><i>Caption for Image 1</i></p>
  </div>

  <div style="flex: 1 1 45%;">
    <img src="https://via.placeholder.com/300x200" alt="Image 2" style="max-width: 100%;">
    <p><i>Caption for Image 2</i></p>
  </div>

  <div style="flex: 1 1 45%;">
    <img src="https://via.placeholder.com/300x200" alt="Image 3" style="max-width: 100%;">
    <p><i>Caption for Image 3</i></p>
  </div>

  <div style="flex: 1 1 45%;">
    <img src="https://via.placeholder.com/300x200" alt="Image 4" style="max-width: 100%;">
    <p><i>Caption for Image 4</i></p>
  </div>

  <div style="flex: 1 1 45%;">
    <img src="https://via.placeholder.com/300x200" alt="Image 5" style="max-width: 100%;">
    <p><i>Caption for Image 5</i></p>
  </div>

  <div style="flex: 1 1 45%;">
    <img src="https://via.placeholder.com/300x200" alt="Image 6" style="max-width: 100%;">
    <p><i>Caption for Image 6</i></p>
  </div>

</div>

---

============================================================
     YOUTUBE VIDEO — replace YOUR_VIDEO_ID with your video ID
     Example ID: dQw4w9WgXcQ  (from youtube.com/watch?v=dQw4w9WgXcQ)
     ============================================================
[![Watch the demo](https://img.youtube.com/vi/YOUR_VIDEO_ID/maxresdefault.jpg)](https://www.youtube.com/watch?v=YOUR_VIDEO_ID)

---
-->

## What It Produces

| Output | Description |
|--------|-------------|
| VI GeoTIFFs | Cloud-masked NDVI / EVI2 / NIRv per granule |
| NetCDF time-series | CF-1.8 compliant, per-tile stacks with sensor metadata |
| Temporal mean mosaics | Study-area-wide, reprojected to your target CRS |
| Seasonal stacks | Multi-band composites for user-defined time windows |
| Outlier products | Raster summaries + searchable GeoPackage of anomalous pixels |

**Supported indices:**

| Index | Formula | Range |
|-------|---------|-------|
| NDVI | `(NIR − Red) / (NIR + Red)` | −1.0 to 1.0 |
| EVI2 | `2.5 × (NIR − Red) / (NIR + 2.4×Red + 1)` | −1.0 to 2.0 |
| NIRv | `NDVI × NIR` | −0.5 to 1.0 |

**Data source:** [NASA HLS v2.0](https://hls.gsfc.nasa.gov/) — 30 m resolution, ~2–3 day combined revisit time.

---

## Key Features

- **End-to-end automation** — a single `bash hls_pipeline.sh` command runs all 11 steps
- **Flexible step control** — run any subset of steps by name or built-in alias
- **Quality masking** — bitwise Fmask decode with configurable cloud, shadow, snow/ice, water, and aerosol flags
- **Outlier detection** — flags pixels outside per-VI valid ranges; exports raster summaries and point-vector GeoPackages
- **Seasonal composites** — user-defined, named time windows with labels embedded in band metadata
- **Memory-efficient** — dask-chunked xarray and streaming rasterio mosaics scale to large study areas
- **Tile-by-tile processing** — steps 01–03 process one MGRS tile at a time to minimize peak disk usage

---

## Quick Start

```bash
# 1. Clone and set up the environment
git clone https://github.com/stephenconklin/HLS_VI_Pipeline.git
cd HLS_VI_Pipeline
conda env create -f environment.yml
conda activate hls_pipeline

# 2. Edit config.env, then run
bash hls_pipeline.sh
```
---

## Credits & Acknowledgments

### Authors

**Stephen Conklin**, Geospatial Analyst — Pipeline architecture, orchestration, and all original code · [github.com/stephenconklin](https://github.com/stephenconklin)

**G. Burch Fisher, PhD**, Research Scientist — Conceptual guidance and original code adapted for:
- `02_hls_vi_calc.py` (VI calculation and Fmask quality masking logic)
- `03_hls_netcdf_build.py` (NetCDF time-series assembly)

### AI Assistance

This pipeline was developed with the assistance of [Google Gemini](https://gemini.google.com/) and [Anthropic Claude / Claude Code](https://claude.ai/code). These tools assisted with code generation and refinement under the direction and review of the authors.

### Adapted Code

**NASA HLS Download Script**
`01_hls_download_query.sh` is adapted in part from the NASA [`getHLS.sh`](https://github.com/nasa/HLS-Data-Resources/tree/main/bash/hls-bulk-download) script, published by the NASA HLS Data Resources Team under the Apache 2.0 License.

### HLS Data Citation

Users of this pipeline who publish results should cite the HLS datasets:

> Masek, J., Ju, J., Roger, J.-C., Skakun, S., Vermote, E., Claverie, M., Dungan, J., Yin, Z., Freitag, B., Justice, C. (2021). *HLS Operational Land Imager Surface Reflectance and TOA Brightness Daily Global 30m v2.0* [Data set]. NASA EOSDIS Land Processes DAAC. https://doi.org/10.5067/HLS/HLSL30.002

> Skakun, S., Ju, J., Roger, J.-C., Vermote, E., Masek, J., Justice, C. (2021). *HLS Sentinel-2 Multi-spectral Instrument Surface Reflectance Daily Global 30m v2.0* [Data set]. NASA EOSDIS Land Processes DAAC. https://doi.org/10.5067/HLS/HLSS30.002

---

## License

[MIT License](LICENSE) · Copyright (c) 2026 Stephen Conklin
