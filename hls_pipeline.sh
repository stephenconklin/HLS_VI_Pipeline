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
#     build_nc        Steps 01–03 (download → VI calc → NetCDF)
#     mosaics         Steps 06–08 (re-mosaic only)
#     outliers        Steps 05+07+08+11 (full outlier chain)
#
# Script-to-step mapping:
#   download       → 01_hls_download_query.sh
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
BUILD_NC_STEPS="download vi_calc netcdf"
MOSAICS_STEPS="mean_mosaic outlier_mosaic outlier_counts"
OUTLIERS_STEPS="outlier_flat outlier_mosaic outlier_counts outlier_gpkg"

# Expand aliases: replace each alias token with its constituent steps
EXPANDED=""
for token in ${STEPS:-all}; do
    case "$token" in
        all)      EXPANDED="$EXPANDED $ALL_STEPS" ;;
        products) EXPANDED="$EXPANDED $PRODUCTS_STEPS" ;;
        build_nc) EXPANDED="$EXPANDED $BUILD_NC_STEPS" ;;
        mosaics)  EXPANDED="$EXPANDED $MOSAICS_STEPS" ;;
        outliers) EXPANDED="$EXPANDED $OUTLIERS_STEPS" ;;
        download|vi_calc|netcdf|mean_flat|outlier_flat| \
        mean_mosaic|outlier_mosaic|outlier_counts|count_valid_mosaic|timeseries|outlier_gpkg)
                  EXPANDED="$EXPANDED $token" ;;
        *)
            echo "Error: Unknown step name '${token}' in STEPS."
            echo "       Valid steps: download vi_calc netcdf mean_flat outlier_flat"
            echo "                    mean_mosaic outlier_mosaic outlier_counts count_valid_mosaic timeseries outlier_gpkg"
            echo "       Valid aliases: all products build_nc mosaics outliers"
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
echo " SpaceSaver: Raw=${SPACE_SAVER_REMOVE_RAW:-FALSE}, VI=${SPACE_SAVER_REMOVE_VI:-FALSE}"
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
# STEPS 01–03: DOWNLOAD / VI CALC / NETCDF
# Processes one tile at a time: download → vi_calc → netcdf → (optional cleanup)
# -----------------------------------------------------------------

    # ------------------------------------------------------------------
    # PRE-FLIGHT: Storage estimate + one user approval (download only)
    # ------------------------------------------------------------------
    if step_active "download"; then
        echo "" | tee -a "$LOGFILE"
        echo "[Tile-by-tile] Phase 1: Calculating storage requirements..." | tee -a "$LOGFILE"

        TEMP_EST_TILE_FILE="./tiles_tbt_estimate.txt"
        echo "$HLS_TILES" | tr ' ' '\n' > "$TEMP_EST_TILE_FILE"

        TBT_TOTAL_GRANULES=0
        for range in $DOWNLOAD_CYCLES; do
            tbt_start=$(echo $range | cut -d'|' -f1)
            tbt_end=$(echo $range | cut -d'|' -f2)
            echo -n "    Scanning $tbt_start to $tbt_end... " | tee -a "$LOGFILE"
            export HLS_MODE="estimate"
            tbt_count=$(./01_hls_download_query.sh "$TEMP_EST_TILE_FILE" "$tbt_start" "$tbt_end" "$RAW_HLS_DIR")
            echo "$tbt_count granules." | tee -a "$LOGFILE"
            TBT_TOTAL_GRANULES=$((TBT_TOTAL_GRANULES + tbt_count))
        done
        rm -f "$TEMP_EST_TILE_FILE"

        tbt_vis_count=$(echo "$PROCESSED_VIS" | wc -w | awk '{print $1}')

        # --- Size constants (MB) — empirically calibrated estimates ---
        # Calibrated from two test datasets:
        #   - 3-tile, 1-VI, 236-granule run  (South Africa, Oct–Dec 2025)
        #   - 27-tile, 3-VI, 3,876-granule run (PA Mountain Laurel, winter 2015–2021)
        # Steps 01–03 per-granule sizes:
        TBT_RAW_PER_GRANULE=45           # ~3 raw band TIFs (HLS int16 COGs); ~41 MB measured
        TBT_VI_PER_GRANULE=15            # float32 LZW per granule per VI; ~8–11 MB measured
        TBT_NC_PER_GRANULE=12            # zlib-compressed NC; ~6–8 MB/granule/VI measured (7x
                                         #   smaller than uncompressed due to NaN-heavy scenes)
        # Steps 04–11 per-tile output sizes (LZW-compressed):
        TBT_MEAN_TILE_MB=55              # per-tile mean reprojected GeoTIFF; ~53 MB measured (PA)
        TBT_OUTLIER_TILE_MB=5            # per-tile outlier mean + count; highly data-dependent —
                                         #   nearly 0 for NDVI [-1,1], higher for tighter ranges
        TBT_MOSAIC_FLOAT_PER_TILE_MB=35  # per-tile contribution to a float32 mosaic; ~34 MB measured (PA)
        TBT_MOSAIC_INT_PER_TILE_MB=3     # per-tile contribution to a uint16 mosaic; ~3 MB measured
        TBT_TS_PER_TILE_WIN_MB=50        # per-tile per-window time-series: mean+count; ~49 MB measured (PA)

        # --- Per-component totals for steps 01–03 (gated on each step being active) ---
        tbt_total_raw_mb=$(( TBT_TOTAL_GRANULES * TBT_RAW_PER_GRANULE ))
        if step_active "vi_calc"; then
            tbt_total_vi_mb=$(( TBT_TOTAL_GRANULES * TBT_VI_PER_GRANULE * tbt_vis_count ))
        else
            tbt_total_vi_mb=0
        fi
        if step_active "netcdf"; then
            tbt_total_nc_mb=$(( TBT_TOTAL_GRANULES * TBT_NC_PER_GRANULE * tbt_vis_count ))
        else
            tbt_total_nc_mb=0
        fi

        # --- Per-tile granule approximation for peak calculation ---
        # A 1.5x coverage factor accounts for tiles that receive more Landsat/Sentinel-2
        # overpasses than the mean (swath overlaps, edge tiles). Conservative overestimate.
        tbt_n_tiles=$(echo $HLS_TILES | wc -w | tr -d ' ')
        if [ "$tbt_n_tiles" -gt 0 ]; then
            tbt_tile_granules=$(( (TBT_TOTAL_GRANULES * 3 / 2 + tbt_n_tiles - 1) / tbt_n_tiles ))
        else
            tbt_tile_granules=0
        fi
        tbt_tile_raw_mb=$(( tbt_tile_granules * TBT_RAW_PER_GRANULE ))
        if step_active "vi_calc"; then
            tbt_tile_vi_mb=$(( tbt_tile_granules * TBT_VI_PER_GRANULE * tbt_vis_count ))
        else
            tbt_tile_vi_mb=0
        fi

        # --- Space-saver peak/final for steps 01–03 ---
        # Space-saver deletion in the tile loop is guarded by step_active "netcdf".
        # If netcdf is not in STEPS, no auto-deletion fires and files accumulate fully.
        if [ "${SPACE_SAVER_REMOVE_RAW:-FALSE}" = "TRUE" ] && step_active "netcdf"; then
            tbt_peak_raw_mb=$tbt_tile_raw_mb   # one tile's raw on disk at peak
            tbt_final_raw_mb=0                 # deleted after each tile's NetCDF
        else
            tbt_peak_raw_mb=$tbt_total_raw_mb
            tbt_final_raw_mb=$tbt_total_raw_mb
        fi
        if [ "${SPACE_SAVER_REMOVE_VI:-FALSE}" = "TRUE" ] && step_active "netcdf"; then
            tbt_peak_vi_mb=$tbt_tile_vi_mb
            tbt_final_vi_mb=0
        else
            tbt_peak_vi_mb=$tbt_total_vi_mb
            tbt_final_vi_mb=$tbt_total_vi_mb
        fi

        tbt_peak_mb=$(( tbt_total_nc_mb + tbt_peak_raw_mb + tbt_peak_vi_mb ))
        tbt_final_mb=$(( tbt_total_nc_mb + tbt_final_raw_mb + tbt_final_vi_mb ))

        # --- Downstream products estimate (steps 04–11) ---
        # These run after the tile loop and add to whatever steps 01–03 left on disk.
        # Sizes scale by n_tiles, n_vis, and (for step 10) n_windows.
        tbt_ds_step04_mb=0
        tbt_ds_step05_mb=0
        tbt_ds_step06_mb=0
        tbt_ds_step07_mb=0
        tbt_ds_step08_mb=0
        tbt_ds_step09_mb=0
        tbt_ds_step10_mb=0
        tbt_n_windows=0

        if step_active "mean_flat"; then
            tbt_ds_step04_mb=$(( tbt_n_tiles * tbt_vis_count * TBT_MEAN_TILE_MB ))
        fi
        if step_active "outlier_flat"; then
            tbt_ds_step05_mb=$(( tbt_n_tiles * tbt_vis_count * TBT_OUTLIER_TILE_MB ))
        fi
        if step_active "mean_mosaic"; then
            tbt_ds_step06_mb=$(( tbt_n_tiles * tbt_vis_count * TBT_MOSAIC_FLOAT_PER_TILE_MB ))
        fi
        if step_active "outlier_mosaic"; then
            tbt_ds_step07_mb=$(( tbt_n_tiles * tbt_vis_count * TBT_MOSAIC_FLOAT_PER_TILE_MB ))
        fi
        if step_active "outlier_counts"; then
            tbt_ds_step08_mb=$(( tbt_n_tiles * tbt_vis_count * TBT_MOSAIC_INT_PER_TILE_MB ))
        fi
        if step_active "count_valid_mosaic"; then
            tbt_ds_step09_mb=$(( tbt_n_tiles * tbt_vis_count * TBT_MOSAIC_INT_PER_TILE_MB ))
        fi
        if step_active "timeseries" && [ "${TIMESLICE_ENABLED:-FALSE}" = "TRUE" ]; then
            tbt_n_windows=$(echo $TIMESLICE_WINDOWS | wc -w | tr -d ' ')
            tbt_ds_step10_mb=$(( tbt_n_tiles * tbt_vis_count * tbt_n_windows * TBT_TS_PER_TILE_WIN_MB ))
        fi

        tbt_ds_total_mb=$(( tbt_ds_step04_mb + tbt_ds_step05_mb + \
                            tbt_ds_step06_mb + tbt_ds_step07_mb + \
                            tbt_ds_step08_mb + tbt_ds_step09_mb + \
                            tbt_ds_step10_mb ))
        tbt_grand_final_mb=$(( tbt_final_mb + tbt_ds_total_mb ))

        tbt_peak_gb=$(( tbt_peak_mb / 1024 ))
        tbt_final_gb=$(( tbt_final_mb / 1024 ))
        tbt_grand_final_gb=$(( tbt_grand_final_mb / 1024 ))

        {
        echo ""
        echo "======================================================"
        echo " TILE-BY-TILE STORAGE ESTIMATE"
        echo "======================================================"
        echo " Cycles:          $(echo $DOWNLOAD_CYCLES | wc -w | tr -d ' ')"
        echo " Tiles:           $tbt_n_tiles"
        echo " Total Granules:  $TBT_TOTAL_GRANULES"
        echo ""
        echo " Steps 01–03:"
        echo "   Raw download:  ~${tbt_total_raw_mb} MB"
        if step_active "vi_calc"; then
        echo "   VI GeoTIFFs:   ~${tbt_total_vi_mb} MB  (per-granule, ${tbt_vis_count} VI(s))"
        fi
        if step_active "netcdf"; then
        echo "   NetCDF:        ~${tbt_total_nc_mb} MB  (compressed time-series)"
        fi
        echo "   Space Saver:   Raw=${SPACE_SAVER_REMOVE_RAW:-FALSE}, VI=${SPACE_SAVER_REMOVE_VI:-FALSE}"
        echo "   Est. Peak:     ~${tbt_peak_gb} GB  (worst-case during tile loop)"
        echo "   Est. Final:    ~${tbt_final_gb} GB  (on-disk after tile loop)"
        if [ "$tbt_ds_total_mb" -gt 0 ]; then
        echo ""
        echo " Steps 04–11 (active steps only, rough estimates):"
        if [ "$tbt_ds_step04_mb" -gt 0 ]; then
        echo "   Step 04 mean tiles:         ~${tbt_ds_step04_mb} MB"
        fi
        if [ "$tbt_ds_step05_mb" -gt 0 ]; then
        echo "   Step 05 outlier tiles:      ~${tbt_ds_step05_mb} MB"
        fi
        tbt_ds_mosaics=$(( tbt_ds_step06_mb + tbt_ds_step07_mb + tbt_ds_step08_mb + tbt_ds_step09_mb ))
        if [ "$tbt_ds_mosaics" -gt 0 ]; then
        echo "   Steps 06–09 mosaics:        ~${tbt_ds_mosaics} MB"
        fi
        if [ "$tbt_ds_step10_mb" -gt 0 ]; then
        echo "   Step 10 time-series:        ~${tbt_ds_step10_mb} MB  (${tbt_n_windows} windows, ${tbt_vis_count} VI(s))"
        fi
        if step_active "outlier_gpkg"; then
        echo "   Step 11 outlier GeoPackage: variable (depends on outlier rate)"
        fi
        echo "   Steps 04–11 subtotal:       ~${tbt_ds_total_mb} MB"
        fi
        echo ""
        echo " Grand Total (all active steps):  ~${tbt_grand_final_gb} GB"
        echo "======================================================"
        echo ""
        } | tee -a "$LOGFILE"

        if [ "${SKIP_APPROVAL:-FALSE}" = "TRUE" ]; then
            echo "[Approval skipped — SKIP_APPROVAL=TRUE]" | tee -a "$LOGFILE"
        elif [ -c /dev/tty ]; then
            echo ">>> Proceed with download? (y/n)" > /dev/tty
            read -n 1 -r tbt_response < /dev/tty
            echo "" > /dev/tty
            if [[ ! $tbt_response =~ ^[Yy]$ ]]; then
                echo "Aborted by user." | tee -a "$LOGFILE"
                exit 1
            fi
        else
            echo "Error: Non-interactive shell. Set SKIP_APPROVAL=TRUE to bypass." | tee -a "$LOGFILE"
            exit 1
        fi
    fi

    # ------------------------------------------------------------------
    # TILE LOOP
    # ------------------------------------------------------------------
    TBT_ORIG_TILES="$HLS_TILES"
    TBT_FAILED_TILES=""
    TBT_SUCCEEDED_TILES=""

    set +e  # Tile failures skip the tile, not the pipeline

    for tbt_tile in $TBT_ORIG_TILES; do
        echo "" | tee -a "$LOGFILE"
        echo "[Tile-by-tile] ====== Processing tile: $tbt_tile ======" | tee -a "$LOGFILE"
        export HLS_TILES="$tbt_tile"
        TBT_TILE_OK=true

        # --- Step 01: Download this tile ---
        if step_active "download" && [ "$TBT_TILE_OK" = "true" ]; then
            echo "[Step 01 | $tbt_tile] Downloading..." | tee -a "$LOGFILE"
            TBT_SINGLE_TILE_FILE="./tile_${tbt_tile}_active_run.txt"
            echo "$tbt_tile" > "$TBT_SINGLE_TILE_FILE"
            TBT_DL_OK=true
            for range in $DOWNLOAD_CYCLES; do
                tbt_start=$(echo $range | cut -d'|' -f1)
                tbt_end=$(echo $range | cut -d'|' -f2)
                export HLS_MODE="batch"
                ./01_hls_download_query.sh "$TBT_SINGLE_TILE_FILE" "$tbt_start" "$tbt_end" "$RAW_HLS_DIR" 2>&1 | tee -a "$LOGFILE"
                if [ ${PIPESTATUS[0]} -ne 0 ]; then
                    echo "[ERROR][Step 01] Tile $tbt_tile failed on cycle $tbt_start. Skipping tile." | tee -a "$LOGFILE"
                    TBT_DL_OK=false
                    break
                fi
            done
            rm -f "$TBT_SINGLE_TILE_FILE"
            [ "$TBT_DL_OK" = "false" ] && TBT_TILE_OK=false
        fi

        # --- Step 02: VI calc for this tile ---
        if step_active "vi_calc" && [ "$TBT_TILE_OK" = "true" ]; then
            echo "[Step 02 | $tbt_tile] Calculating VIs: ${PROCESSED_VIS} ..." | tee -a "$LOGFILE"
            "$PYTHON_EXEC" 02_hls_vi_calc.py 2>&1 | tee -a "$LOGFILE"
            if [ ${PIPESTATUS[0]} -ne 0 ]; then
                echo "[ERROR][Step 02] Tile $tbt_tile failed. Skipping tile." | tee -a "$LOGFILE"
                TBT_TILE_OK=false
            fi
        fi

        # --- Step 03: NetCDF for this tile ---
        if step_active "netcdf" && [ "$TBT_TILE_OK" = "true" ]; then
            echo "[Step 03 | $tbt_tile] Building NetCDF..." | tee -a "$LOGFILE"
            "$PYTHON_EXEC" 03_hls_netcdf_build.py 2>&1 | tee -a "$LOGFILE"
            if [ ${PIPESTATUS[0]} -ne 0 ]; then
                echo "[ERROR][Step 03] Tile $tbt_tile failed. Skipping tile." | tee -a "$LOGFILE"
                TBT_TILE_OK=false
            fi
        fi

        # --- Space saver cleanup (only after successful step 03) ---
        if [ "$TBT_TILE_OK" = "true" ] && step_active "netcdf"; then
            if [ "${SPACE_SAVER_REMOVE_RAW:-FALSE}" = "TRUE" ]; then
                find "$RAW_HLS_DIR" -name "HLS.*.T${tbt_tile}.*.tif" -type f -delete
                echo "[Space Saver] Deleted raw HLS files for tile $tbt_tile" | tee -a "$LOGFILE"
            fi
            if [ "${SPACE_SAVER_REMOVE_VI:-FALSE}" = "TRUE" ]; then
                find "$VI_OUTPUT_DIR" -name "HLS.*.T${tbt_tile}.*.tif" -type f -delete
                echo "[Space Saver] Deleted VI GeoTIFFs for tile $tbt_tile" | tee -a "$LOGFILE"
            fi
        fi

        # --- Track tile result ---
        if [ "$TBT_TILE_OK" = "true" ]; then
            TBT_SUCCEEDED_TILES="$TBT_SUCCEEDED_TILES $tbt_tile"
        else
            TBT_FAILED_TILES="$TBT_FAILED_TILES $tbt_tile"
        fi
    done

    set -e  # Restore fail-fast for steps 04–11

    # Restore full tile list for steps 04–11
    export HLS_TILES="$TBT_ORIG_TILES"

    {
    echo ""
    echo "-----------------------------------------------------------------"
    echo " Steps 01–03 complete."
    echo " Succeeded: $(echo $TBT_SUCCEEDED_TILES | tr ' ' '\n' | sort | tr '\n' ' ')"
    if [ -n "$TBT_FAILED_TILES" ]; then
    echo " [WARN] Failed: $TBT_FAILED_TILES"
    fi
    echo "-----------------------------------------------------------------"
    } | tee -a "$LOGFILE"


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
