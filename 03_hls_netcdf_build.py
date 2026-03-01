#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# 03_hls_netcdf_build.py
# Pipeline Step 03 (netcdf): Aggregate per-granule VI GeoTIFFs into per-tile
#   NetCDF time-series with CF-1.8 compliance and correct pixel-center coords.
#
# Author:  Stephen Conklin <stephenconklin@gmail.com>
#          https://github.com/stephenconklin
# Adapted from original code by G. Burch Fisher, PhD
# License: MIT

import os
import numpy as np
import netCDF4 as nc4
import rasterio
from rasterio.crs import CRS
import pandas as pd
from pathlib import Path
import multiprocessing as mp
import warnings
import glob
from hls_utils import get_configured_tiles

warnings.filterwarnings("ignore", category=rasterio.errors.NotGeoreferencedWarning)

def process_netcdf_chunk(chunk_info):
    # Worker function (Must be top-level)
    try:
        chunk_id = chunk_info['chunk_id']
        files = chunk_info['files']
        tile_id = chunk_info['tile_id']
        vi_type = chunk_info['vi_type']
        output_folder = Path(chunk_info['output_folder'])
        # x/y coords are passed as lists/arrays
        x_coords = chunk_info['x_coords']
        y_coords = chunk_info['y_coords']
        # CRS info
        crs_wkt = chunk_info.get('crs_wkt', "")
        
        # Dimensions
        height, width = len(y_coords), len(x_coords)
        
        # Filename logic
        if chunk_info.get('total_chunks', 1) == 1:
            output_filename = f"{tile_id}_{vi_type}.nc"
        else:
            output_filename = f"{tile_id}_{vi_type}_chunk{chunk_id:02d}.nc"
            
        output_path = output_folder / output_filename
        
        # Date processing
        dates = [f['date'] for f in files]
        # Convert to days since epoch
        time_values = [(d - pd.Timestamp('1970-01-01')).days for d in dates]
        
        with nc4.Dataset(output_path, 'w', format='NETCDF4') as nc:
            # Create Dimensions
            nc.createDimension('time', len(files))
            nc.createDimension('y', height)
            nc.createDimension('x', width)
            
            # Create Variables
            time_var = nc.createVariable('time', 'i4', ('time',))
            time_var[:] = time_values
            time_var.units = 'days since 1970-01-01'
            
            # Spatial Variables
            y_var = nc.createVariable('y', 'f8', ('y',))
            y_var[:] = y_coords
            y_var.units = 'meter' # Assumption for UTM
            
            x_var = nc.createVariable('x', 'f8', ('x',))
            x_var[:] = x_coords
            x_var.units = 'meter'

            # --- NEW: Grid Mapping Variable for Universal CRS ---
            # This is the standard CF-convention way to store CRS
            crs_var = nc.createVariable('spatial_ref', 'i4')
            crs_var.spatial_ref = crs_wkt
            # Add simple EPSG attribute if available
            # (You might need to parse this upstream if you want a clean EPSG code)
            
            # Data Variable
            vi_var = nc.createVariable(vi_type, 'f4', ('time', 'y', 'x'), zlib=True, complevel=chunk_info.get('complevel', 1), fill_value=np.nan)
            vi_var.grid_mapping = 'spatial_ref' # Link data to CRS variable
            
            sensor_var = nc.createVariable('sensor', 'S3', ('time',))
            
            for i, f_info in enumerate(files):
                try:
                    with rasterio.open(f_info['file_path']) as src:
                        data = src.read(1)
                        # Basic shape check
                        if data.shape == (height, width):
                             vi_var[i, :, :] = data
                        else:
                             # Handle edge case where a granule might have different extent (rare in HLS Tiled)
                             vi_var[i, :, :] = np.nan
                        sensor_var[i] = f_info['sensor']
                except:
                    vi_var[i, :, :] = np.nan
            
            # Global Attributes
            nc.title = f'HLS {vi_type} Tile {tile_id}'
            # Also write CRS to global attribute for redundancy
            nc.crs = crs_wkt
            
        return f"✓ Chunk {chunk_id}: {output_filename}"
    except Exception as e:
        return f"✗ Chunk {chunk_id}: {str(e)}"

class HLSNetCDFAggregator:
    def __init__(self, input_folder, output_folder, wanted_vis=None):
        self.input_folder = Path(input_folder)
        self.output_folder = Path(output_folder)
        self.output_folder.mkdir(parents=True, exist_ok=True)
        self.vegetation_indices = wanted_vis if wanted_vis else ['NDVI', 'EVI2', 'NIRv']
        self.netcdf_complevel = int(os.environ.get("NETCDF_COMPLEVEL", 1))
        
    def extract_metadata_from_filename(self, filename):
        name = filename.name
        # Expecting: HLS.L30.T18TVL.2020081T154931.v2.0.NDVI.tif
        try:
            parts = name.split('.')
            if len(parts) >= 7:
                sensor = parts[1] # L30 or S30
                tile_id = parts[2] # T18TVL
                date_str = parts[3] # 2020081T...
                
                # Check if the VI is in the filename (usually near the end)
                # The generic structure might put VI at index 6 or -2
                vi_candidate = parts[-2]
                
                if vi_candidate in self.vegetation_indices:
                     vi_type = vi_candidate
                else:
                     return None, None, None, None

                # Parse Date
                year = int(date_str[:4])
                doy = int(date_str[4:7])
                date = pd.to_datetime(f"{year}-{doy:03d}", format="%Y-%j")
                
                return sensor, tile_id, date, vi_type
        except: 
            pass
        return None, None, None, None

    def merge_chunks(self, tile_id, vi_type, chunk_files):
        print(f"    Merging {len(chunk_files)} chunks for {tile_id} {vi_type}...")
        
        merged_file = self.output_folder / f"{tile_id}_{vi_type}.nc"

        # Clean up chunk files in a finally block so they are always
        # removed — even if the merge fails — preventing stale chunks
        # from confusing a subsequent re-run.
        try:
            # Read metadata from the first chunk
            with nc4.Dataset(chunk_files[0], 'r') as src:
                y_coords = src.variables['y'][:]
                x_coords = src.variables['x'][:]
                crs_wkt = src.getncattr('crs') if 'crs' in src.ncattrs() else ""
                # Check for spatial_ref variable
                has_spatial_ref = 'spatial_ref' in src.variables

            # Calculate total time dimension
            total_time = 0
            for cf in chunk_files:
                 with nc4.Dataset(cf, 'r') as src:
                     total_time += len(src.dimensions['time'])
            
            with nc4.Dataset(merged_file, 'w', format='NETCDF4') as dst:
                dst.createDimension('time', total_time)
                dst.createDimension('y', len(y_coords))
                dst.createDimension('x', len(x_coords))
                
                t_var = dst.createVariable('time', 'i4', ('time',))
                t_var.units = 'days since 1970-01-01'
                
                dst.createVariable('y', 'f8', ('y',))[:] = y_coords
                dst.createVariable('x', 'f8', ('x',))[:] = x_coords

                # Re-create spatial_ref
                if has_spatial_ref:
                    crs_var = dst.createVariable('spatial_ref', 'i4')
                    crs_var.spatial_ref = crs_wkt

                vi_var = dst.createVariable(vi_type, 'f4', ('time', 'y', 'x'), zlib=True, complevel=self.netcdf_complevel)
                if has_spatial_ref: vi_var.grid_mapping = 'spatial_ref'
                
                s_var = dst.createVariable('sensor', 'S3', ('time',))
                
                # copy data
                current_t = 0
                for cf in chunk_files:
                    with nc4.Dataset(cf, 'r') as src:
                        t_len = len(src.dimensions['time'])
                        t_var[current_t:current_t+t_len] = src.variables['time'][:]
                        vi_var[current_t:current_t+t_len,:,:] = src.variables[vi_type][:]
                        s_var[current_t:current_t+t_len] = src.variables['sensor'][:]
                        current_t += t_len
                
                dst.title = f'HLS {vi_type} {tile_id}'
                dst.crs = crs_wkt
            
            print(f"    ✓ Merged file created: {merged_file.name}")

        except Exception as e:
            print(f"    ✗ Merge failed: {e}")

        finally:
            # Always clean up chunk files, regardless of merge success/failure
            for cf in chunk_files:
                try: os.remove(cf)
                except: pass

    def collect_files(self):
        file_org = {}
        print("Scanning for files recursively...")
        # RECURSIVE SEARCH matches your new directory structure
        tif_files = list(self.input_folder.glob("**/*.tif"))
        print(f"Found {len(tif_files)} total .tif files.")
        
        for f in tif_files:
            sensor, tile_id, date, vi_type = self.extract_metadata_from_filename(f)
            
            if tile_id and date and vi_type:
                bare = tile_id[1:] if tile_id.startswith('T') else tile_id
                configured = get_configured_tiles()
                if configured and bare not in configured:
                    continue
                if tile_id not in file_org: file_org[tile_id] = {}
                if vi_type not in file_org[tile_id]: file_org[tile_id][vi_type] = []
                
                file_org[tile_id][vi_type].append({
                    'file_path': str(f), 
                    'sensor': sensor, 
                    'date': date, 
                    'tile_id': tile_id, 
                    'vi_type': vi_type
                })
        
        # Sort by date
        for t in file_org:
            for v in file_org[t]:
                file_org[t][v].sort(key=lambda x: x['date'])
        return file_org

    def run(self, chunk_size=10, n_workers=4):
        file_org = self.collect_files()
        if not file_org: 
            print("No matching files found.")
            return

        for tile_id in sorted(file_org.keys()):
            print(f"\nProcessing Tile: {tile_id}")
            for vi_type in sorted(file_org[tile_id].keys()):
                files = file_org[tile_id][vi_type]
                print(f"  {vi_type}: {len(files)} files")
                
                # Get Spatial Ref from the first file
                try:
                    with rasterio.open(files[0]['file_path']) as src:
                        transform = src.transform
                        crs = src.crs
                        shape = src.shape
                        crs_wkt = crs.to_wkt()
                        width = src.width
                        height = src.height
                        
                        # Pixel-center coordinates.
                        # transform.c / transform.f are the TOP-LEFT CORNER of pixel (0,0).
                        # Center of pixel i = corner + pixel_size * (i + 0.5)
                        x_coords = transform.c + transform.a * (np.arange(width)  + 0.5)
                        y_coords = transform.f + transform.e * (np.arange(height) + 0.5)
                    
                    # Create Chunks
                    chunks = []
                    total_chunks = (len(files) + chunk_size - 1) // chunk_size
                    
                    for i, start_idx in enumerate(range(0, len(files), chunk_size)):
                        chunk_files = files[start_idx : start_idx + chunk_size]
                        chunks.append({
                            'chunk_id': i + 1,
                            'total_chunks': total_chunks,
                            'files': chunk_files,
                            'tile_id': tile_id,
                            'vi_type': vi_type,
                            'output_folder': str(self.output_folder),
                            'x_coords': x_coords,
                            'y_coords': y_coords,
                            'crs_wkt': crs_wkt,
                            'shape': shape,
                            'complevel': self.netcdf_complevel,
                        })
                    
                    # Process Chunks in Parallel
                    with mp.Pool(n_workers) as pool:
                        results = pool.map(process_netcdf_chunk, chunks)
                    
                    for r in results: print(f"    {r}")
                    
                    # Merge if necessary
                    if total_chunks > 1:
                        # Reconstruct expected chunk filenames
                        expected_chunks = []
                        for i in range(1, total_chunks + 1):
                            expected_chunks.append(self.output_folder / f"{tile_id}_{vi_type}_chunk{i:02d}.nc")
                        
                        self.merge_chunks(tile_id, vi_type, expected_chunks)
                        
                except Exception as e:
                    print(f"  Error preparing tile {tile_id}: {e}")

if __name__ == "__main__":
    # --- CONFIGURATION FROM ENV ---
    input_folder = os.environ.get("VI_OUTPUT_DIR", "")
    netcdf_output_folder = os.environ.get("NETCDF_DIR", "")
    processed_vis = os.environ.get("PROCESSED_VIS", "NDVI EVI2 NIRv").split()
    
    # Load worker counts from env or default to 4
    try: 
        n_workers = int(os.environ.get("NUM_WORKERS", 4))
        chunk_size = int(os.environ.get("CHUNK_SIZE", 10))
    except ValueError: 
        n_workers = 4
        chunk_size = 10

    if not input_folder or not netcdf_output_folder:
        print("Error: Config not loaded. Ensure VI_OUTPUT_DIR and NETCDF_DIR are set.")
        exit(1)

    try:
        netcdf_complevel = max(0, min(9, int(os.environ.get("NETCDF_COMPLEVEL", 1))))
    except ValueError:
        netcdf_complevel = 1

    print(f"HLS NetCDF Aggregation (Indices: {processed_vis})")
    print(f"Workers: {n_workers} | Chunk Size: {chunk_size} | NetCDF complevel: {netcdf_complevel}")
    
    aggregator = HLSNetCDFAggregator(input_folder, netcdf_output_folder, wanted_vis=processed_vis)
    aggregator.run(chunk_size=chunk_size, n_workers=n_workers)
    print("NetCDF Creation Complete.")