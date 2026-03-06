#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# 02_hls_vi_calc.py
# Pipeline Step 02 (vi_calc): Compute VI GeoTIFFs from raw HLS band files.
#
# Reads PROCESSED_VIS from env. Supports NDVI, EVI2, NIRv in a single pass.
# Parallel processing via multiprocessing.Pool (NUM_WORKERS).
#
# Author:  Stephen Conklin <stephenconklin@gmail.com>
#          https://github.com/stephenconklin
# Adapted from original code by G. Burch Fisher, PhD
# License: MIT

import os
import numpy as np
import rasterio
from rasterio.transform import from_bounds
from rasterio.crs import CRS
import glob
from pathlib import Path
import multiprocessing as mp
import warnings
from hls_utils import filter_by_configured_tiles

# Suppress "NotGeoreferencedWarning" which can be spammy with HLS data
warnings.filterwarnings("ignore", category=rasterio.errors.NotGeoreferencedWarning)

GEOTIFF_COMPRESS = os.environ.get("GEOTIFF_COMPRESS", "LZW").upper()

class HLSProcessor:
    def __init__(self, s30_dir, l30_dir, output_dir, wanted_vis=None):
        self.s30_dir = s30_dir
        self.l30_dir = l30_dir
        self.output_dir = output_dir
        self.wanted_vis = wanted_vis if wanted_vis else ["NDVI", "EVI2", "NIRv"]
        
        # --- BITWISE MASKING CONFIGURATION  ---
        # Defaults match config.env — only relevant if run outside the pipeline
        self.mask_cirrus = os.environ.get("MASK_CIRRUS",          "TRUE").upper()  == "TRUE"
        self.mask_cloud  = os.environ.get("MASK_CLOUD",           "TRUE").upper()  == "TRUE"
        self.mask_adj    = os.environ.get("MASK_ADJACENT_CLOUD",  "TRUE").upper()  == "TRUE"
        self.mask_shadow = os.environ.get("MASK_CLOUD_SHADOW",    "TRUE").upper()  == "TRUE"
        self.mask_snow   = os.environ.get("MASK_SNOW_ICE",        "TRUE").upper()  == "TRUE"
        self.mask_water  = os.environ.get("MASK_WATER",           "TRUE").upper()  == "TRUE"

        # --- AEROSOL MODE SELECTOR  ---
        self.aerosol_mode = os.environ.get("MASK_AEROSOL_MODE", "MODERATE").upper()
        
        # Scale factor
        self.scale_factor = float(os.environ.get("HLS_SCALE_FACTOR", 0.0001))
        
        os.makedirs(self.output_dir, exist_ok=True)
        
    def find_granules(self, base_dir, product_type):
        granules = []
        print(f"Scanning {product_type} directory recursively...")
        search_pattern = os.path.join(base_dir, "**", "*Fmask.tif")
        fmask_files = glob.glob(search_pattern, recursive=True)
        fmask_files = filter_by_configured_tiles(fmask_files)

        print(f"  Found {len(fmask_files)} {product_type} granules.")
        
        for fmask_path in fmask_files:
            folder = os.path.dirname(fmask_path)
            filename = os.path.basename(fmask_path)
            
            if product_type == 'L30':
                red_name = filename.replace('Fmask', 'B04')
                nir_name = filename.replace('Fmask', 'B05')
            else: # S30
                red_name = filename.replace('Fmask', 'B04')
                nir_name = filename.replace('Fmask', 'B8A')
            
            red_path = os.path.join(folder, red_name)
            nir_path = os.path.join(folder, nir_name)
            
            if os.path.exists(red_path) and os.path.exists(nir_path):
                granules.append({
                    'type': product_type,
                    'red': red_path,
                    'nir': nir_path,
                    'fmask': fmask_path,
                    'basename': filename.replace('.Fmask.tif', '')
                })
        return granules

    def calculate_indices(self, red, nir):
        # np.errstate suppresses console warnings for 0/0 and x/0 operations.
        # inf and nan values produced here are intentional — they are physically
        # implausible and will be caught by the valid-range filter in steps 04/05.
        with np.errstate(divide='ignore', invalid='ignore'):
            # NDVI
            ndvi_denom = nir + red
            ndvi = (nir - red) / ndvi_denom
            
            # EVI2
            evi2_denom = nir + 2.4 * red + 1
            evi2 = 2.5 * (nir - red) / evi2_denom
            
            # NIRv
            nirv = ndvi * nir
            
        return ndvi, evi2, nirv

    def process_granule_static(self, granule_info):
        try:
            basename = granule_info['basename']
            
            needed_outputs = []
            if "NDVI" in self.wanted_vis: needed_outputs.append(os.path.join(self.output_dir, f"{basename}.NDVI.tif"))
            if "EVI2" in self.wanted_vis: needed_outputs.append(os.path.join(self.output_dir, f"{basename}.EVI2.tif"))
            if "NIRv" in self.wanted_vis: needed_outputs.append(os.path.join(self.output_dir, f"{basename}.NIRv.tif"))
            
            if all(os.path.exists(p) for p in needed_outputs):
                return f"Skipped (Exists): {basename}"

            with rasterio.open(granule_info['red']) as src_red, \
                 rasterio.open(granule_info['nir']) as src_nir, \
                 rasterio.open(granule_info['fmask']) as src_fmask:
                
                red = src_red.read(1).astype('float32')
                nir = src_nir.read(1).astype('float32')
                fmask = src_fmask.read(1)
                profile = src_red.profile
                
                red = red * self.scale_factor
                nir = nir * self.scale_factor
                
                # --- QUALITY MASKING ---
                mask = np.zeros(fmask.shape, dtype=bool)

                if self.mask_cirrus: mask |= ((fmask & (1 << 0)) > 0)
                if self.mask_cloud:  mask |= ((fmask & (1 << 1)) > 0)
                if self.mask_adj:    mask |= ((fmask & (1 << 2)) > 0)
                if self.mask_shadow: mask |= ((fmask & (1 << 3)) > 0)
                if self.mask_snow:   mask |= ((fmask & (1 << 4)) > 0)
                if self.mask_water:  mask |= ((fmask & (1 << 5)) > 0)
                
                aerosol_level = (fmask >> 6) & 0b11
                
                if self.aerosol_mode == "LOW":
                    mask |= (aerosol_level >= 1)
                elif self.aerosol_mode == "MODERATE":
                    mask |= (aerosol_level >= 2)
                elif self.aerosol_mode == "HIGH":
                    mask |= (aerosol_level == 3)

                # Mask the structural NoData fill value (255) only.
                # Negative reflectance and other physically implausible values
                # are intentionally passed through — they produce inf/nan in the
                # VI math above, which the valid-range filter in steps 04/05 masks.
                mask |= (fmask == 255)

                red[mask] = np.nan
                nir[mask] = np.nan
                
                # Calculate Indices
                ndvi, evi2, nirv = self.calculate_indices(red, nir)
                
                profile.update(dtype=rasterio.float32, nodata=np.nan, count=1, compress=GEOTIFF_COMPRESS)
                
                # Write outputs
                if "NDVI" in self.wanted_vis:
                    with rasterio.open(os.path.join(self.output_dir, f"{basename}.NDVI.tif"), 'w', **profile) as dst:
                        dst.write(ndvi, 1)
                    
                if "EVI2" in self.wanted_vis:
                    with rasterio.open(os.path.join(self.output_dir, f"{basename}.EVI2.tif"), 'w', **profile) as dst:
                        dst.write(evi2, 1)
                        
                if "NIRv" in self.wanted_vis:
                    with rasterio.open(os.path.join(self.output_dir, f"{basename}.NIRv.tif"), 'w', **profile) as dst:
                        dst.write(nirv, 1)

            return f"Processed: {basename}"
            
        except Exception as e:
            return f"Error processing {granule_info.get('basename', 'unknown')}: {str(e)}"

    def process_all_data_parallel(self, n_workers=4, chunk_size=1):
        l30_granules = self.find_granules(self.l30_dir, 'L30')
        s30_granules = self.find_granules(self.s30_dir, 'S30')
        all_granules = l30_granules + s30_granules
        
        print(f"Total granules found: {len(all_granules)}")
        
        if not all_granules:
            print("No granules found.")
            return

        print(f"Starting pool with {n_workers} workers...")
        with mp.Pool(processes=n_workers) as pool:
            results = pool.imap_unordered(self.process_granule_static, all_granules, chunksize=chunk_size)
            count = 0
            for res in results:
                count += 1
                if count % 10 == 0: print(f"[{count}/{len(all_granules)}] {res}")
                elif "Error"   in res: print(f"[ERROR]   {res}")
                elif "Skipped" in res: print(f"[SKIPPED] {res}")

if __name__ == "__main__":
    try: mp.set_start_method('fork', force=True)
    except RuntimeError: pass
    
    base_dir = os.environ.get("RAW_HLS_DIR", "")
    output_folder = os.environ.get("VI_OUTPUT_DIR", "")
    processed_vis = os.environ.get("PROCESSED_VIS", "NDVI EVI2 NIRv").split()
    
    if not base_dir or not output_folder:
        print("Error: Config not loaded.")
        exit(1)

    try:
        n_workers = int(os.environ.get("NUM_WORKERS", mp.cpu_count()))
        chunk_size = int(os.environ.get("CHUNK_SIZE", 1))
    except ValueError:
        n_workers = mp.cpu_count(); chunk_size = 1

    s30_folder = os.path.join(base_dir, "S30")
    l30_folder = os.path.join(base_dir, "L30")
    
    print(f"Processing VIs: {processed_vis}")
    print(f"Aerosol Mode: {os.environ.get('MASK_AEROSOL_MODE', 'HIGH')}")
    
    processor = HLSProcessor(s30_folder, l30_folder, output_folder, wanted_vis=processed_vis)
    processor.process_all_data_parallel(n_workers=n_workers, chunk_size=chunk_size)
    print("VI Processing Complete.")