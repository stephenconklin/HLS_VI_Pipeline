#!/bin/bash
# =================================================================
# hls_pipeline.sh
# HLS VI Pipeline — Master Orchestrator
#
# USAGE
#   conda activate hls_pipeline
#   bash hls_pipeline.sh
#
# STEP CONTROL (set STEPS= in config.env)
#
#   Named steps (any combination, space-separated):
#     download        01 — Download raw HLS data from NASA LP DAAC
#     vi_calc         02 — Compute VI GeoTIFFs from raw bands
#     netcdf          03 — Build per-tile NetCDF time-series
#     mean_flat       04 — Temporal mean tiles, reproject
#     outlier_flat    05 — Outlier mean + count tiles, reproject
#     mean_mosaic     06 — Mosaic mean tiles
#     outlier_mosaic  07 — Mosaic outlier mean tiles
#     outlier_counts  08 — Mosaic outlier count tiles
#     count_valid_mosaic 09 — CountValid mosaic across all download cycles
#     timeseries      10 — Custom time-window multi-band stacks
#     outlier_gpkg    11 — Export per-pixel outlier observations to GeoPackage
#
#   Aliases:
#     all             Steps 01–11 (full pipeline)
#     products        Steps 02–11 (skip download)
#     mosaics         Steps 06–08 (re-mosaic only)
#     outliers        Steps 05+07+08+11 (full outlier chain)
#
# Script-to-step mapping:
#   download       → 01_hls_download.sh  (calls 01a_hls_download_query.sh)
#   vi_calc        → 02_hls_vi_calc.py
#   netcdf         → 03_hls_netcdf_build.py
#   mean_flat      → 04_hls_mean_reproject.py
#   outlier_flat   → 05_hls_outlier_reproject.py
#   mean_mosaic    → 06_hls_mean_mosaic.py
#   outlier_mosaic → 07_hls_outlier_mean_mosaic.py
#   outlier_counts → 08_hls_outlier_count_mosaic.py
#   count_valid_mosaic → 09_hls_count_valid_mosaic.py
#   timeseries       → 10_hls_timeseries_mosaic.py
#   outlier_gpkg     → 11_hls_outlier_gpkg.py
#
# Author:  Stephen Conklin <stephenconklin@gmail.com>
#          https://github.com/stephenconklin
# License: MIT
# =================================================================
set -e
set -o pipefail   # Propagate exit codes through pipes (e.g. python script | tee logfile)

# -----------------------------------------------------------------
# 1. LOAD CONFIGURATION
# -----------------------------------------------------------------
if [ -f config.env ]; then
    set -a; source config.env; set +a
    echo "Configuration loaded from config.env"
else
    echo "Error: config.env not found."
    exit 1
fi

# -----------------------------------------------------------------
# 2. VALIDATE BAND REQUIREMENTS VS PROCESSED_VIS
# -----------------------------------------------------------------
# Each VI has a known minimum set of bands. Check that L30_BANDS and
# S30_BANDS contain everything needed for the VIs in PROCESSED_VIS.
# Prints a warning for missing bands; does not abort (the user may be
# intentionally skipping download and the bands already exist on disk).

_check_bands() {
    local sensor="$1"      # L30 or S30
    local band_list="$2"   # e.g. "B05 B04 Fmask"
    local vi="$3"          # e.g. NDVI
    local required="$4"    # space-separated required bands

    local missing=""
    for band in $required; do
        if ! echo "$band_list" | grep -qw "$band"; then
            missing="$missing $band"
        fi
    done

    if [ -n "$missing" ]; then
        echo "  [WARN] VI=${vi}, Sensor=${sensor}: missing band(s):${missing}"
        echo "         Add$(echo $missing) to ${sensor}_BANDS in config.env"
        return 1
    fi
    return 0
}

echo "Validating band requirements for PROCESSED_VIS: ${PROCESSED_VIS}"
BAND_WARNINGS=0

for vi in $PROCESSED_VIS; do
    case "$vi" in
        NDVI|EVI2|NIRv)
            # All three require NIR + Red + Fmask only
            _check_bands "L30" "$L30_BANDS" "$vi" "B05 B04 Fmask" || BAND_WARNINGS=$((BAND_WARNINGS+1))
            _check_bands "S30" "$S30_BANDS" "$vi" "B8A B04 Fmask" || BAND_WARNINGS=$((BAND_WARNINGS+1))
            ;;
        EVI)
            # 3-band EVI additionally requires Blue (B02)
            _check_bands "L30" "$L30_BANDS" "$vi" "B05 B04 B02 Fmask" || BAND_WARNINGS=$((BAND_WARNINGS+1))
            _check_bands "S30" "$S30_BANDS" "$vi" "B8A B04 B02 Fmask" || BAND_WARNINGS=$((BAND_WARNINGS+1))
            ;;
        *)
            echo "  [WARN] VI=${vi} is not recognised — no band requirements defined."
            echo "         Supported VIs: NDVI EVI2 NIRv  (add EVI for 3-band EVI)"
            BAND_WARNINGS=$((BAND_WARNINGS+1))
            ;;
    esac
done

if [ "$BAND_WARNINGS" -eq 0 ]; then
    echo "  Band requirements satisfied for all VIs."
else
    echo ""
    echo "  ${BAND_WARNINGS} band warning(s) above. The pipeline will continue, but"
    echo "  vi_calc (Step 02) may fail if required bands were not downloaded."
    echo "  Fix the band lists in config.env before running Step 01 (download)."
fi
echo ""
# -----------------------------------------------------------------
# 3. RESOLVE STEPS ALIASES INTO CANONICAL STEP NAMES
# -----------------------------------------------------------------
# Expand any aliases in STEPS to their constituent step names,
# then deduplicate while preserving logical pipeline order.

ALL_STEPS="download vi_calc netcdf mean_flat outlier_flat mean_mosaic outlier_mosaic outlier_counts count_valid_mosaic timeseries outlier_gpkg"
PRODUCTS_STEPS="vi_calc netcdf mean_flat outlier_flat mean_mosaic outlier_mosaic outlier_counts count_valid_mosaic timeseries outlier_gpkg"
MOSAICS_STEPS="mean_mosaic outlier_mosaic outlier_counts"
OUTLIERS_STEPS="outlier_flat outlier_mosaic outlier_counts outlier_gpkg"

# Expand aliases: replace each alias token with its constituent steps
EXPANDED=""
for token in ${STEPS:-all}; do
    case "$token" in
        all)      EXPANDED="$EXPANDED $ALL_STEPS" ;;
        products) EXPANDED="$EXPANDED $PRODUCTS_STEPS" ;;
        mosaics)  EXPANDED="$EXPANDED $MOSAICS_STEPS" ;;
        outliers) EXPANDED="$EXPANDED $OUTLIERS_STEPS" ;;
        download|vi_calc|netcdf|mean_flat|outlier_flat| \
        mean_mosaic|outlier_mosaic|outlier_counts|count_valid_mosaic|timeseries|outlier_gpkg)
                  EXPANDED="$EXPANDED $token" ;;
        *)
            echo "Error: Unknown step name '${token}' in STEPS."
            echo "       Valid steps: download vi_calc netcdf mean_flat outlier_flat"
            echo "                    mean_mosaic outlier_mosaic outlier_counts count_valid_mosaic timeseries outlier_gpkg"
            echo "       Valid aliases: all products mosaics outliers"
            exit 1
            ;;
    esac
done

# Deduplicate expanded list while preserving pipeline order
ACTIVE_STEPS=""
for canonical in $ALL_STEPS; do
    for requested in $EXPANDED; do
        if [ "$requested" = "$canonical" ]; then
            # Only add if not already in ACTIVE_STEPS
            if ! echo "$ACTIVE_STEPS" | grep -qw "$canonical"; then
                ACTIVE_STEPS="$ACTIVE_STEPS $canonical"
            fi
            break
        fi
    done
done
ACTIVE_STEPS="${ACTIVE_STEPS## }"   # trim leading space

# Helper: returns 0 (true) if a step name is in the active list
step_active() {
    echo "$ACTIVE_STEPS" | grep -qw "$1"
}

# -----------------------------------------------------------------
# 4. SETUP DIRECTORIES & LOGGING
# -----------------------------------------------------------------
mkdir -p "$LOG_DIR" "$VI_OUTPUT_DIR" "$NETCDF_DIR" \
         "$REPROJECTED_DIR" "$REPROJECTED_DIR_OUTLIERS" \
         "$MOSAIC_DIR" "${TIMESLICE_OUTPUT_DIR:-${BASE_DIR}/3_Out/2_TimeSeries}" \
         "${OUTLIER_GPKG_DIR:-${BASE_DIR}/3_Out/3_Outlier_Points}"

TIMESTAMP=$(date +"%Y%m%d_%H%M")
LOGFILE="${LOG_DIR}/pipeline_run_${TIMESTAMP}.log"

# -----------------------------------------------------------------
# 5. PRINT RUN SUMMARY
# -----------------------------------------------------------------
{
echo "================================================================="
echo " HLS VI PIPELINE — RUN SUMMARY"
echo "================================================================="
echo " Timestamp:  $TIMESTAMP"
echo " VIs:        $PROCESSED_VIS"
echo " Tiles:      $(echo $HLS_TILES | wc -w | tr -d ' ')"
echo " Workers:    $NUM_WORKERS"
echo " CRS:        $TARGET_CRS"
echo " STEPS:      ${STEPS:-all}"
echo ""
echo " Active steps (in execution order):"
for step in $ALL_STEPS; do
    num=""
    label=""
    case "$step" in
        download)       num="01"; label="Download raw HLS data" ;;
        vi_calc)        num="02"; label="VI calculation" ;;
        netcdf)         num="03"; label="NetCDF aggregation" ;;
        mean_flat)      num="04"; label="Temporal mean + reproject" ;;
        outlier_flat)   num="05"; label="Outlier extraction + reproject" ;;
        mean_mosaic)    num="06"; label="Mean mosaic" ;;
        outlier_mosaic) num="07"; label="Outlier mean mosaic" ;;
        outlier_counts) num="08"; label="Outlier count mosaic" ;;
        count_valid_mosaic) num="09"; label="CountValid mosaic (all download cycles)" ;;
        timeseries)       num="10"; label="Time-series stacks" ;;
        outlier_gpkg)     num="11"; label="Outlier GeoPackage export" ;;
    esac
    if step_active "$step"; then
        echo "   [✓] Step $num  $label  ($step)"
    else
        echo "   [–] Step $num  $label  (skipped)"
    fi
done
echo ""
echo " Log: $LOGFILE"
echo "================================================================="
} | tee -a "$LOGFILE"

# -----------------------------------------------------------------
# STEP 01: DOWNLOAD
# -----------------------------------------------------------------
if step_active "download"; then
    echo "" | tee -a "$LOGFILE"
    echo "[Step 01 | download] Downloading raw HLS data..." | tee -a "$LOGFILE"
    ./01_hls_download.sh 2>&1 | tee -a "$LOGFILE"
    echo "[Step 01] Complete." | tee -a "$LOGFILE"
fi

# -----------------------------------------------------------------
# STEP 02: VI CALCULATION
# -----------------------------------------------------------------
if step_active "vi_calc"; then
    echo "" | tee -a "$LOGFILE"
    echo "[Step 02 | vi_calc] Calculating VIs: ${PROCESSED_VIS} ..." | tee -a "$LOGFILE"
    "$PYTHON_EXEC" 02_hls_vi_calc.py 2>&1 | tee -a "$LOGFILE"
    echo "[Step 02] Complete." | tee -a "$LOGFILE"
fi

# -----------------------------------------------------------------
# STEP 03: NETCDF AGGREGATION
# -----------------------------------------------------------------
if step_active "netcdf"; then
    echo "" | tee -a "$LOGFILE"
    echo "[Step 03 | netcdf] Building NetCDF time-series..." | tee -a "$LOGFILE"
    "$PYTHON_EXEC" 03_hls_netcdf_build.py 2>&1 | tee -a "$LOGFILE"
    echo "[Step 03] Complete." | tee -a "$LOGFILE"
fi

# -----------------------------------------------------------------
# STEP 04: TEMPORAL MEAN + REPROJECT
# -----------------------------------------------------------------
if step_active "mean_flat"; then
    echo "" | tee -a "$LOGFILE"
    echo "[Step 04 | mean_flat] Computing temporal means for: ${PROCESSED_VIS} ..." | tee -a "$LOGFILE"
    "$PYTHON_EXEC" 04_hls_mean_reproject.py 2>&1 | tee -a "$LOGFILE"
    echo "[Step 04] Complete." | tee -a "$LOGFILE"
fi

# -----------------------------------------------------------------
# STEP 05: OUTLIER EXTRACTION + REPROJECT
# -----------------------------------------------------------------
if step_active "outlier_flat"; then
    echo "" | tee -a "$LOGFILE"
    echo "[Step 05 | outlier_flat] Extracting outliers for: ${PROCESSED_VIS} ..." | tee -a "$LOGFILE"
    "$PYTHON_EXEC" 05_hls_outlier_reproject.py 2>&1 | tee -a "$LOGFILE"
    echo "[Step 05] Complete." | tee -a "$LOGFILE"
fi

# -----------------------------------------------------------------
# STEP 06: MEAN MOSAIC
# -----------------------------------------------------------------
if step_active "mean_mosaic"; then
    echo "" | tee -a "$LOGFILE"
    echo "[Step 06 | mean_mosaic] Mosaicking mean tiles for: ${PROCESSED_VIS} ..." | tee -a "$LOGFILE"
    "$PYTHON_EXEC" 06_hls_mean_mosaic.py 2>&1 | tee -a "$LOGFILE"
    echo "[Step 06] Complete." | tee -a "$LOGFILE"
fi

# -----------------------------------------------------------------
# STEP 07: OUTLIER MEAN MOSAIC
# -----------------------------------------------------------------
if step_active "outlier_mosaic"; then
    echo "" | tee -a "$LOGFILE"
    echo "[Step 07 | outlier_mosaic] Mosaicking outlier mean tiles..." | tee -a "$LOGFILE"
    "$PYTHON_EXEC" 07_hls_outlier_mean_mosaic.py 2>&1 | tee -a "$LOGFILE"
    echo "[Step 07] Complete." | tee -a "$LOGFILE"
fi

# -----------------------------------------------------------------
# STEP 08: OUTLIER COUNT MOSAIC
# -----------------------------------------------------------------
if step_active "outlier_counts"; then
    echo "" | tee -a "$LOGFILE"
    echo "[Step 08 | outlier_counts] Mosaicking outlier count tiles..." | tee -a "$LOGFILE"
    "$PYTHON_EXEC" 08_hls_outlier_count_mosaic.py 2>&1 | tee -a "$LOGFILE"
    echo "[Step 08] Complete." | tee -a "$LOGFILE"
fi

# -----------------------------------------------------------------
# STEP 09: COUNTVALID MOSAIC (ALL DOWNLOAD CYCLES)
# -----------------------------------------------------------------
if step_active "count_valid_mosaic"; then
    echo "" | tee -a "$LOGFILE"
    echo "[Step 09 | count_valid_mosaic] Building CountValid mosaic for: ${PROCESSED_VIS} ..." | tee -a "$LOGFILE"
    "$PYTHON_EXEC" 09_hls_count_valid_mosaic.py 2>&1 | tee -a "$LOGFILE"
    echo "[Step 09] Complete." | tee -a "$LOGFILE"
fi

# -----------------------------------------------------------------
# STEP 10: TIME-SERIES STACKS
# -----------------------------------------------------------------
if step_active "timeseries"; then
    if [ "${TIMESLICE_ENABLED:-FALSE}" = "TRUE" ]; then
        echo "" | tee -a "$LOGFILE"
        echo "[Step 10 | timeseries] Building time-series stacks for: ${PROCESSED_VIS} ..." | tee -a "$LOGFILE"
        echo "[Step 10] Windows: ${TIMESLICE_WINDOWS}" | tee -a "$LOGFILE"
        "$PYTHON_EXEC" 10_hls_timeseries_mosaic.py 2>&1 | tee -a "$LOGFILE"
        echo "[Step 10] Complete." | tee -a "$LOGFILE"
    else
        echo "" | tee -a "$LOGFILE"
        echo "[Step 10 | timeseries] Skipped — TIMESLICE_ENABLED is not TRUE." | tee -a "$LOGFILE"
    fi
fi

# -----------------------------------------------------------------
# STEP 11: OUTLIER GEOPACKAGE EXPORT
# -----------------------------------------------------------------
if step_active "outlier_gpkg"; then
    echo "" | tee -a "$LOGFILE"
    echo "[Step 11 | outlier_gpkg] Exporting outlier points to GeoPackage for: ${PROCESSED_VIS} ..." | tee -a "$LOGFILE"
    "$PYTHON_EXEC" 11_hls_outlier_gpkg.py 2>&1 | tee -a "$LOGFILE"
    echo "[Step 11] Complete." | tee -a "$LOGFILE"
fi

# -----------------------------------------------------------------
# DONE
# -----------------------------------------------------------------
{
echo ""
echo "================================================================="
echo " PIPELINE COMPLETE"
echo " Finished:   $(date +"%Y%m%d_%H%M")"
echo " Steps run:  ${ACTIVE_STEPS}"
echo " VIs:        ${PROCESSED_VIS}"
echo " Mosaic:     ${MOSAIC_DIR}"
if [ "${TIMESLICE_ENABLED:-FALSE}" = "TRUE" ] && step_active "timeseries"; then
echo " TimeSeries: ${TIMESLICE_OUTPUT_DIR}"
fi
if step_active "outlier_gpkg"; then
echo " OutlierPts: ${OUTLIER_GPKG_DIR}"
fi
echo "================================================================="
} | tee -a "$LOGFILE"
