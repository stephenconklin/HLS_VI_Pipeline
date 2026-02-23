#!/bin/bash
# =================================================================
# 01_hls_download.sh
# HLS VI Pipeline — Step 01: Download Loop
#
# Multi-cycle download orchestrator. Calls 01a_hls_download_query.sh
# for each configured date cycle. Provides a pre-download storage
# estimate and user approval gate before downloading any data.
#
# Author:  Stephen Conklin <stephenconklin@gmail.com>
#          https://github.com/stephenconklin
# Adapted from: getHLS.sh by NASA HLS Data Resources Team
#   https://github.com/nasa/HLS-Data-Resources/tree/main/bash/hls-bulk-download
# License: MIT
# =================================================================

# STEP 0: LOAD CONFIGURATION
# =================================================================
if [ -z "$RAW_HLS_DIR" ] || [ -z "$HLS_TILES" ]; then
    if [ -f config.env ]; then
        set -a; source config.env; set +a
    else
        echo "Error: config.env not found and critical variables (RAW_HLS_DIR, HLS_TILES) are missing."
        exit 1
    fi
fi

# Double check that the output directory is actually set
if [ -z "$RAW_HLS_DIR" ]; then
    echo "Error: RAW_HLS_DIR is not set in config.env"
    exit 1
fi

OUT_DIR="$RAW_HLS_DIR"

# =================================================================
# STEP 1: PREPARE TILE LIST
# =================================================================
TEMP_TILE_FILE="./tiles_active_run.txt"
echo "$HLS_TILES" | tr ' ' '\n' > "$TEMP_TILE_FILE"

echo "------------------------------------------------------"
echo "HLS DOWNLOAD PIPELINE INITIATED"
echo "------------------------------------------------------"

# =================================================================
# STEP 2: GLOBAL ESTIMATION (PASS 1)
# =================================================================
echo ">>> Phase 1: Calculating Total Storage Requirements..."
echo "    (This scans NASA CMR for all cycles, please wait)"

TOTAL_GRANULES=0

# Iterate through cycles purely for counting
for range in $DOWNLOAD_CYCLES; do
    start_date=$(echo $range | cut -d'|' -f1)
    end_date=$(echo $range | cut -d'|' -f2)
    
    echo -n "    Scanning $start_date to $end_date... "
    
    # CALL 01a_hls_download_query.sh IN ESTIMATE MODE
    # It will return ONLY the integer count of granules
    export HLS_MODE="estimate"
    count=$(./01a_hls_download_query.sh "$TEMP_TILE_FILE" "$start_date" "$end_date" "$OUT_DIR")
    
    echo "$count granules."
    TOTAL_GRANULES=$((TOTAL_GRANULES + count))
done

# --- CALCULATE TOTAL SIZE ---
# (Constants must match 01a_hls_download_query.sh)
vis_count=$(echo "$PROCESSED_VIS" | wc -w | awk '{print $1}')
RAW_PER_GRANULE=60
VI_PER_GRANULE=54
NC_PER_GRANULE=54

total_processed_per_vi=$(( VI_PER_GRANULE + NC_PER_GRANULE ))
total_processed_all_vis=$(( total_processed_per_vi * vis_count ))
total_per_granule=$(( RAW_PER_GRANULE + total_processed_all_vis ))

TOTAL_MB=$(( TOTAL_GRANULES * total_per_granule ))
TOTAL_GB=$(( TOTAL_MB / 1024 ))

MSG="
======================================================
 TOTAL DOWNLOAD SUMMARY
======================================================
 Cycles:        $(echo $DOWNLOAD_CYCLES | wc -w)
 Total Granules: $TOTAL_GRANULES
 Est. Download:  $(( TOTAL_GRANULES * RAW_PER_GRANULE )) MB
 Est. Processed: $(( TOTAL_GRANULES * total_processed_all_vis )) MB
 -----------------------------------------------------
 GRAND TOTAL:    ~${TOTAL_GB} GB
======================================================
"

# Print summary to log/stdout
echo "$MSG"

# =================================================================
# STEP 3: USER APPROVAL
# =================================================================
# We force this prompt to /dev/tty so it appears even if 
# the script is being piped to a log file.
if [ -c /dev/tty ]; then
    echo "$MSG" > /dev/tty
    echo ">>> Do you want to proceed with the FULL pipeline? (y/n)" > /dev/tty
    read -n 1 -r response < /dev/tty
    echo "Proceeding [Step 0] Checking/Downloading Data..." > /dev/tty
else
    # If not interactive, assume we cannot proceed safely
    echo "Error: Non-interactive shell detected during approval phase."
    exit 1
fi

if [[ ! $response =~ ^[Yy]$ ]]; then
    echo "Aborted by user."
    rm "$TEMP_TILE_FILE"
    exit 1
fi

# =================================================================
# STEP 4: BATCH EXECUTION (PASS 2)
# =================================================================
echo ">>> Phase 2: Starting Batch Download..."

for range in $DOWNLOAD_CYCLES; do
    start_date=$(echo $range | cut -d'|' -f1)
    end_date=$(echo $range | cut -d'|' -f2)

    echo ">>> Processing Batch: $start_date to $end_date"

    # CALL 01a_hls_download_query.sh IN BATCH MODE
    # It will skip the prompt and download immediately
    export HLS_MODE="batch"
    ./01a_hls_download_query.sh "$TEMP_TILE_FILE" "$start_date" "$end_date" "$OUT_DIR"
    
    if [ $? -ne 0 ]; then
        echo "   [STOP] Error encountered in batch $start_date."
        rm "$TEMP_TILE_FILE"
        exit 1 
    fi
    echo "------------------------------------------------------"
done

# =================================================================
# STEP 5: TEARDOWN
# =================================================================
rm "$TEMP_TILE_FILE"
echo "All download cycles complete."