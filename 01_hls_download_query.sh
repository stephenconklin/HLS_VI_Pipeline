#!/bin/bash
# =================================================================
# 01_hls_download_query.sh
# HLS VI Pipeline — Step 01: CMR Query & Download
#
# Single-cycle NASA CMR API query and parallel band downloader.
# Called by hls_pipeline.sh for each tile in each configured date cycle.
# Supports estimate mode (returns granule count only) and batch mode
# (auto-approves and downloads).
#
# Usage: ./01_hls_download_query.sh <tilelist> <date_begin> <date_end> <out_dir>
#
# Author:  Stephen Conklin <stephenconklin@gmail.com>
#          https://github.com/stephenconklin
# Adapted in part from: getHLS.sh by NASA HLS Data Resources Team
#   https://github.com/nasa/HLS-Data-Resources/tree/main/bash/hls-bulk-download
# License: MIT
# =================================================================

if [ $# -ne 4 ]
then
    echo "Usage: $0 <tilelist> <date_begin> <date_end> <out_dir>" >&2
    exit 1
fi

tilelist=$1
datebeg=$2
dateend=$3
OUTDIR=$4

# --- CONFIGURATION FROM ENV ---
NP=${NUM_WORKERS:-8}
CLOUD=${CLOUD_COVERAGE_MAX:-100}
SPATIAL=${SPATIAL_COVERAGE_MIN:-0}
VIS_LIST=${PROCESSED_VIS:-"NDVI"}

# Defaults if not set in config.env
L30_BANDS=${L30_BANDS:-"B05 B04 B02 Fmask"}
S30_BANDS=${S30_BANDS:-"B8A B04 B02 Fmask"}

# Only print header if NOT in estimate mode to keep output clean for parsing
if [ "$HLS_MODE" != "estimate" ]; then
    echo "   [Downloader] Workers: $NP | Cloud Max: $CLOUD% | Spatial Min: $SPATIAL%"
    echo "   [Bands L30] $L30_BANDS"
    echo "   [Bands S30] $S30_BANDS"
fi

### earthdata account check
if [ ! -f $HOME/.netrc ]; then
    echo "$HOME/.netrc file unavailable" >&2
    exit 1
fi

### Delete the tailing "/" if there is any.
OUTDIR=$(echo $OUTDIR | sed 's:/$::')   
export OUTDIR

### wget/curl availability
WGET=false; CURL=false
which wget >/dev/null 2>&1 && WGET=true
which curl >/dev/null 2>&1 && CURL=true

if [ $WGET = false ] && [ $CURL = false ]; then
    echo "This script needs wget or curl to be installed." >&2
    exit 1
fi 
export WGET CURL

### Build up the query strings
# Unique temp file naming to avoid collisions in parallel runs
fbase=tmp_$(basename $1)_${datebeg}_${dateend}_$$
query="https://cmr.earthdata.nasa.gov/search/granules.json?collection_concept_id=C2021957295-LPCLOUD&collection_concept_id=C2021957657-LPCLOUD&page_size=2000"
query="${query}&temporal=${datebeg}T00:00:00Z,${dateend}T23:59:59Z"
query="${query}&attribute[]=int,SPATIAL_COVERAGE,$SPATIAL,"

meta=/tmp/${fbase}.down.meta.txt
>$meta

# Loop through tiles and fetch metadata
# (We silence output in estimate mode to keep the pipe clean)
for tile in $(cat $tilelist); do
    query_final="${query}&attribute[]=int,CLOUD_COVERAGE,,$CLOUD"
    if [ $WGET = true ]; then
        wget -q "${query_final}&attribute[]=string,MGRS_TILE_ID,$tile" -O - >>$meta
    else
        curl -s "${query_final}&attribute[]=string,MGRS_TILE_ID,$tile" >>$meta
    fi
done

### --- BAND FILTERING LOGIC ---
flist=/tmp/${fbase}.down.flist.txt
export flist

# Convert space-separated config string to pipe-separated regex
# e.g., "B05 B04" -> "B05|B04"
L30_REGEX=$(echo "$L30_BANDS" | tr ' ' '|')
S30_REGEX=$(echo "$S30_BANDS" | tr ' ' '|')

tr "," "\n" < $meta | 
  grep https | 
  grep -E "HLS\.L30\..*\.(${L30_REGEX})\.tif|HLS\.S30\..*\.(${S30_REGEX})\.tif" | 
  tr "\"" " " | 
  awk '{print $3}' | 
  awk -F"/" '{print $NF, $0}' | 
  sort -k1,1 | 
  awk '{print $2}' > $flist

### --- MODE 1: ESTIMATE ONLY ---
# If HLS_MODE is 'estimate', just print the count and exit.
ng=$(grep Fmask $flist | wc -l | awk '{print $1}')

if [ "$HLS_MODE" == "estimate" ]; then
    # Output ONLY the number of granules so the master script can read it
    echo "$ng"
    # Clean up and exit
    rm -f $meta $flist
    exit 0
fi

### --- DOWNLOAD FUNCTION ---
# Downloads all band files for a single granule, validating each file with
# gdalinfo after download. Corrupt or missing files are retried up to
# MAX_RETRIES times. If any file cannot be downloaded cleanly, the entire
# granule directory is removed so step 02 skips it rather than crashing.
function download_granule() {
    fullpath=$1
    Fmaskbase=$(basename $fullpath)

    granule=$(echo $Fmaskbase | awk -F"." '{print $1 "." $2 "." $3 "." $4 "." $5 "." $6}')
    allfile=/tmp/tmp.files.in.${granule}.$$.txt
    grep $granule $flist > $allfile

    set $(echo $Fmaskbase | awk -F"." '{ print $2, substr($3,2,5), substr($4,1,4)}')
    type=$1; tileid=$2; year=$3
    subdir=$(echo $tileid | awk '{print substr($0,1,2) "/" substr($0,3,1) "/" substr($0,4,1) "/" substr($0,5,1)}')
    outdir=$OUTDIR/$type/$year/$subdir/$granule
    mkdir -p $outdir

    cookie=/tmp/tmp.cookie.$granule.$$
    MAX_RETRIES=3
    granule_ok=true

    echo "Downloading into $outdir"

    while IFS= read -r url; do
        fname=$(basename "$url")
        outfile="$outdir/$fname"

        # Skip if the file already exists and is a valid raster
        if gdalinfo "$outfile" >/dev/null 2>&1; then
            continue
        fi

        # Download with per-attempt retry and gdalinfo validation
        downloaded=false
        for attempt in $(seq 1 $MAX_RETRIES); do
            rm -f "$outfile"
            if [ $WGET = true ]; then
                wget -q -O "$outfile" "$url"
            else
                curl --cookie-jar "$cookie" -n -s -L --output "$outfile" "$url"
            fi
            if gdalinfo "$outfile" >/dev/null 2>&1; then
                downloaded=true
                break
            fi
            echo "   [WARNING] Attempt $attempt/$MAX_RETRIES failed for $fname (not a valid raster)"
            sleep $((attempt * 2))
        done

        if [ "$downloaded" = false ]; then
            echo "   [ERROR] Failed to download valid file after $MAX_RETRIES attempts: $fname"
            rm -f "$outfile"
            granule_ok=false
        fi
    done < "$allfile"

    rm -f "$allfile" "$cookie"

    if [ "$granule_ok" = false ]; then
        echo "   [ERROR] Removing incomplete granule directory: $outdir"
        rm -rf "$outdir"
    fi
}
export -f download_granule

### --- STORAGE ESTIMATION DISPLAY ---

if [ "$ng" -gt 0 ]; then
    vis_count=$(echo "$VIS_LIST" | wc -w | awk '{print $1}')
    
    # Conservative Estimates (MB)
    RAW_PER_GRANULE=60
    VI_PER_GRANULE=54
    NC_PER_GRANULE=54
    
    total_processed_per_vi=$(( VI_PER_GRANULE + NC_PER_GRANULE ))
    total_processed_all_vis=$(( total_processed_per_vi * vis_count ))
    total_per_granule=$(( RAW_PER_GRANULE + total_processed_all_vis ))
    
    est_mb=$(( ng * total_per_granule ))
    est_gb=$(( est_mb / 1024 ))

    msg="
------------------------------------------------------
   [Storage Est] Granules to Process: $ng
   [Storage Est] Target VIs ($vis_count): $VIS_LIST
   [Storage Est] Raw Download: ~$(( ng * RAW_PER_GRANULE )) MB
   [Storage Est] Pipeline Output: ~$(( ng * total_processed_all_vis )) MB
   [Storage Est] TOTAL REQUIRED: ~${est_mb} MB (~${est_gb} GB)
------------------------------------------------------"
    
    # Print the estimate banner (unless in batch mode where we want less noise)
    if [ "$HLS_MODE" != "batch" ]; then
        echo "$msg"
    fi

    # --- MODE 2: BATCH (AUTO-APPROVE) ---
    if [ "$HLS_MODE" == "batch" ]; then
        echo "   [Batch Mode] Auto-approving download of $ng granules..."
        response="y"
    
    # --- MODE 3: INTERACTIVE (DEFAULT) ---
    else 
        if [ -c /dev/tty ]; then
            echo "$msg" > /dev/tty
            echo "   >>> Do you want to proceed with this batch? (y/n)" > /dev/tty
            read -n 1 -r response < /dev/tty
            echo "" > /dev/tty
        else
            # If no TTY and not in batch mode, default to abort for safety
            response="n" 
        fi
    fi

    if [[ ! $response =~ ^[Yy]$ ]]; then
        echo "   [Aborted] User cancelled download."
        rm -f $meta $flist
        exit 1
    fi
else
    echo "   [Storage Est] 0 granules found."
fi

echo "$ng granules to download"
grep Fmask $flist | xargs -n1 -P $NP -I% bash -c "download_granule %"  

rm -f $meta $flist
exit 0