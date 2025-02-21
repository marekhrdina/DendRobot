#imports #tweak
import alphashape, base64,cc3d, ctypes, fiona, gc, geopandas as gpd, h5py, inspect,joblib , laspy, logging, math, multiprocessing as mp, numpy as np, open3d as o3d, os, pandas as pd, psutil, pyvista as pv, platform, rasterio, shutil, subprocess, sys, tempfile, tkinter as tk, threading, time, traceback

from functools import wraps
from matplotlib import Path
from multiprocessing import Pool, cpu_count, shared_memory, Array, Lock
from io import BytesIO
from joblib import Parallel, delayed
from PIL import Image, ImageTk 
from pyproj import CRS
from rasterio.enums import Resampling
from rasterio.features import shapes
from rasterio.transform import from_origin, Affine
from rasterio.warp import Resampling
from scipy.ndimage import gaussian_filter
from scipy.spatial import cKDTree, Delaunay, ConvexHull
from shapely.geometry import MultiPolygon, Point, Polygon, shape, mapping, box
from shapely.prepared import prep
from shapely.wkb import dumps as wkb_dumps, loads as wkb_loads
from skimage.segmentation import watershed
from skimage.feature import peak_local_max
from sklearn.neighbors import NearestNeighbors
from tkinter import filedialog, BooleanVar, font, messagebox, ttk, TclError

#global variables #tweak
array_shape = None
discsall = []
globalprocessing = False
pause_event = threading.Event()
pause_event.set()
repopulated_trees = None
shared_densecloud = None
shared_pointcloud = None
shared_visited = None
shiftby = None
tree = None

###Custom###
def create_shared_memory(array):
    """Create shared memory from a NumPy array."""
    #global shared_pointcloud, array_shape

    array_shape = array.shape
    shared_mem = shared_memory.SharedMemory(create=True, size=array.nbytes)
    shared_pointcloud = np.ndarray(array_shape, dtype=array.dtype, buffer=shared_mem.buf)
    np.copyto(shared_pointcloud, array)
    return shared_mem

def create_shared_memory2(array):
    """Create shared memory from a NumPy array."""
    shared_mem = shared_memory.SharedMemory(create=True, size=array.nbytes)
    shared_array = np.ndarray(array.shape, dtype=array.dtype, buffer=shared_mem.buf)
    np.copyto(shared_array, array)  # Copy data into shared memory
    return shared_mem, array.shape, array.dtype

def filter_and_transform(grouped_data, max_d = 2):
    """
    Filters and transforms the processed data to extract unique ID_TREE information
    and create a new structured array without duplicates.

    Parameters:
        grouped_data (dict): Dictionary where keys are group values and values are numpy arrays.

    Returns:
        np.ndarray: A structured numpy array containing the unique extracted information.
    """
    filtered_data = []

    for group_value, array in grouped_data.items():
        # Assuming ID_TREE is at index -7 and TREE_H at index -6
        id_tree_index = -7
        tree_height_index = -6
        disc_h_index = -5
        disc_x_index = -4
        disc_y_index = -3
        disc_d_index = -2
        disc_error_index = -1

        # Get unique ID_TREE values
        unique_id_trees = np.unique(array[:, id_tree_index])

        for id_tree in unique_id_trees:
            # Filter rows for this ID_TREE
            id_tree_rows = array[array[:, id_tree_index] == id_tree]

            disc_d_values = id_tree_rows[:, disc_d_index].astype(np.float32)
            
            # If any disc_d is negative or larger than max_d, skip this tree entirely.
            if np.any(disc_d_values <= 0) or np.any(disc_d_values > max_d):
                continue


            # Extract tree height (from the first row for this ID_TREE)
            tree_height = id_tree_rows[0, tree_height_index]

            # Remove duplicates based on the last 5 columns
            unique_discs, unique_indices = np.unique(
                id_tree_rows[:, disc_h_index:], axis=0, return_index=True
            )

            for idx in unique_indices:
                row = id_tree_rows[idx]
                filtered_data.append({
                    "ID_TREE": id_tree.astype(np.int16),              # ID_TREE
                    "DISC_X": row[disc_x_index].astype(np.float64),     # DISC_X
                    "DISC_Y": row[disc_y_index].astype(np.float64),     # DISC_Y
                    "DISC_H": round(row[disc_h_index].astype(np.float64), 2),     # DISC_H
                    "DISC_D": (row[disc_d_index] * 2).astype(np.float64), # DISC_D
                    "TREE_H": tree_height.astype(np.float32),           # TREE_H
                    "DISC_ERROR": row[disc_error_index].astype(np.float32)  # DISC_ERROR
                })

        # Convert to a DataFrame
    filtered_df = pd.DataFrame(filtered_data)

    # Remove duplicates
    filtered_df = filtered_df.drop_duplicates()

    return filtered_df

def filter_disc_height(df, target_height=1.3, dbhlim = 2):
    """
    Filters rows with DISC_H == target_height or approximates values for missing DISC_H.

    Parameters:
        df (pd.DataFrame): The input DataFrame with required fields.
        target_height (float): The target DISC_H value to filter or approximate (default is 1.3).

    Returns:
        pd.DataFrame: A DataFrame with rows filtered or approximated for DISC_H == target_height.
    """
    if not {'ID_TREE', 'DISC_X', 'DISC_Y', 'DISC_H', 'DISC_D'}.issubset(df.columns):
        raise ValueError("The input DataFrame must contain 'ID_TREE', 'DISC_X', 'DISC_Y', 'DISC_H', and 'DISC_D' columns.")

    # Separate DataFrame into those with and without the target DISC_H
    with_target = df[df['DISC_H'] == target_height]
    without_target = df[df['DISC_H'] != target_height]

    # Get IDs of trees that already have DISC_H == target_height
    existing_trees = set(with_target['ID_TREE'])

    # Process trees missing the target DISC_H
    missing_target_trees = without_target[~without_target['ID_TREE'].isin(existing_trees)].groupby('ID_TREE')
    new_records = []

    for id_tree, group in missing_target_trees:
        if len(group) >= 2:
            # Calculate average DISC_X and DISC_Y
            avg_disc_x = group['DISC_X'].mean()
            avg_disc_y = group['DISC_Y'].mean()

            # Fit a linear model for DISC_D against DISC_H
            slope, intercept = np.polyfit(group['DISC_H'], group['DISC_D'], 1)

            # Predict DISC_D for the target height
            predicted_disc_d = slope * target_height + intercept

            if predicted_disc_d <= 0 or predicted_disc_d > dbhlim:
                continue

            # Create a new record
            new_record = {
                'ID_TREE': id_tree,
                'DISC_X': avg_disc_x,
                'DISC_Y': avg_disc_y,
                'DISC_H': target_height,
                'DISC_D': predicted_disc_d,
                'TREE_H': group['TREE_H'].iloc[0],  # Assuming TREE_H is constant per ID_TREE
                'DISC_ERROR': 999  # Using average DISC_ERROR
            }
            new_records.append(new_record)

    # Combine the original with_target DataFrame and the new records
    new_records_df = pd.DataFrame(new_records)
    result = pd.concat([with_target, new_records_df], ignore_index=True)

    return result

def initial_cleanup(pointcloudpath, debug, reevaluate, keepfields = "xyz"):
    folder = os.path.dirname(pointcloudpath)
    filename = os.path.splitext(os.path.basename(pointcloudpath))[0]
    processingfolder = os.path.join(folder, f"{filename}-Processing")
    reprocessingfolder = os.path.join(folder, f"{filename}-Processing-reevaluate")
    if reevaluate == False:
        if os.path.exists(processingfolder):
            if debug:
                print(f"[{TimeNow()}] {inspect.currentframe().f_code.co_name}: Deleting existing processing folder: {processingfolder}")
            shutil.rmtree(processingfolder)
        os.makedirs(processingfolder)
        if debug:
            print(f"[{TimeNow()}] {inspect.currentframe().f_code.co_name}: Created processing folder: {processingfolder}")
        
        cloud = LoadPointCloud(pointcloudpath, fields=keepfields)
        return cloud, processingfolder
    
    elif reevaluate == True:

        if os.path.exists(reprocessingfolder):
            if debug:
                print(f"[{TimeNow()}] {inspect.currentframe().f_code.co_name}: Deleting existing reprocessing folder: {reprocessingfolder}")
            shutil.rmtree(reprocessingfolder)
        os.makedirs(reprocessingfolder)
        processing_files = os.listdir(processingfolder)
        for f in processing_files:
            if "_TreeCrowns." in f:
                f_path = os.path.join(processingfolder, f)
                copy_path = os.path.join(reprocessingfolder, f"TreeCrowns{os.path.splitext(f)[1]}")
                shutil.copy2(f_path, copy_path)
        if debug:
            print(f"[{TimeNow()}] {inspect.currentframe().f_code.co_name}: Created reprocessing folder: {reprocessingfolder}")

        pointcloudpath = os.path.join(os.path.dirname(pointcloudpath), f"{os.path.splitext(os.path.basename(pointcloudpath))[0]}-Processing", f"{os.path.splitext(os.path.basename(pointcloudpath))[0]}_cloud_density.txt")

        try:
            cloud = LoadPointCloud(pointcloudpath)
        except:
            pointcloudpath = f"{os.path.join(os.path.dirname(pointcloudpath), f'{os.path.splitext(os.path.basename(pointcloudpath))[0]}-Processing', '13ComputeDensity-CloudDensity.txt')}"

        return cloud, reprocessingfolder

def init_shared_array(shared_array, shape):
    """Initialize the global shared memory array for all workers."""
    global shared_pointcloud
    shared_pointcloud = shared_array
    global array_shape
    array_shape = shape

def process_discsall(discsall, output_dir, debug = False, shiftby = [0,0,0]):
    """
    Process and export grouped features from a list of point clouds, 
    and return the contents as a dictionary.

    Parameters:
        discsall (list): List of np arrays representing discs.
        output_dir (str): Root directory for saving outputs.

    Returns:
        dict: A dictionary where keys are the group values and values are the corresponding DataFrames.
    """
    def group_and_export(data, output_dir, column_index=-5, debug=False):
        """
        Groups data based on unique values in a specified column, exports each group to a text file,
        and stores each group in a dictionary.

        Parameters:
            data (pd.DataFrame): Input point cloud data as a pandas DataFrame.
            output_dir (str): Directory to save the output text files.
            column_index (int): Column index to group by (default: -1).

        Returns:
            dict: A dictionary of grouped DataFrames.
        """
        # Ensure output directory exists
        os.makedirs(output_dir, exist_ok=True)

        # Get the column name using the index
        grouping_column = data[:, column_index]
        unique_values = np.unique(grouping_column)

        print(f"[{TimeNow()}] {inspect.currentframe().f_code.co_name}: Found {len(unique_values)} unique groups in column index {column_index}.")

        # Dictionary to store the contents of each group
        grouped_contents = {}

        for value in unique_values:
            group = data[grouping_column == value]
            grouped_contents[value] = group

        # Iterate through each group and export to a file
            if debug == "Nothing": #True
                output_file = os.path.join(output_dir, f"grouped_{round(value,2)}.txt")
                SavePointCloud(group, output_file, shiftby=shiftby)
                print(f"[{TimeNow()}] {inspect.currentframe().f_code.co_name}: Exported {len(group)} rows to {output_file}.")

        return grouped_contents

        
    def flatten_list(discs):
        # Filter out invalid or empty DataFrames
        discs = [disc for disc in discs if isinstance(disc, pd.DataFrame) and not disc.empty]

        if not discs:
            print("No valid discs to concatenate.")
            return None  # Handle empty case as needed

        # Concatenate valid DataFrames
        else: 
            discs = np.vstack(discs)
            return discs

    # Ensure the main output directory exists
    os.makedirs(output_dir, exist_ok=True)



    flat_discsall = flatten_list(discsall)

    combined_data = flat_discsall
    if debug == True:
        SavePointCloud(combined_data, os.path.join(output_dir, "StemDiscsProcessed.txt"), shiftby=shiftby)

    # Call the grouping and export function and collect grouped contents
    grouped_data_contents = group_and_export(combined_data, output_dir, column_index=-5, debug=debug)

    return grouped_data_contents


def worker_ignore():
    import warnings
    warnings.filterwarnings("ignore")

def process_single_tree(label, shared_memory_name_trees, shared_shape_trees, shared_dtype_trees,
                        shiftby, dtmmesh, debugdir, disc_heights, XSectionThickness, 
                        CCfinestep, ptsfilter, RANSACn, RANSACd, shared_memory_name_densecloud, 
                        shared_shape_densecloud, shared_dtype_densecloud):
    """
    Processes a single tree identified by its label using shared memory.
    """
    existing_shm_trees = shared_memory.SharedMemory(name=shared_memory_name_trees)
    existing_shm_densecloud = shared_memory.SharedMemory(name=shared_memory_name_densecloud)
    
    repopulated_trees = np.ndarray(shared_shape_trees, dtype=shared_dtype_trees, buffer=existing_shm_trees.buf)
    shared_densecloud = np.ndarray(shared_shape_densecloud, dtype=shared_dtype_densecloud, buffer=existing_shm_densecloud.buf)

    temp_file_path_DIST = None  
    temp_discs_path = None  
    
    # Extract tree points
    try:
        treecloud = repopulated_trees[repopulated_trees[:, -1] == label]
        del repopulated_trees
        if len(treecloud) == 0:
            print(f"[process_single_tree] Warning: No points found for label {label}.")
            return [], None, None  
    except Exception as e:
        print(f"[process_single_tree] ERROR: Failed to extract treecloud for label {label}: {e}")
        return [], None, None  

    # Connected component analysis
    try:
        refined_sub_cloud = LabelConnectedComponents(input_data=treecloud, voxel_size=0.4, min_points=10)
        del treecloud
        if len(refined_sub_cloud) == 0:
            print(f"[process_single_tree] Warning: No subcomponent found for label {label}.")
            return [], None, None  
    except Exception as e:
        print(f"[process_single_tree] ERROR: Connected component analysis failed for label {label}: {e}")
        return [], None, None  

    # Identify lowest part of the tree
    try:
        lowest_z_index = np.argmin(refined_sub_cloud[:, 2])
        lowest_z_label = refined_sub_cloud[lowest_z_index, -1]
        low_component = refined_sub_cloud[refined_sub_cloud[:, -1] == lowest_z_label]
        del refined_sub_cloud
    except Exception as e:
        print(f"[process_single_tree] ERROR: Failed to identify lowest part of tree for label {label}: {e}")
        return [], None, None  

    # Compute vertical distances
    try:
        distances = CloudToMeshVerticalDistance(
            low_component, dtmmesh, outputdir=None, shiftby=shiftby,
            max_dist=(max(disc_heights) + 0.5 * XSectionThickness) + 0.01
        )
        del low_component
        if distances is None or distances.empty:
            print(f"[process_single_tree] Warning: Distance computation failed for label {label}.")
            return [], None, None  
    except Exception as e:
        print(f"[process_single_tree] ERROR: Distance computation failed for label {label}: {e}")
        return [], None, None  

    max_z_index = distances.iloc[:, 2].idxmax()
    min_z_index = distances.iloc[:, 2].idxmin()

    # Extract the full XYZ coordinates for these two points
    point_max = distances.iloc[max_z_index, :].to_numpy()
    point_min = distances.iloc[min_z_index, :].to_numpy()

    # Compute the Euclidean distance between the two points
    distance = np.linalg.norm(point_max - point_min)

    # Round the result to two decimal places and convert to np.float32
    tree_height = np.float32(round(distance, 2))
    #Apply `shiftby` to CloudTerrainDistances
    try:


        # Create per-process temp files for later merging if debugging
        if debugdir:
            os.makedirs(debugdir, exist_ok=True)

            temp_file_DIST = tempfile.NamedTemporaryFile(delete=False, mode="w", dir=debugdir, suffix=".txt")
            temp_file_path_DIST = temp_file_DIST.name
            temp_file_DIST.close()

            distances_np = distances.to_numpy().astype(np.float64)
            shiftby = np.array(shiftby, dtype=np.float64)
            distances_np[:, :3] += shiftby  
            with open(temp_file_path_DIST, "a") as f:
                np.savetxt(f, distances_np, fmt="%.6f")  
            del distances_np
    except Exception as e:
        print(f"[process_single_tree] ERROR: Failed to create debug files for label {label}: {e}")

    # Extract tree cross-sections (discs)
    discs = []
    for h in disc_heights:
        try:
            disc = FilterByValue(distances, -1, h - 0.5 * XSectionThickness, h + 0.5 * XSectionThickness)
            disc = LoadPointCloud(disc, "np")

            # Crop dense cloud
            cropped_points = crop_dense_cloud_with_obb(shared_densecloud, disc)
            disc = LoadPointCloud(cropped_points, "np", "xyz")
            del cropped_points

            if disc.shape[0] > 0:
                disc = AddConstantField(disc, label, "tree_id", np.int16)
                disc = AddConstantField(disc, tree_height, "tree_height", np.float16)
                disc = AddConstantField(disc, h, "height_level")
                discs.append(disc)
        except Exception as e:
            print(f"[process_single_tree] ERROR: Failed to process disc at height {h} for label {label}: {e}")
            continue  
    del distances
    if debugdir:
        os.makedirs(debugdir, exist_ok=True)

        temp_discs_file = tempfile.NamedTemporaryFile(delete=False, mode="w", dir=debugdir, suffix="_discs.txt")
        temp_discs_path = temp_discs_file.name
        temp_discs_file.close()
        
        if discs:
            discs_np = np.vstack(discs)
            discs_np = discs_np.astype(np.float64)
            shiftby = np.array(shiftby, dtype=np.float64)
            discs_np[:, :3] += shiftby  

            with open(temp_discs_path, "a") as f:
                np.savetxt(f, discs_np, fmt="%.6f")  
            del discs_np


    # --- Post-processing each disc ---
    for i, disc in enumerate(discs):
        if disc is None or disc.shape[0] == 0:
            continue
        try:
            disc = SORFilter(disc, npoints=12, sd=1)
            disc = ComputeVerticality(disc, radius=0.05)
            disc = FilterByValue(disc, -1, 0.4, 1)
            disc = RemoveField(disc, -1)
            
            disc = LabelConnectedComponents(disc, voxel_size=CCfinestep, min_points=ptsfilter, keep_indices=1)
            disc = RemoveField(disc, -1)

            # Fit circle
            x_center, y_center, radius, error = FitCircleRANSAC(disc, n=RANSACn, d=RANSACd)

            disc = AddConstantField(disc, x_center, "x_center", np.float64)
            disc = AddConstantField(disc, y_center, "y_center", np.float64)
            disc = AddConstantField(disc, radius, "radius", np.float16)
            disc = AddConstantField(disc, error, "error", np.float16)

        except Exception as e:
            disc = None
            print(f"[{TimeNow()}] Failed to fit circle to disc for label {label}: {e}. Disc removed.")

        discs[i] = disc.astype(shared_dtype_densecloud)
    existing_shm_trees.close()
    existing_shm_densecloud.close()
    

    return discs, temp_file_path_DIST, temp_discs_path  

def process_trees_parallel(repopulated_trees, shiftby, unique_labels, dtmmesh, debugdir, disc_heights, 
                           XSectionThickness, folder, RANSACn, RANSACd, CCfinestep, ptsfilter, debug, 
                           shared_densecloud, cpus_to_leave_free=1): 
    """
    Runs process_trees in parallel and merges debug outputs from per-process temp files.
    """
    # import warnings
    # warnings.filterwarnings("ignore")
    try:
        # Create shared memory for `repopulated_trees`
        shared_trees = shared_memory.SharedMemory(create=True, size=repopulated_trees.nbytes)
        np_shared_trees = np.ndarray(repopulated_trees.shape, dtype=repopulated_trees.dtype, buffer=shared_trees.buf)
        np.copyto(np_shared_trees, repopulated_trees)

        # Create shared memory for `shared_densecloud`
        shared_dense = shared_memory.SharedMemory(create=True, size=shared_densecloud.nbytes)
        np_shared_densecloud = np.ndarray(shared_densecloud.shape, dtype=shared_densecloud.dtype, buffer=shared_dense.buf)
        np.copyto(np_shared_densecloud, shared_densecloud)

        shared_shape_trees = repopulated_trees.shape
        shared_dtype_trees = repopulated_trees.dtype
        shared_shape_densecloud = shared_densecloud.shape
        shared_dtype_densecloud = shared_densecloud.dtype

        temp_files = []  
        temp_discs_files = []  
        all_discs = []   

        with mp.Pool(processes=os.cpu_count() - cpus_to_leave_free) as pool: #os.cpu_count() - cpus_to_leave_free initializer=worker_ignore
            results = pool.starmap(
                process_single_tree,
                [(label, shared_trees.name, shared_shape_trees, shared_dtype_trees, 
                  shiftby, dtmmesh, debugdir, disc_heights, XSectionThickness, CCfinestep, ptsfilter, 
                  RANSACn, RANSACd, shared_dense.name, shared_shape_densecloud, shared_dtype_densecloud)
                for label in unique_labels]
            )
 
        #results = results.get()

        for result in results:
            if result is None or not isinstance(result, tuple) or len(result) != 3:
                print(f"[process_trees_parallel] ERROR: Unexpected return format: {result}")
                continue  

            discs, temp_file, temp_discs_file = result
            if isinstance(discs, list):
                all_discs.extend(discs)
            if isinstance(temp_file, str):  
                temp_files.append(temp_file)
            if isinstance(temp_discs_file, str):  
                temp_discs_files.append(temp_discs_file)

        # 🟢 Merge per-process debug files
        final_debug_file = os.path.join(debugdir, "CloudTerrainDistances.txt")
        with open(final_debug_file, "w") as outfile:
            for temp_file in temp_files:
                with open(temp_file, "r") as infile:
                    shutil.copyfileobj(infile, outfile)

        final_discs_file = os.path.join(debugdir, "StemDiscsUnprocessed.txt")
        with open(final_discs_file, "w") as outfile:
            for temp_discs_file in temp_discs_files:
                with open(temp_discs_file, "r") as infile:
                    shutil.copyfileobj(infile, outfile)

        for temp_file in temp_files:
            os.remove(temp_file)
        for temp_discs_file in temp_discs_files:
            os.remove(temp_discs_file)

        return all_discs

    finally:
        shared_trees.close()
        shared_trees.unlink()
        shared_dense.close()
        shared_dense.unlink()


def process_trees(repopulated_trees, shiftby, unique_labels, dtmmesh, debugdir, disc_heights, XSectionThickness, folder, RANSACn, RANSACd, CCfinestep, ptsfilter, debug): #works on single cpu core only
    discsall = []
    global shared_densecloud
    dense_pcd = o3d.geometry.PointCloud()
    dense_pcd.points = o3d.utility.Vector3dVector(shared_densecloud)
    del shared_densecloud
    for label in unique_labels:
        check_stop() 
        try:
            # Access the tree subset
            treecloud = repopulated_trees[repopulated_trees[:, -1] == label]
            refined_sub_cloud = LabelConnectedComponents(input_data=treecloud, voxel_size=0.4, min_points=10)
            del treecloud
            if len(refined_sub_cloud) == 0:
                print(f"No subcomponent found for label {label}.")
                continue

            # Find the lowest Z-value component
            lowest_z_index = np.argmin(refined_sub_cloud[:, 2])
            lowest_z_label = refined_sub_cloud[lowest_z_index, -1]
            low_component = refined_sub_cloud[refined_sub_cloud[:, -1] == lowest_z_label]
            del refined_sub_cloud
            # Compute distances to mesh
            distances = CloudToMeshVerticalDistance(low_component, dtmmesh, outputdir=debugdir, shiftby=shiftby, max_dist = (max(disc_heights)+0.5*XSectionThickness)+0.01)
            del low_component

             # Optionally write out the distance information for debugging.
            if debugdir is not None and distances is not None:
                output_file = os.path.join(debugdir, "CloudTerrainDistances.txt")
                os.makedirs(debugdir, exist_ok=True)
                distances_np = distances.to_numpy().astype(np.float64)
                shiftby_arr = np.array(shiftby, dtype=np.float64)
                distances_np[:, :3] += shiftby_arr
                with open(output_file, "a") as f:
                    np.savetxt(f, distances_np, fmt="%.6f")
                del distances_np

            max_z_index = distances.iloc[:, 2].idxmax()
            min_z_index = distances.iloc[:, 2].idxmin()

            # Extract the full XYZ coordinates for these two points
            point_max = distances.iloc[max_z_index, :].to_numpy()
            point_min = distances.iloc[min_z_index, :].to_numpy()

            # Compute the Euclidean distance between the two points
            distance = np.linalg.norm(point_max - point_min)

            # Round the result to two decimal places and convert to np.float32
            tree_height = np.float32(round(distance, 2))
            
   

            # Generate discs
            for h in disc_heights:
                try:
                    disc = FilterByValue(distances, -1, h - 0.5 * XSectionThickness, h + 0.5 * XSectionThickness, outputdir=None, shiftby=shiftby)
                    disc = LoadPointCloud(disc, "np")
                    # Crop the dense cloud using the oriented bounding box from the disc.
                    disc = crop_dense_cloud_with_obb2(dense_pcd, disc)
                    disc = LoadPointCloud(disc, "np", "xyz")

                except Exception:
                    disc = np.array([])

                if disc.shape[0] > 0:
                    disc = AddConstantField(disc, label, "tree_id")
                    disc = AddConstantField(disc, tree_height, "tree_height")
                    disc = AddConstantField(disc, round(h, 2), "height_level")
                    discsall.append(disc)
 
        except Exception as e:
            print( f"Error processing label {label}: {e}")
            continue
        del distances

    if debug == True:
        merged = np.vstack(discsall)
        SavePointCloud(merged, os.path.join(folder, f"StemDiscsUnprocessed.txt"), shiftby=shiftby)
        del merged

    #### split here
    for i, disc in enumerate(discsall):
        check_stop() 
        if disc is None or disc.shape[0] == 0:
            print(f"[{TimeNow()}] {inspect.currentframe().f_code.co_name}: Skipping empty or invalid disc.")
            continue  # Skip empty discs or invalid data
        try:
            ##
            disc = SORFilter(disc, npoints=12, sd=1)
            #density = CalculateAvgDensity(disc, 0.1, 12, 10)
            disc = ComputeVerticality(disc, radius = 0.05) ### BY DENSITY
            disc = FilterByValue(disc, -1, 0.4, 1)
            disc = RemoveField(disc, -1)
            disc = LabelConnectedComponents(disc, voxel_size=CCfinestep, min_points=ptsfilter, keep_indices=1)
            disc = RemoveField(disc, -1)

            # Attempt to fit a circle to the disc
            x_center, y_center, radius, error = FitCircleRANSAC(disc, n=RANSACn, d=RANSACd)  # Returns (x_center, y_center, radius, error)

            # Add new fields for the circle fit results
            disc = AddConstantField(disc, x_center, field_name="x_center") #WARNING too memory inefficient
            disc = AddConstantField(disc, y_center, field_name="y_center") #WARNING too memory inefficient
            disc = AddConstantField(disc, radius, field_name="radius") #WARNING too memory inefficient
            disc = AddConstantField(disc, error, field_name="error") #WARNING too memory inefficient
        except Exception as e:
            # Handle errors by deleting erroneous discs
            disc = None
            print(f"[{TimeNow()}] {inspect.currentframe().f_code.co_name}: Failed to fit circle to disc: {e}. Disc removed.")
            
        # Save the updated disc back to the original list
        discsall[i] = disc

    return discsall

def rename_files_in_directory(pcdpath):
    main_folder_path = os.path.dirname(pcdpath)
    main_file_name = os.path.splitext(os.path.basename(pcdpath))[0]  # Get the main file name

    for root, dirs, files in os.walk(main_folder_path):
        # Check if the current folder contains the main file name
        if f"{main_file_name}-Processing" in os.path.basename(root) or f"{main_file_name}-Processing-reevaluate" in os.path.basename(root):
            # Process each file in the directory
            for file in files:
                # Skip files that already contain the main file name
                if main_file_name in file:
                    continue

                # Get the full file path
                old_file_path = os.path.join(root, file)

                # Create the new file name
                new_file_name = f"{main_file_name}_{file}"

                # Get the new file path
                new_file_path = os.path.join(root, new_file_name)

                # Rename the file
                os.rename(old_file_path, new_file_path)
                # Uncomment the line below for debugging
                # print(f"Renamed: {old_file_path} to {new_file_path}")

def repopulate_pointcloud(filterdensity, densecloud, cpus_to_leave_free=1):
    unique_indices = np.unique(filterdensity[:, -1])
    num_workers = max(cpu_count() - cpus_to_leave_free, 1)
    global_bbox = GetBoundingBox(densecloud) 
    # Create shared memory for densecloud and filterdensity
    densecloud_shm, densecloud_shape, densecloud_dtype = create_shared_memory2(densecloud)
    filterdensity_shm, filterdensity_shape, filterdensity_dtype = create_shared_memory2(filterdensity)

    # Prepare tasks for multiprocessing
    tasks = [
        (idx, densecloud_shm.name, densecloud_shape, densecloud_dtype,
         filterdensity_shm.name, filterdensity_shape, filterdensity_dtype, global_bbox)
        for idx in unique_indices
    ]

    results = []
    try:
        with Pool(processes=num_workers) as pool:
            for result in pool.imap_unordered(repopulate_pointcloud_helper, tasks):
                results.append(result)  # Process results as they arrive
        return results
    except Exception as e:
        print(f"Error during parallel execution: {e}")
        return []
    finally:
        # Clean up shared memory
        densecloud_shm.close()
        densecloud_shm.unlink()
        filterdensity_shm.close()
        filterdensity_shm.unlink()

def repopulate_pointcloud_helper(args):
    idx, shm_densecloud_name, densecloud_shape, densecloud_dtype, \
    shm_filterdensity_name, filterdensity_shape, filterdensity_dtype, global_bbox = args

    # Access shared memory for densecloud
    densecloud_shm = shared_memory.SharedMemory(name=shm_densecloud_name)
    shared_densecloud = np.ndarray(densecloud_shape, dtype=densecloud_dtype, buffer=densecloud_shm.buf)

    # Access shared memory for filterdensity
    filterdensity_shm = shared_memory.SharedMemory(name=shm_filterdensity_name)
    shared_filterdensity = np.ndarray(filterdensity_shape, dtype=filterdensity_dtype, buffer=filterdensity_shm.buf)

    # Process the data
    component = shared_filterdensity[shared_filterdensity[:, -1] == idx]
    try:
        hull = GetConcaveHull(component) 
        del component
        hull = AdjustHull(hull, 0.05, global_bbox=global_bbox)
        hull = Polygon(hull)
        segment = CropPointCloudByPolygon(shared_densecloud, hull)

        # Add the index column
        idx_column = np.full((segment.shape[0], 1), idx, dtype=np.int32)
        segment_with_idx = np.concatenate((segment, idx_column), axis=1)
    except:
        segment_with_idx = np.empty((0, shared_densecloud.shape[1] + 1))
    # Clean up
    densecloud_shm.close()
    filterdensity_shm.close()
    return segment_with_idx

def save_to_shapefile(filtered_np, output_dir, output_file, epsg=32633, shiftby=[0,0]):
    """
    Converts a filtered DataFrame into a GeoDataFrame and saves it as a shapefile.

    Parameters:
        filtered_df (pd.DataFrame): The filtered data containing columns DISC_X, DISC_Y, etc.
        output_dir (str): The directory where the shapefile will be saved.
        output_file (str): The name of the output shapefile (without extension).
        epsg (int): The EPSG code for the coordinate reference system (default is WGS 84).
    """
    # Ensure the output file ends with '.shp'
    if not output_file.endswith('.shp'):
        output_file += '.shp'

    # Full path to the shapefile
    output_path = os.path.join(output_dir, output_file)

    ########turn np into pddf, rename columns



    # Check if required columns are present
    required_columns = {'DISC_X', 'DISC_Y'}
    if not required_columns.issubset(filtered_np.columns):
        raise ValueError(f"Filtered DataFrame must contain {required_columns} columns.")

    # Apply the shift to coordinates
    shift_x, shift_y = shiftby[:2]
    filtered_np.loc[:, 'DISC_X'] = filtered_np['DISC_X'] + shift_x
    filtered_np.loc[:, 'DISC_Y'] = filtered_np['DISC_Y'] + shift_y
    # filtered_df['DISC_X'] = filtered_df['DISC_X'] + shift_x
    # filtered_df['DISC_Y'] = filtered_df['DISC_Y'] + shift_y


    # Convert DataFrame to GeoDataFrame
    gdf = gpd.GeoDataFrame(
        filtered_np,
        geometry=[Point(xy) for xy in zip(filtered_np['DISC_X'], filtered_np['DISC_Y'])],
        crs=f'epsg:{epsg}'
    )

    # Save GeoDataFrame to a shapefile
    gdf.to_file(output_path, driver='ESRI Shapefile')
    print(f"[{TimeNow()}] {inspect.currentframe().f_code.co_name}: Shapefile saved as: {output_path}")

def SegmentateTrees(pcdpath, outpcdformat="ASC",debug=False, cpus_to_leave_free = 1):
    #if debug true, unfiltered trees will be kept for possible comparison with filtered trees
    folder = os.path.dirname(pcdpath)
    treefolder = os.path.join(folder, f"{os.path.splitext(os.path.basename(pcdpath))[0]}-Processing", "trees")
    name = os.path.splitext(os.path.basename(pcdpath))[0]
    shppath = f"{pcdpath.split('_cloud_density')[0]}_TreeCrowns.shp"
    check_stop()
    ExtractPcdsByShapefile(pcdpath, shppath, prefix=name, cpus_to_leave_free=cpus_to_leave_free) #this uses all minus 4 CPUs of the PC to cut the trees out. Sorting is done, to have points by coordinates, not by time of creation
    check_stop()
    if os.path.exists(treefolder):
        shutil.rmtree(treefolder)
    #shutil.move(os.path.join(folder, "Shapefiles" , "trees"), treefolder)
    check_stop()
    #CleanTreesClouds(os.path.join(folder, "PointCloudProcessing", "trees"), outpcdformat, ClCexedir) #filters out terrain and smaller trees from the main trees. Was only tested on small sample so far.
    check_stop()
    if debug == False:
        for f in os.listdir(treefolder):
            if "_clean" not in f:
                path = os.path.join(treefolder, f)
                os.remove(path)

    ####UNFLATTEN
    print(f"[{TimeNow()}] {inspect.currentframe().f_code.co_name}: Completed.")

def UpdateCrownIDs(trees_path, crowns_path, epsg_code=3067):
    """
    Update the CrownID field in the crown polygons based on spatial relationships with tree points.

    Args:
        trees_path (str): Path to the shapefile containing tree points with ID_TREE field.
        crowns_path (str): Path to the shapefile containing crown polygons with CrownID field.
        epsg_code (int): EPSG code for the coordinate system. Defaults to 3067.

    Returns:
        None
    """
    try:
        # Load the shapefiles and ensure correct CRS
        trees_gdf = gpd.read_file(trees_path).to_crs(epsg=epsg_code)
        crowns_gdf = gpd.read_file(crowns_path).to_crs(epsg=epsg_code)

        updated_crown_ids = []

        for _, crown in crowns_gdf.iterrows():
            crown_geom = crown.geometry
            original_crown_id = crown['CrownID']
            tree_ids = []

            for _, tree in trees_gdf.iterrows():
                tree_geom = tree.geometry
                tree_id = int(tree['ID_TREE'])

                # Check if tree point is within the crown polygon
                if crown_geom.contains(tree_geom):
                    tree_ids.append(str(tree_id))

            # Determine new CrownID
            if tree_ids:
                new_crown_id = "_".join(tree_ids)
            else:
                new_crown_id = f"{original_crown_id}x"

            updated_crown_ids.append(new_crown_id)

        # Update the CrownID field in the GeoDataFrame
        crowns_gdf["CrownID"] = updated_crown_ids

        # Overwrite the crowns shapefile with updated data
        crowns_gdf.to_file(crowns_path)
        print(f"[{TimeNow()}] {inspect.currentframe().f_code.co_name}: Updated crowns shapefile saved to: {crowns_path}")

    except Exception as e:
        print(f"[{TimeNow()}] {inspect.currentframe().f_code.co_name}: Error: {e}")

###General###
def CheckEPSGIsMetric(epsg_code=None):
    try:
        # Create a CRS object from the EPSG code
        crs = CRS.from_epsg(epsg_code)
        
        # Check if the CRS is projected
        if crs.is_projected:
            print(f"[{TimeNow()}] {inspect.currentframe().f_code.co_name}: Valid EPSG code ({epsg_code}) for metric coordinates.")
            return epsg_code
        else:
            print(f"EPSG {epsg_code} corresponds to a geographic CRS with angular units. Please enter a projected CRS with metric coordinates.")
            raise ValueError("EPSG code not supported. Exiting.")
    except ValueError:
        print(f"[{TimeNow()}] {inspect.currentframe().f_code.co_name}: Invalid input. Please enter a valid numeric EPSG code.")
        raise ValueError("Invalid input. Please enter a valid numeric EPSG code.")
    except CRS.InvalidCRSError:
        print(f"[{TimeNow()}] {inspect.currentframe().f_code.co_name}: Invalid EPSG code. Please try again.")
        raise ValueError("Invalid EPSG code. Please try again.")

def RenameFile(pathtofile, newname):
    """
    Renames a file to a new name, keeping it in the same directory.

    Parameters:
        pathtofile (str): The full path of the file to rename.
        newname (str): The new name for the file (should include the extension).

    Returns:
        str: The new full path of the renamed file.
    """
    # Ensure the file exists
    if not os.path.isfile(pathtofile):
        raise FileNotFoundError(f"The file '{pathtofile}' does not exist.")

    # Extract the directory and current file extension
    dir_name = os.path.dirname(pathtofile)
    _, ext = os.path.splitext(pathtofile)

    # Ensure the new name includes the extension
    if not os.path.splitext(newname)[1]:
        newname += ext

    # Create the new file path
    new_path = os.path.join(dir_name, newname)

    # Check if a file with the new name already exists
    if os.path.exists(new_path):
        raise FileExistsError(f"A file with the name '{newname}' already exists in the directory '{dir_name}'.")

    # Rename the file
    os.rename(pathtofile, new_path)
    print(f"[{TimeNow()}] {inspect.currentframe().f_code.co_name}: File renamed to '{new_path}'")

    return new_path

def StopDropbox():
    try:
        if platform.system() == 'Windows':
            subprocess.run(["taskkill", "/f", "/im", "Dropbox.exe"], check=False)
        elif platform.system() == 'Darwin':  # macOS
            subprocess.run(["pkill", "Dropbox"], check=False)
        elif platform.system() == 'Linux':
            subprocess.run(["pkill", "dropbox"], check=False)
    except subprocess.CalledProcessError as e:
        print(f"")

def StopGoogleDrive():
    try:
        if platform.system() == 'Windows':
            subprocess.run(["taskkill", "/f", "/im", "googledrivesync.exe"], check=False)
        elif platform.system() == 'Darwin':  # macOS
            subprocess.run(["pkill", "Google Drive"], check=False)
        elif platform.system() == 'Linux':
            subprocess.run(["pkill", "google-drive"], check=False)
    except subprocess.CalledProcessError as e:
        print(f"")

def StopiCloud():
    try:
        if platform.system() == 'Windows':
            subprocess.run(["taskkill", "/f", "/im", "iCloud.exe"], check=False)
        elif platform.system() == 'Darwin':  # macOS
            subprocess.run(["pkill", "iCloud"], check=False)
        elif platform.system() == 'Linux':  # iCloud is not natively supported on Linux, but you can add custom handling here
            print("iCloud is not natively available on Linux.")
    except subprocess.CalledProcessError as e:
        print(f"")

def StopOnedrive():
    try:
        if platform.system() == 'Windows':
            subprocess.run(["taskkill", "/f", "/im", "OneDrive.exe"], check=False)
        elif platform.system() == 'Darwin':  # macOS
            subprocess.run(["pkill", "OneDrive"], check=False)
        elif platform.system() == 'Linux':
            subprocess.run(["pkill", "onedrive"], check=False)
    except subprocess.CalledProcessError as e:
        print(f"")

def TimeNow():
    from datetime import datetime
    return datetime.now().strftime("%H:%M:%S")

###GUI###
def check_stop():
    """
    Checks for stop and pause flags. If stop is triggered, raise an exception. 
    If paused, wait for the pause to be cleared before continuing.
    """
    global stop_processing
    if stop_processing:  # Check for stop flag
        raise StopProcessException("Processing was stopped by user.")
    
    # Check for pause flag and wait until it is cleared
    while not pause_event.is_set():
        # Check for stop while paused
        if stop_processing:
            raise StopProcessException("Processing was stopped by user.")
        time.sleep(0.1)  # Avoid busy-waiting

class StopProcessException(Exception):
    """Custom exception to stop the processing."""
    pass

global stop_processing
stop_processing = False

###Point Clouds###
def AddConstantField(input_data, fieldvalue, field_name="constant_field", field_dtype=None):
    """
    Load a point cloud, append a new column with a constant value, and return the updated point cloud.
    Supports input as file paths or DataFrames.

    Parameters:
    -----------
    input_data : str or pandas.DataFrame
        Path to the point cloud file or a DataFrame containing point cloud data.
    fieldvalue : float or int
        The constant value to add as a new column.
    field_name : str, optional
        The name of the new column to add (default: "constant_field").

    Returns:
    --------
    pandas.DataFrame
        The updated point cloud with the new column added.
    """
    # Load the point cloud data
    if isinstance(input_data, pd.DataFrame):
        pointcloud = input_data.copy()  # Explicitly create a copy of the input DataFrame
    else:
        # Use LoadPointCloud to load the point cloud from file path
        pointcloud = LoadPointCloud(input_data, return_type="pddf")
    
    # Add the new column with the constant value
    pointcloud[field_name] = fieldvalue


    if field_dtype is not None:
        pointcloud[field_name] = pointcloud[field_name].astype(field_dtype)

    # Ensure the new column is at the last position
    columns_order = [col for col in pointcloud.columns if col != field_name] + [field_name]
    pointcloud = pointcloud[columns_order]

    # Return the updated DataFrame
    return pointcloud

def AdjustBoundingBox(component_bbox, global_bbox, bufferx=0.05, buffery=0.05, bufferz=0.01):
    """
    Adjusts a bounding box with buffers to ensure it fits within the bounding box of the dense cloud.

    Parameters:
        component_bbox (list): Bounding box of the component [xmin, xmax, ymin, ymax, zmin, zmax].
        shared_densecloud (np.ndarray): Entire dense cloud data (N, 3).
        bufferx (float): Buffer to add along the x-axis.
        buffery (float): Buffer to add along the y-axis.
        bufferz (float): Buffer to add along the z-axis.

    Returns:
        list: Adjusted bounding box [xmin, xmax, ymin, ymax, zmin, zmax].
    """
    # Get global bounding box of the entire dense cloud

    data_xmin, data_xmax, data_ymin, data_ymax, data_zmin, data_zmax = global_bbox

    # Extract component bounding box
    xmin, xmax, ymin, ymax, zmin, zmax = component_bbox

    # Apply buffer and clamp to global bounding box
    adjusted_bbox = [
        max(xmin - bufferx, data_xmin),  # Clamp to global xmin
        min(xmax + bufferx, data_xmax),  # Clamp to global xmax
        max(ymin - buffery, data_ymin),  # Clamp to global ymin
        min(ymax + buffery, data_ymax),  # Clamp to global ymax
        max(zmin - bufferz, data_zmin),  # Clamp to global zmin
        min(zmax + bufferz, data_zmax),  # Clamp to global zmax
    ]

    return adjusted_bbox

def CalculateAvgDensity(input_data, volume=0.01, num_points=1000, num_attempts=5):
    """
    Calculates the average density of points within a predefined spherical volume
    for a randomly sampled subset of points from the input data.

    Parameters:
        input_data (np.ndarray): Point cloud data as a NumPy array of shape (n, m).
        volume (float): The volume of the spherical region to search for neighbors.
        num_points (int): Number of random points to sample for density estimation.
        num_attempts (int): Number of repeated random samplings.

    Returns:
        float: The average neighbor count per sampled point within the given volume.
    """
    input_data=LoadPointCloud(input_data, "np", "xyz")
    
    # Calculate the radius corresponding to the given volume
    radius = (3 * volume / (4 * np.pi)) ** (1 / 3)
    
    # Adjust num_points to the available number of points
    num_points = min(num_points, input_data.shape[0])
    
    # Handle the edge case of a single-point dataset
    if input_data.shape[0] == 1:
        return 0.0

    # Build a KD-tree for efficient neighbor searches
    kdtree = cKDTree(input_data)
    
    # Accumulate neighbor counts across attempts
    total_avg_neighbors = 0.0
    
    for _ in range(num_attempts):
        # Randomly select `num_points` from the input data
        selected_indices = np.random.choice(input_data.shape[0], num_points, replace=False)
        selected_points = input_data[selected_indices]
        
        # Query the KD-tree for neighbors within the radius for all selected points
        counts = kdtree.query_ball_point(selected_points, radius, return_length=True)
        
        # Average the neighbor count per sampled point (excluding the point itself)
        avg_neighbors = np.mean(np.array(counts) - 1)  # Exclude self-count
        total_avg_neighbors += avg_neighbors
    
    # Average across all attempts
    final_avg_neighbors = total_avg_neighbors / num_attempts
    
    print(f"Average density: {final_avg_neighbors:.2f} neighbors per {volume} unit³.")
    return final_avg_neighbors

def CalculateBoundingBoxDimensions(input_data):
    """
    Calculate the dimensions of the bounding box for a given point cloud.

    This function takes input data, which can be either a directory containing
    point cloud data (PCD) files or a point cloud variable, and computes the
    dimensions of the bounding box that encloses the point cloud.

    Parameters:
    input_data : str or np.ndarray
        The input data can be either:
        - A string representing the directory containing point cloud data (PCD) files.
        - A numpy array representing the point cloud data directly.

    Returns:
    np.ndarray
        A numpy array containing the dimensions of the bounding box in the format [dx, dy, dz],
        where dx, dy, and dz represent the dimensions along the x, y, and z axes, respectively.

    Example:
    --------
    >>> import numpy as np
    >>> points = np.array([[0, 0, 0], [1, 1, 1], [2, 2, 2]])
    >>> CalculateBoundingBoxDimensions(points)
    array([2, 2, 2])
    """
    pointcloudvar = LoadPointCloud(input_data)
    points = pointcloudvar
    min_values = np.min(points, axis=0)
    max_values = np.max(points, axis=0)
    bboxdims = max_values - min_values
    return bboxdims

def CalculateMeshArea(mesh):
    """
    Calculate the surface area of a given mesh.

    Parameters:
        mesh (pv.PolyData): A PyVista mesh object.

    Returns:
        float: The total surface area of the mesh.
    """
    if not isinstance(mesh, pv.PolyData):
        raise TypeError("The input must be a PyVista PolyData object.")

    # Use PyVista's built-in area calculation
    area = mesh.area

    print(f"[{TimeNow()}] {inspect.currentframe().f_code.co_name}: The total surface area of the mesh is {area:.6f} units².")
    return area

def ChunkPointCloud(points, n=2):
    """
    Chunks a point cloud into n x n pieces based on the XY bounding box.
    
    Parameters
    ----------
    points : np.ndarray
        An (N, 3) numpy array (or (N, 2)) representing the point cloud. Only the first two
        columns (X and Y) are used for chunking.
    n : int, optional
        Number of chunks along each axis (default is 2, resulting in 2x2=4 chunks).
        
    Returns
    -------
    chunks : list of np.ndarray
        A list of numpy arrays, each containing the points that fall within one chunk.
    """

    points = LoadPointCloud(points,"np")
    # Compute the bounding box for the x and y coordinates
    x_min, x_max = np.min(points[:, 0]), np.max(points[:, 0])
    y_min, y_max = np.min(points[:, 1]), np.max(points[:, 1])
    
    # Create evenly spaced edges along x and y
    x_edges = np.linspace(x_min, x_max, n + 1)
    y_edges = np.linspace(y_min, y_max, n + 1)
    
    chunks = []
    
    # Loop over each cell in the n x n grid
    for i in range(n):
        for j in range(n):
            # Define the boundaries for the current chunk
            x0, x1 = x_edges[i], x_edges[i + 1]
            y0, y1 = y_edges[j], y_edges[j + 1]
            
            # For non-final bins, use half-open intervals [a, b)
            if i < n - 1:
                x_mask = (points[:, 0] >= x0) & (points[:, 0] < x1)
            else:
                x_mask = (points[:, 0] >= x0) & (points[:, 0] <= x1)
            
            if j < n - 1:
                y_mask = (points[:, 1] >= y0) & (points[:, 1] < y1)
            else:
                y_mask = (points[:, 1] >= y0) & (points[:, 1] <= y1)
            
            # Combine masks for x and y to get the points in this chunk
            mask = x_mask & y_mask
            chunk = points[mask]
            chunks.append(chunk)
    print(f"[{TimeNow()}] {inspect.currentframe().f_code.co_name}: Done")  
    return chunks


def ChunkPointCloudBySize(points, chunk_size=10):
    """
    Chunks a point cloud into pieces of approximately `chunk_size` meters based on the XY bounding box.
    
    Parameters
    ----------
    points : np.ndarray
        An (N, 3) numpy array (or (N, 2)) representing the point cloud. Only the first two
        columns (X and Y) are used for chunking.
    chunk_size : float, optional
        Desired size of each chunk in meters (default is 10 meters).
        
    Returns
    -------
    chunks : list of np.ndarray
        A list of numpy arrays, each containing the points that fall within one chunk.
    """
    points = LoadPointCloud(points, "np")
    # Compute the bounding box for the x and y coordinates
    x_min, x_max = np.min(points[:, 0]), np.max(points[:, 0])
    y_min, y_max = np.min(points[:, 1]), np.max(points[:, 1])
    
    # Determine the number of chunks needed along each axis
    n_x = max(1, int(np.ceil((x_max - x_min) / chunk_size)))
    n_y = max(1, int(np.ceil((y_max - y_min) / chunk_size)))
    
    # Create grid edges
    x_edges = np.linspace(x_min, x_max, n_x + 1)
    y_edges = np.linspace(y_min, y_max, n_y + 1)
    
    chunks = []
    
    # Loop over each cell in the grid
    for i in range(n_x):
        for j in range(n_y):
            # Define the boundaries for the current chunk
            x0, x1 = x_edges[i], x_edges[i + 1]
            y0, y1 = y_edges[j], y_edges[j + 1]
            
            # Create masks to filter points within the chunk
            x_mask = (points[:, 0] >= x0) & (points[:, 0] < x1)
            y_mask = (points[:, 1] >= y0) & (points[:, 1] < y1)
            
            # Include the upper boundary for the final bins
            if i == n_x - 1:
                x_mask = (points[:, 0] >= x0) & (points[:, 0] <= x1)
            if j == n_y - 1:
                y_mask = (points[:, 1] >= y0) & (points[:, 1] <= y1)
            
            mask = x_mask & y_mask
            chunk = points[mask]
            chunks.append(chunk)
    
    print(f"[{inspect.currentframe().f_code.co_name}]: Done")  
    return chunks

def CloudToMeshDistance(input_data, meshpath_or_mesh, outputdir=None, shiftby=[0,0]):
    """
    Computes the distance from each point in a point cloud to the closest surface of a mesh.

    Parameters:
        pointcloudpath (str or pd.DataFrame): The input point cloud data or file path.
        meshpath_or_mesh (str or pv.PolyData): The path to the input mesh file or a preloaded PyVista mesh.
        outputdir (str, optional): Directory to save the output file. If None, no files are saved (debugging off).

    Returns:
        pd.DataFrame: The updated DataFrame with distances added as a column.
    """
    data = LoadPointCloud(input_data, "pddf")

    # Ensure there are at least three columns for X, Y, Z
    if data.shape[1] < 3:
        raise ValueError("The input point cloud must have at least three columns (X, Y, Z).")

    # Rename columns for clarity
    column_names = [f"Column{i+1}" for i in range(data.shape[1])]
    data.columns = column_names

    # Extract the XYZ points
    points = data.iloc[:, :3].values

    # Load the mesh
    if isinstance(meshpath_or_mesh, str):
        mesh = pv.read(meshpath_or_mesh)
    elif isinstance(meshpath_or_mesh, pv.PolyData):
        mesh = meshpath_or_mesh
    else:
        raise ValueError("meshpath_or_mesh must be either a file path (str) or a PyVista PolyData object.")

    # Convert the points into a PyVista PolyData object
    point_cloud = pv.PolyData(points)

    # Compute the distance from the point cloud to the mesh surface
    distances = point_cloud.compute_implicit_distance(mesh)

    # Extract the distances from the resulting field
    distance_values = distances['implicit_distance']

    # Add the distances to the DataFrame
    data['CtoM_Distance'] = distance_values
    print(f"[{TimeNow()}] {inspect.currentframe().f_code.co_name}: Done")
    return data


##vytunenej

def CloudToMeshVerticalDistance(input_data, meshpath_or_mesh, outputdir=None, shiftby=[0,0], max_dist=np.inf, grid_resolution=100):
    """
    Computes the vertical distance from each point in a point cloud to the closest surface of a mesh
    directly below it, using a uniform grid acceleration structure and a 2D KDTree for efficiency.

    Parameters:
        input_data (str or pd.DataFrame): The input point cloud data or file path.
        meshpath_or_mesh (str or pv.PolyData): The path to the input mesh file or a preloaded PyVista mesh.
        outputdir (str, optional): Directory to save the output file. If None, no files are saved (debugging off).
        max_dist (float, optional): Maximum allowed vertical distance. Points beyond this distance are assigned max_dist.
        grid_resolution (int, optional): Resolution of the uniform grid used for acceleration. Higher values improve precision but increase computation time.

    Returns:
        pd.DataFrame: The updated DataFrame with vertical distances added as a column.
    """
    data = LoadPointCloud(input_data, "pddf")

    # Ensure there are at least three columns for X, Y, Z
    if data.shape[1] < 3:
        raise ValueError("The input point cloud must have at least three columns (X, Y, Z).")

    # Rename columns for clarity
    column_names = [f"Column{i+1}" for i in range(data.shape[1])]
    data.columns = column_names

    # Extract the XYZ points
    points = data.iloc[:, :3].values
    points_xy = points[:, :2]  # Extract only X, Y for 2D KDTree

    # Load the mesh
    if isinstance(meshpath_or_mesh, str):
        mesh = pv.read(meshpath_or_mesh)
    elif isinstance(meshpath_or_mesh, pv.PolyData):
        mesh = meshpath_or_mesh
    else:
        raise ValueError("meshpath_or_mesh must be either a file path (str) or a PyVista PolyData object.")

    # Convert mesh to uniform grid (heightmap acceleration)
    bounds = mesh.bounds
    x_vals = np.linspace(bounds[0], bounds[1], grid_resolution)
    y_vals = np.linspace(bounds[2], bounds[3], grid_resolution)
    xv, yv = np.meshgrid(x_vals, y_vals)
    zv = np.full_like(xv, np.nan, dtype=float)

    # Extract faces properly
    faces = mesh.faces.reshape(-1, mesh.faces[0] + 1)[:, 1:]
    for face in faces:
        face_points = mesh.points[face]
        mean_x, mean_y, mean_z = face_points.mean(axis=0)
        grid_x_idx = np.argmin(np.abs(x_vals - mean_x))
        grid_y_idx = np.argmin(np.abs(y_vals - mean_y))
        zv[grid_y_idx, grid_x_idx] = mean_z
    
    # Flatten grid for KDTree
    grid_xy = np.column_stack((xv.ravel(), yv.ravel()))
    grid_z = zv.ravel()
    valid_mask = ~np.isnan(grid_z)
    kd_tree_2d = cKDTree(grid_xy[valid_mask])

    # Find nearest (X, Y) projection on heightmap
    _, nearest_indices = kd_tree_2d.query(points_xy)
    nearest_z = grid_z[valid_mask][nearest_indices]

    # Compute vertical distances using the Z-difference
    vertical_distances = np.full(points.shape[0], float(max_dist), dtype=float)
    mask = (points[:, 2] >= nearest_z) & (points[:, 2] - nearest_z <= max_dist)
    vertical_distances[mask] = points[mask, 2] - nearest_z[mask]

    # Assign computed distances to the dataframe
    data['CtoM_Vertical_Distance'] = vertical_distances.astype(np.float32)
    
    print(f"[{TimeNow()}] {inspect.currentframe().f_code.co_name}: Done")
    return data






def ComputeDensity(input_data, radius=0.1, outputdir = None,shiftby=[0,0,0], cpus_to_leave_free=1):
    """
    Computes the density of each point in a point cloud using parallel processing.

    Parameters:
        pointcloudpath (str): The path to the input point cloud file.
        radius (float): The radius within which to count neighbors.
    """
    # Load the point cloud data
    data = LoadPointCloud(input_data)  # Assuming LoadPointCloud is implemented elsewhere

    # Ensure there are at least three columns for spatial coordinates
    if data.shape[1] < 3:
        raise ValueError("The input file must have at least three columns (X, Y, Z).")

    # Rename columns for clarity
    column_names = [f"Column{i+1}" for i in range(data.shape[1])]
    data.columns = column_names

    # Extract X, Y, Z coordinates
    coordinates = data[["Column1", "Column2", "Column3"]].values

    # Build a KDTree for efficient neighbor queries
    tree = cKDTree(coordinates)

    # Determine the number of cores to use (all cores minus 4)
    num_cores = max(mp.cpu_count() - cpus_to_leave_free, 1)

    # Split the data into chunks for parallel processing
    chunk_size = len(coordinates) // num_cores
    chunks = [(i, min(i + chunk_size, len(coordinates))) for i in range(0, len(coordinates), chunk_size)]

    # Compute densities in parallel
    densities = Parallel(n_jobs=num_cores, timeout=None, backend="threading")(
        delayed(compute_density_helper)(tree, coordinates, radius, start, end)
        for start, end in chunks
    )

    # Flatten the list of densities
    density_counts = np.concatenate(densities)

    # Add density values as a new column
    data[f"Density_{radius}"] = density_counts.astype(np.int32)

    # Determine save directory and file name
    if isinstance(input_data, str):
        dir_name, base_name = os.path.split(input_data)
        file_name, ext = os.path.splitext(base_name)
    elif outputdir is not None:
        dir_name, base_name = outputdir, "cloud.txt"
        file_name, ext = os.path.splitext(base_name)
    elif outputdir is None:
        dir_name, file_name, ext = None, None, None
        print(f"[{TimeNow()}] {inspect.currentframe().f_code.co_name}: Done")

    # Save the file if a directory is determined
    if dir_name is not None:
        output_file = os.path.join(dir_name, f"{file_name}_density{ext}")
        SavePointCloud(data, output_file, shiftby=shiftby)
        print(f"[{TimeNow()}] {inspect.currentframe().f_code.co_name}: Point cloud has been saved to {output_file}")

    # Always return the data
    print(f"[{TimeNow()}] {inspect.currentframe().f_code.co_name}: Done")
    return data
def compute_density_helper(tree, coordinates, radius, start, end):
    """
    Computes the density for a chunk of points.

    Parameters:
        tree (cKDTree): The KDTree built from the full dataset.
        coordinates (ndarray): The full array of points.
        radius (float): The radius for density computation.
        start (int): The starting index of the chunk.
        end (int): The ending index of the chunk.

    Returns:
        List[int]: The density counts for the chunk.
    """
    chunk_coordinates = coordinates[start:end]
    densities = tree.query_ball_point(chunk_coordinates, radius, return_length=True)
    gc.collect()
    return densities

def ComputeVerticality(points, radius=0.01):
    """
    Compute the verticality of points in a 3D point cloud.

    Parameters:
        points (numpy.ndarray): A (N, 3) array of 3D points.
        radius (float): Radius to select neighboring points for computing verticality.

    Returns:
        numpy.ndarray: A (N, 4) array where each point has an additional verticality attribute.
    """

    points = LoadPointCloud(points, "np", "all")
    # Build KD-tree for efficient neighbor search
    
    tree = cKDTree(points[:, :3])

    # Extend points array with an additional column for verticality
    points = np.hstack((points, np.zeros((points.shape[0], 1))))

    for i, point in enumerate(points[:, :3]):  # Process only the XYZ coordinates
        # Find indices of neighboring points within the radius
        indices = tree.query_ball_point(point, r=radius)

        # Skip if there are too few neighbors to compute the tensor
        if len(indices) < 3:
            points[i, -1] = 0  # Set verticality to 0 or np.nan
            continue

        # Get the neighboring points
        neighbors = points[indices, :3]

        # Compute the covariance matrix (structure tensor)
        mean_point = np.mean(neighbors, axis=0)
        centered_neighbors = neighbors - mean_point
        covariance_matrix = np.dot(centered_neighbors.T, centered_neighbors) / len(neighbors)

        # Eigen decomposition of the covariance matrix
        eigenvalues, eigenvectors = np.linalg.eigh(covariance_matrix)

        # The eigenvector corresponding to the smallest eigenvalue
        e3 = eigenvectors[:, 0]  # Assuming sorted eigenvalues

        # Compute verticality: 1 - |h[0, 0, 1], e3|
        vertical_direction = np.array([0, 0, 1])
        points[i, -1] = 1 - abs(np.dot(vertical_direction, e3))

    return points

def CropByBoxDimensions(input_data, xmin, xmax, ymin, ymax, zmin, zmax, bufferx=0, buffery=0, bufferz=0):
    """
    Crops a point cloud by the given bounding box dimensions.
    
    Parameters:
    - input_data: np.ndarray of shape (N, 3), where each row is (x, y, z).
    - xmin, xmax, ymin, ymax, zmin, zmax: float or -1.
      If a coordinate is -1, it is treated as infinite (no bound).
    
    Returns:
    - cropped_data: np.ndarray containing points within the specified box.
    """
    # Replace -1 with -inf for min bounds and +inf for max bounds
    xmin = -np.inf if xmin == -1 else xmin - bufferx
    xmax = np.inf if xmax == -1 else xmax + bufferx
    ymin = -np.inf if ymin == -1 else ymin - buffery
    ymax = np.inf if ymax == -1 else ymax + buffery
    zmin = -np.inf if zmin == -1 else zmin - bufferz
    zmax = np.inf if zmax == -1 else zmax + bufferz
    
    # Apply the bounding box filter
    mask = (
        (input_data[:, 0] >= xmin) & (input_data[:, 0] <= xmax) &  # x range
        (input_data[:, 1] >= ymin) & (input_data[:, 1] <= ymax) &  # y range
        (input_data[:, 2] >= zmin) & (input_data[:, 2] <= zmax)    # z range
    )
    
    cropped_data = input_data[mask]
    return cropped_data

def CropCloudByExtent(cloud_data, extent_data, method="convex", cpus_to_leave_free = 1):
    """
    Optimized function to crop a point cloud using an extent defined by a polygon, mesh, or raster.
    Uses parallel processing with (all CPUs - 4) to improve performance.
    """

    # Calculate the number of CPUs to use
    available_cpus = mp.cpu_count()
    n_jobs = max(1, available_cpus - cpus_to_leave_free)  # Ensure at least 1 CPU is used

    # Load extent data
    if isinstance(extent_data, str) and extent_data.endswith(".ply"):
        mesh = o3d.io.read_triangle_mesh(extent_data)
        vertices = np.asarray(mesh.vertices)[:, :2]
    elif isinstance(extent_data, str) and extent_data.endswith((".tif", ".tiff")):
        vertices = RasterToPointCloud(extent_data)[:, :2]
    elif isinstance(extent_data, Polygon):
        vertices = np.array(extent_data.exterior.coords)
    else:
        extent_points = LoadPointCloud(extent_data, "pddf")
        vertices = extent_points.iloc[:, :2].to_numpy()  # Assume first two columns are X and Y

    # Define bounding polygon
    if method == "convex":
        bounding_polygon = Polygon(vertices).convex_hull
    elif method == "concave":
        bounding_polygon = compute_concave_hull(vertices)
    else:
        raise ValueError("Method must be 'convex' or 'concave'.")

 
    # Serialize the original geometry (not the PreparedGeometry object)
    bounding_polygon_wkb = wkb_dumps(bounding_polygon)

    # Load the point cloud data
    points_df = LoadPointCloud(cloud_data, "pddf")  # Assume a pandas DataFrame
    x_col, y_col, z_col = points_df.columns[:3]  # Assume X, Y, Z are the first three columns

    # Parallelize the `contains` operation TO USE WITH PYINSTALLER EXE CHANGE BACKEND TO THREADING AND MOVE GLOBAL VARIABLES TO GUARD
    inside_flags = Parallel(n_jobs=n_jobs)(
        delayed(cropcloudbyextent_helper)(row, bounding_polygon_wkb, x_col, y_col) 
        for row in points_df.itertuples(index=False)
    )

    # Filter points
    points_df["inside"] = inside_flags
    cropped_points_df = points_df[points_df["inside"]].drop(columns=["inside"])

    # Save result if input was a file
    if isinstance(cloud_data, str):
        directory = os.path.dirname(cloud_data)
        name = os.path.basename(cloud_data)
        extension = os.path.splitext(cloud_data)[1]
        name_without_extension = name.replace(extension, "")
        output_path = os.path.join(directory, f"{name_without_extension}_cropped.csv")
        cropped_points_df.to_csv(output_path, index=False)
    print(f"[{TimeNow()}] {inspect.currentframe().f_code.co_name}: Done")
    return cropped_points_df
def cropcloudbyextent_helper(row, polygon_wkb, x_col, y_col):
    """
    Checks if a point is inside the given polygon (deserialized from WKB).
    """
    polygon = wkb_loads(polygon_wkb)  # Deserialize the polygon
    return polygon.contains(Point(row[x_col], row[y_col]))
    #return polygon.contains(Point(getattr(row, x_col), getattr(row, y_col)))
def compute_concave_hull(points, alpha=1.0):
    """
    Computes a concave hull for a set of points using the alphashape library.

    Parameters:
    - points: np.ndarray of shape (N, 2), where N is the number of points and each row is (x, y).
    - alpha: float, controls the concavity of the hull. Smaller values result in tighter fits.

    Returns:
    - concave_hull: Shapely Polygon or MultiPolygon representing the concave hull.
    """
    if points.shape[0] < 3:
        # Not enough points to form a hull
        return None

    # Compute the concave hull
    hull = alphashape.alphashape(points, alpha)

    # Ensure the hull is a valid Shapely Polygon or MultiPolygon
    if isinstance(hull, (Polygon, MultiPolygon)) and not hull.is_empty:
        return hull

    # Return None if the hull is invalid or empty
    return None



def crop_dense_cloud_with_obb(dense_cloud, disc_points):
    """
    Crop the dense point cloud using the oriented bounding box derived from disc_points.
    
    Parameters:
        dense_cloud (np.ndarray): An Mx3 numpy array of the dense cloud points.
        disc_points (np.ndarray): An Nx3 numpy array of disc points (used to compute the OBB).
    
    Returns:
        cropped_points (np.ndarray): The cropped dense cloud points.
    """
    dense_cloud = LoadPointCloud(dense_cloud, "np")

    # Compute the oriented bounding box from the disc points.
    obb = get_oriented_bounding_box(disc_points)
    
    # Convert the dense cloud to an Open3D point cloud.
    dense_pcd = o3d.geometry.PointCloud()
    dense_pcd.points = o3d.utility.Vector3dVector(dense_cloud)
    del dense_cloud
    
    # Crop the dense cloud with the OBB.
    cropped_pcd = dense_pcd.crop(obb)
    
    # Convert the result back to a NumPy array.
    cropped_points = np.asarray(cropped_pcd.points)
    print(f"[{TimeNow()}] {inspect.currentframe().f_code.co_name}: Done")
    return cropped_points

def crop_dense_cloud_with_obb2(dense_cloudo3d, disc_points):
    """
    Crop the dense point cloud using the oriented bounding box derived from disc_points.
    
    Parameters:
        dense_cloud (np.ndarray): An Mx3 numpy array of the dense cloud points.
        disc_points (np.ndarray): An Nx3 numpy array of disc points (used to compute the OBB).
    
    Returns:
        cropped_points (np.ndarray): The cropped dense cloud points.
    """
    # Compute the oriented bounding box from the disc points.
    obb = get_oriented_bounding_box(disc_points)
    
    # Crop the dense cloud with the OBB.
    cropped_pcd = dense_cloudo3d.crop(obb)
    
    # Convert the result back to a NumPy array.
    cropped_points = np.asarray(cropped_pcd.points)
    print(f"[{TimeNow()}] {inspect.currentframe().f_code.co_name}: Done")
    return cropped_points



def CropDenseCloudWithOBB(dense_cloud, disc_points):
    """
    Crop the dense point cloud using the oriented bounding box derived from disc_points.
    
    Parameters:
        dense_cloud (np.ndarray): An Mx3 numpy array of the dense cloud points.
        disc_points (np.ndarray): An Nx3 numpy array of disc points (used to compute the OBB).
    
    Returns:
        cropped_points (np.ndarray): The cropped dense cloud points.
    """
    dense_cloud = LoadPointCloud(dense_cloud, "np")

    # Compute the oriented bounding box (OBB) parameters
    obb = get_oriented_bounding_box(disc_points)
    center = np.asarray(obb.center)
    R = np.asarray(obb.R)  # Rotation matrix
    extent = np.asarray(obb.extent)  # Half-dimensions along each axis

    # Transform points into the OBB local coordinate system
    transformed_points = (dense_cloud - center) @ R.T

    # Apply axis-aligned cropping in the OBB space
    mask = np.all(np.abs(transformed_points) <= extent, axis=1)

    # Select only points inside the OBB
    cropped_points = dense_cloud[mask]
    
    print(f"[{TimeNow()}] {inspect.currentframe().f_code.co_name}: Done")
    return cropped_points


def CropPointCloudByPolygon(point_cloud, polygon):
    """
    Fast cropping of a point cloud using matplotlib's Path.contains_points().

    Parameters:
    - point_cloud: np.ndarray of shape (N, 3), where each row is (x, y, z).
    - polygon: Shapely Polygon object.

    Returns:
    - np.ndarray of shape (M, 3) with points inside the polygon.
    """
    if not isinstance(polygon, Polygon):
        raise ValueError("The input polygon must be a Shapely Polygon object.")

    # Convert polygon to a matplotlib Path
    path = Path(np.array(polygon.exterior.coords))

    # Check which points are inside the polygon (only consider XY)
    mask = path.contains_points(point_cloud[:, :2])  # Only check XY

    return point_cloud[mask]

def DelaunayMesh25D(input_data, shiftby=None, outputdir=None):
    """
    Perform 2.5D Delaunay triangulation on a point cloud (XY plane) and save the resulting surface mesh.

    Parameters:
        pointcloudpath (str, np.ndarray, or pd.DataFrame): The input point cloud data or file path.
        outputdir (str, optional): Directory to save the resulting mesh file.

    Returns:
        pv.PolyData: The resulting PyVista mesh object.

    Raises:
        ValueError: If the input does not contain at least three columns (X, Y, Z).
    """
    # Load the input data through LoadPointCloud
    data = LoadPointCloud(input_data)

    # Ensure there are at least three columns for X, Y, Z
    if data.shape[1] < 3:
        raise ValueError("The input data must have at least three columns (X, Y, Z).")

    # Extract X, Y for triangulation and keep Z for attributes
    points_xy = data.iloc[:, :2].values  # XY coordinates
    z_values = data.iloc[:, 2].values   # Z-values

    # Perform 2D Delaunay triangulation in the XY plane
    delaunay = Delaunay(points_xy)

    # Create vertices for PyVista
    points_xyz = np.column_stack((points_xy, z_values))  # Combine XY with Z for full 3D representation

    # Create faces for PyVista (each simplex is a triangle)
    faces = np.hstack([
        np.array([3, *simplex]) for simplex in delaunay.simplices  # 3 indicates a triangle
    ]).astype(np.int32)

    # Create a PyVista PolyData object
    mesh = pv.PolyData(points_xyz)
    mesh.faces = faces

    # Compute normals (optional, for visualization)
    mesh.compute_normals(inplace=True)

    # Determine save directory and file name
    if isinstance(input_data, str):
        dir_name, base_name = os.path.split(input_data)
        file_name, ext = os.path.splitext(base_name)
    elif outputdir is not None:
        dir_name, base_name = outputdir, "cloud.ply" #if another format needed, rewrite here to .vtk or .ply
        file_name, ext = os.path.splitext(base_name)
    else:
        dir_name, file_name, ext = None, None, None

    # Save the mesh if a directory is determined
    if dir_name is not None:

        output_file_shift = os.path.join(dir_name, f"{file_name}_delaunay_SHIFT{ext or '.vtk'}")
        mesh.save(output_file_shift)

        if shiftby is None:
            shiftby = globals().get('shiftby', [0,0,0])  # Check global scope for shiftby, else default to [0, 0]

        # # Perform the shift
        mesh.translate([shiftby[0], shiftby[1], shiftby[2]], inplace=True)

        output_file = os.path.join(dir_name, f"{file_name}_delaunay{ext or '.vtk'}")
        mesh.save(output_file)
        print(f"[{TimeNow()}] {inspect.currentframe().f_code.co_name}: Delaunay triangulation has been saved to {output_file} and {output_file_shift}.")

    # Always return the mesh
    print(f"[{TimeNow()}] {inspect.currentframe().f_code.co_name}: Done")
    return mesh

def ExtractPcdsByShapefile(cloud_data, shapefile_path, outformat=".xyz", prefix="object", cpus_to_leave_free = 1):
    """
    Extract and save point cloud segments based on polygons in a Shapefile using Fiona.
    """
    try:
        # Sort the point cloud data by XY coordinates
        pcd = SortPointCloudByXY(cloud_data)

        # Create shared memory for the DataFrame as a NumPy array
        shm = shared_memory.SharedMemory(create=True, size=pcd.values.nbytes)
        shared_pcd = np.ndarray(pcd.shape, dtype=pcd.values.dtype, buffer=shm.buf)
        np.copyto(shared_pcd, pcd.values)

        # Load the Shapefile using Fiona
        polygons_and_classes = []
        with fiona.open(shapefile_path, 'r') as sf:
            for feature in sf:
                geometry = shape(feature['geometry'])  # Convert to Shapely geometry
                # Extract the class name from the correct field in the shapefile
                class_name = feature['properties'].get("CrownID", "unknown")
                polygons_and_classes.append((geometry, class_name))

        # Create "trees" folder if it doesn't exist
        trees_folder = os.path.join(os.path.dirname(shapefile_path), "trees")
        if not os.path.exists(trees_folder):
            os.makedirs(trees_folder)

        # Determine the number of processes to use
        num_processes = max(1, cpu_count() - cpus_to_leave_free)
        chunk_size = len(pcd) // num_processes
        chunks = [
            (
                pcd.iloc[i:i + chunk_size],
                i,
                shm.name,
                pcd.shape,
                pcd.values.dtype,
                polygons_and_classes,
                trees_folder,
                outformat,
                prefix,
            )
            for i in range(0, len(pcd), chunk_size)
        ]

        # Use multiprocessing to process each chunk independently
        with Pool(processes=num_processes) as pool:
            pool.starmap(ExtractPcdsByShapefileHelper, chunks)
            pool.close()
            pool.join()

        # Clean up shared memory
        shm.close()
        shm.unlink()
        MergePointCloudsByClass(trees_folder,outformat=outformat, cpus_to_leave_free=cpus_to_leave_free)
        print(f"[{TimeNow()}] {inspect.currentframe().f_code.co_name}: Done")

    except Exception as e:
        print(f"[{TimeNow()}] {inspect.currentframe().f_code.co_name}: An error occurred: {e}")
        print(traceback.format_exc())
def ExtractPcdsByShapefileHelper(chunk, offset, shm_name, shape, dtype, polygons_and_classes, trees_folder, outformat, prefix):
    """
    Process a chunk of point cloud data (as a Pandas DataFrame) to extract segments based on polygons.
    """
    try:
        check_stop()
        # Attach to the existing shared memory
        existing_shm = shared_memory.SharedMemory(name=shm_name)
        pcd = np.ndarray(shape, dtype=dtype, buffer=existing_shm.buf)

        # Validate that chunk is a DataFrame
        if not isinstance(chunk, pd.DataFrame):
            raise ValueError("Chunk must be a Pandas DataFrame.")

        # Calculate chunk bounding box using DataFrame indexing
        chunk_bbox = box(
            chunk.iloc[:, 0].min(), chunk.iloc[:, 1].min(),
            chunk.iloc[:, 0].max(), chunk.iloc[:, 1].max()
        )

        # Get relevant polygons for this chunk by checking bounding box overlap
        relevant_indices = []
        for idx, (polygon, _) in enumerate(polygons_and_classes):
            poly_bbox = polygon.bounds
            if (chunk_bbox.bounds[2] > poly_bbox[0] and chunk_bbox.bounds[0] < poly_bbox[2] and
                chunk_bbox.bounds[3] > poly_bbox[1] and chunk_bbox.bounds[1] < poly_bbox[3]):
                relevant_indices.append(idx)

        relevant_polygons = [(polygons_and_classes[i][0], polygons_and_classes[i][1]) for i in relevant_indices]

        # Process each relevant polygon to extract points within the polygon
        for i, (polygon, class_name) in enumerate(relevant_polygons):
            bounding_polygon = Polygon(polygon.exterior.coords)
            prepared_polygon = prep(bounding_polygon)

            # Create a mask for points within the polygon
            mask = chunk.apply(
                lambda row: prepared_polygon.contains(Point(row.iloc[:2])),
                axis=1
            )
            cropped_points = chunk[mask]

            # Save the cropped point cloud
            if not cropped_points.empty:
                output_path = os.path.join(trees_folder, f"{prefix}${class_name}${offset}{outformat}")
                SavePointCloud(cropped_points.values, output_path)

        # Clean up
        existing_shm.close()
    except Exception as e:
        print(f"[{TimeNow()}] {inspect.currentframe().f_code.co_name}: An error occurred in processing chunk {offset}: {e}")
        print(f"[{TimeNow()}] {inspect.currentframe().f_code.co_name}: {traceback.format_exc()}")

def FilterByValue(input_data, column, minvalue, maxvalue, outputdir = None, shiftby=None):
    """
    Filters points in a point cloud based on a specified column and value range. The filtered
    data is saved to a new file with a suffix indicating the filtered column.

    Parameters:
        pointcloudpath (str): The path to the input point cloud file.
        column (int or str): The index (starting from 0) or name of the column to filter by.
        minvalue (float or str): The minimal value to keep, or a percentage string (e.g., '10%').
        maxvalue (float or str): The maximal value to keep, or a percentage string (e.g., '90%').
    """
    # Read the input file into a pandas DataFrame
    data = LoadPointCloud(input_data)

    # Rename columns for better readability
    column_names = [f"Column{i}" for i in range(data.shape[1])]
    data.columns = column_names

    # Determine the column to filter by
    if isinstance(column, int):
        filter_column = column_names[column]
    elif isinstance(column, str):
        if column in data.columns:
            filter_column = column
        else:
            raise ValueError(f"Column '{column}' not found in the file.")
    else:
        raise TypeError("Column must be an integer index or a string column name.")

    # Handle percentage min/max values
    column_values = data[filter_column]

    # if isinstance(minvalue, str) and minvalue.endswith('%'):
    #     minvalue = column_values.quantile(float(minvalue.strip('%')) / 100)
    # if isinstance(maxvalue, str) and maxvalue.endswith('%'):
    #     maxvalue = column_values.quantile(float(maxvalue.strip('%')) / 100)


    value_min, value_max = column_values.min(), column_values.max()
    # Convert percentage-based minvalue and maxvalue to linear scale
    if isinstance(minvalue, str) and minvalue.endswith('%'):
        minvalue = value_min + float(minvalue.strip('%')) / 100 * (value_max - value_min)
    if isinstance(maxvalue, str) and maxvalue.endswith('%'):
        maxvalue = value_min + float(maxvalue.strip('%')) / 100 * (value_max - value_min)


    # Filter the data
    filtered_data = data[(column_values >= minvalue) & (column_values <= maxvalue)]

    # Determine save directory and file name
    if isinstance(input_data, str):
        dir_name, base_name = os.path.split(input_data)
        file_name, ext = os.path.splitext(base_name)
    elif outputdir is not None:
        dir_name, base_name = outputdir, "cloud.txt"
        file_name, ext = os.path.splitext(base_name)
    elif outputdir is None:
        dir_name, file_name, ext = None, None, None
        print(f"[{TimeNow()}] {inspect.currentframe().f_code.co_name}: Done")

    # Save the file if a directory is determined
    if dir_name is not None:
        output_suffix = f"_filtered{column}" if isinstance(column, int) else f"_filtered{minvalue}to{maxvalue}"
        output_file = os.path.join(dir_name, f"{file_name}{output_suffix}{ext}")
        SavePointCloud(filtered_data, output_file, shiftby=shiftby)
        print(f"[{TimeNow()}] {inspect.currentframe().f_code.co_name}: Filtered point cloud has been saved to {output_file}")

    # Always return the filtered data
    return filtered_data

def FitCircleRANSAC(input_data, n=1000, d=0.01):
    '''
    Takes a slice of a pointcloud, that represents DBH of a tree and performs ransac circle fitting. n= iterations, d=outlier distance limit
    '''

    points = LoadPointCloud(input_data, "np")

    # Center the point cloud at the origin
    centroid = np.mean(points, axis=0)
    points_centered = points - centroid

    x = points_centered[:, 0]
    y = points_centered[:, 1]

    best_ic = None
    best_r = None
    best_error = float('inf')

    def residuals(params, x, y):
        xc, yc, r = params
        return ((x - xc)**2 + (y - yc)**2) - r**2

    for _ in range(n):
        # Randomly select 3 points
        idx = np.random.choice(range(len(x)), 3, replace=False)
        x1, y1, x2, y2, x3, y3 = x[idx[0]], y[idx[0]], x[idx[1]], y[idx[1]], x[idx[2]], y[idx[2]]

        # Calculate the circle parameters
        a = np.linalg.det([[x1, y1, 1], [x2, y2, 1], [x3, y3, 1]])
        b = -np.linalg.det([[x1**2+y1**2, y1, 1], [x2**2+y2**2, y2, 1], [x3**2+y3**2, y3, 1]])
        c = np.linalg.det([[x1**2+y1**2, x1, 1], [x2**2+y2**2, x2, 1], [x3**2+y3**2, x3, 1]])
        d = -np.linalg.det([[x1**2+y1**2, x1, y1], [x2**2+y2**2, x2, y2], [x3**2+y3**2, x3, y3]])

        if a == 0:
            continue

        xc, yc, r = -b/(2*a), -c/(2*a), np.sqrt((b**2+c**2-4*a*d)/(4*a**2))

        # Calculate the distances of the points from the circle center
        distances = np.sqrt((x - xc)**2 + (y - yc)**2)

        # Calculate the error (RMSE)
        error = np.sqrt(np.mean((distances - r)**2))

        if error < best_error:
            best_ic = xc, yc
            best_r = float(r)
            best_error = error

    return best_ic[0] + centroid[0], best_ic[1] + centroid[1], best_r, best_error  # add the centroid back to get the original position

def FlattenPointCloud(input_data, outputdir=None, shiftby=[0,0]):
    """
    Flattens the Z-coordinates in a point cloud file and saves the output to the specified directory
    with '_flat' added to the filename before the extension.

    Parameters:
        input_data (str or pd.DataFrame): The path to the input point cloud file or a DataFrame containing point cloud data.
        outputdir (str, optional): The directory where the output file will be saved. Defaults to the same as the input file.

    Returns:
        pandas.DataFrame: The modified point cloud DataFrame.
    """
    data=LoadPointCloud(input_data, "pddf")

    # Preserve the original Z-values (assume third column as Z)
    original_z = data.iloc[:, 2].copy()

    # Flatten Z values (set to 0)
    data.iloc[:, 2] = 0

    # Add the original Z column back as a new column
    data["Original_Z"] = original_z

    cols = list(data.columns)
    cols.insert(3, cols.pop(cols.index("Original_Z")))
    data = data[cols]

    # Handle file saving logic
    if isinstance(input_data, str):
        # Use input_data to derive directory and base name
        dir_name, base_name = os.path.split(input_data)
        file_name, ext = os.path.splitext(base_name)
    elif outputdir is not None:
        # Use outputdir with a default base name
        dir_name, base_name = outputdir, "cloud.txt"
        file_name, ext = os.path.splitext(base_name)
    else:
        # Neither input_data nor outputdir is provided; no saving
        dir_name, file_name, ext = None, None, None
        print(f"[{TimeNow()}] {inspect.currentframe().f_code.co_name}: Done")

    # Save the file if a directory is determined
    if dir_name is not None:
        output_file = os.path.join(dir_name, f"{file_name}_flat{ext}")
        # Save file without index or header
        SavePointCloud(data, output_file, shiftby=shiftby)
        print(f"[{TimeNow()}] {inspect.currentframe().f_code.co_name}: Point cloud has been saved to {output_file}")

    # Return the modified DataFrame
    return data

def GetBoundingBox(input_data):
    """
    Calculates the bounding box of a point cloud.

    Parameters:
    - input_data: np.ndarray of shape (N, 3), where N is the number of points, and each row is (x, y, z).

    Returns:
    - bbox: list containing [xmin, xmax, ymin, ymax, zmin, zmax].
    """
    input_data = LoadPointCloud(input_data, "np", "xyz")
    
    # Calculate min and max for each axis
    xmin, ymin, zmin = np.min(input_data, axis=0)
    xmax, ymax, zmax = np.max(input_data, axis=0)
    
    # Return the bounding box as a list
    bbox = [xmin, xmax, ymin, ymax, zmin, zmax]
    return bbox

def get_oriented_bounding_box(points):
    """
    Compute the oriented bounding box for the given points.
    
    Parameters:
        points (np.ndarray): An Nx3 numpy array representing the XYZ coordinates.
    
    Returns:
        obb (open3d.geometry.OrientedBoundingBox): The computed oriented bounding box.
    """
    points = LoadPointCloud(points, "np", "xyz")
    pcd = o3d.geometry.PointCloud()
    pcd.points = o3d.utility.Vector3dVector(points)
    obb = pcd.get_oriented_bounding_box()
    return obb

def GetConcaveHull(input_data, alpha=1.0):
    """
    Calculates the concave hull of a point cloud in 2D (XY plane).

    Parameters:
    - input_data: np.ndarray of shape (N, 3), where N is the number of points, and each row is (x, y, z).
    - alpha: float, the alpha value to control the concavity of the hull. Smaller values result in tighter fits.

    Returns:
    - concave_hull: Shapely Polygon object representing the concave hull.
    """
    # Extract only the XY coordinates
    xy_points = input_data[:, :2]

    # Compute the concave hull using alphashape
    concave_hull = alphashape.alphashape(xy_points, alpha)

    # Ensure the result is a Polygon (or MultiPolygon)
    if isinstance(concave_hull, MultiPolygon):
        # Merge into a single Polygon if needed
        concave_hull = max(concave_hull.geoms, key=lambda p: p.area)

    # Convert to Polygon if it’s not already one
    if not isinstance(concave_hull, Polygon):
        concave_hull = Polygon(concave_hull)

    return concave_hull

def AdjustHull(hull, buffer_distance, global_bbox):
    """
    Adjusts the concave hull by extending it with a buffer distance, ensuring it stays within the global bounding box.

    Parameters:
    - hull: Shapely Polygon object representing the concave hull.
    - buffer_distance: float, the distance to extend the hull outward (positive values expand, negative values shrink).
    - global_bbox: tuple containing (data_xmin, data_xmax, data_ymin, data_ymax, data_zmin, data_zmax), 
                   the extent of the main point cloud.

    Returns:
    - adjusted_hull: Shapely Polygon object representing the adjusted hull, clipped to the global bounding box.
    """
    if not isinstance(hull, Polygon):
        raise ValueError("Input hull must be a Shapely Polygon.")
    if len(global_bbox) != 6:
        raise ValueError("Global bounding box must contain six values: (xmin, xmax, ymin, ymax, zmin, zmax).")

    # Extract XY bounds from the global bounding box
    data_xmin, data_xmax, data_ymin, data_ymax, _, _ = global_bbox

    # Expand the hull by the buffer distance
    buffered_hull = hull.buffer(buffer_distance)

    # Create the 2D global bounding box as a Shapely Polygon
    global_bbox_polygon = box(data_xmin, data_ymin, data_xmax, data_ymax)

    # Clip the buffered hull to the global bounding box
    adjusted_hull = buffered_hull.intersection(global_bbox_polygon)

    return adjusted_hull

def LabelConnectedComponents(input_data, voxel_size=0.1, min_points=10, keep_indices=-1):
    def voxelize_point_cloud(points, voxel_size):
        """
        Voxelize the point cloud by snapping points to a 3D grid with a specified voxel size.
        """
        min_coords = points.min(axis=0)
        voxel_indices = np.floor((points - min_coords) / voxel_size).astype(int)
        return voxel_indices, min_coords

    # def map_voxels_to_points(voxel_indices, labels):
    #     """
    #     Map voxel labels back to the original points.
    #     """
    #     voxel_to_label = {tuple(voxel): label for voxel, label in zip(voxel_indices, labels.flatten())}
    #     point_labels = np.array([voxel_to_label[tuple(voxel)] for voxel in voxel_indices])
    #     return point_labels

    def filter_and_renumber_components(labeled_pointcloud, min_points):
        """
        Filter out components with fewer than `min_points` points, then renumber them such that
        the largest component gets label 1, and others follow in descending order of size.
        """
        labels = labeled_pointcloud[:, -1].astype(int)
        unique_labels, counts = np.unique(labels, return_counts=True)

        # Filter labels based on minimum size
        valid_labels = unique_labels[counts >= min_points]
        filtered_pointcloud = labeled_pointcloud[np.isin(labels, valid_labels)]

        # Sort components by size in descending order
        valid_labels_sorted = valid_labels[np.argsort(-counts[np.isin(unique_labels, valid_labels)])]

        # Create a mapping from old labels to new labels
        label_mapping = {old_label: new_label for new_label, old_label in enumerate(valid_labels_sorted, start=1)}

        # Apply the new labels
        filtered_pointcloud[:, -1] = [label_mapping[label] for label in filtered_pointcloud[:, -1]]

        return filtered_pointcloud

    # Load the point cloud from a file (replace 'your_pointcloud.txt' with your actual file)
    input_data = LoadPointCloud(input_data, "np", "all")
    original_dtype = input_data.dtype

    additional_fields = input_data[:, 3:] if input_data.shape[1] > 3 else None
    input_data = input_data[:, :3]

    # Step 1: Voxelize the point cloud
    voxel_indices, min_coords = voxelize_point_cloud(input_data, voxel_size)
    del min_coords
    # Create a binary 3D volume from voxel indices
    unique_voxels, inverse_indices = np.unique(voxel_indices, axis=0, return_inverse=True)
    del voxel_indices
    voxel_grid_shape = unique_voxels.max(axis=0) + 1
    voxel_grid = np.zeros(voxel_grid_shape, dtype=bool)
    for voxel in unique_voxels:
        voxel_grid[tuple(voxel)] = True

    # Step 2: Apply connected components labeling on the voxel grid
    labeled_voxels = cc3d.connected_components(voxel_grid, connectivity=26)
    del voxel_grid
    # Map voxel labels back to the original point cloud
    labels = labeled_voxels[tuple(unique_voxels.T)]
    del labeled_voxels, unique_voxels
    point_labels = labels[inverse_indices].astype(np.int32)
    del inverse_indices

    # Combine points with their component labels
    labeled_pointcloud = np.hstack((input_data, point_labels.reshape(-1, 1)))
    del input_data

    if additional_fields is not None:
        xyz = labeled_pointcloud[:, :3]
        # Get the remaining columns (if any)
        ccid = labeled_pointcloud[:, 3:]
        # Stack the arrays with additional_fields inserted
        labeled_pointcloud = np.hstack((xyz, additional_fields, ccid))


    # Step 3: Filter out small components
    filtered_pointcloud = filter_and_renumber_components(labeled_pointcloud, min_points=min_points)

    # Step 4: Filter by keep_indices if specified
    if keep_indices != -1:
        filtered_pointcloud = filtered_pointcloud[filtered_pointcloud[:, -1] == keep_indices]
    
    print(f"[{TimeNow()}] {inspect.currentframe().f_code.co_name}: Done")
    return filtered_pointcloud.astype(original_dtype)


def LoadPointCloud(input_data, return_type="pddf", fields="all", cpus_to_leave_free=1):
    """
    Load point cloud data

    Parameters:
    input_data : str or np.ndarray or o3d.geometry.PointCloud or pandas.DataFrame
        The input point cloud data.
    return_type : str, optional
        Output format: "pddf" (default) for DataFrame or "np" for NumPy array.
    fields : str, optional
        Fields to load: "xyz" (default), "all", or "nonull".

    max_ram_fraction : float, optional
        Fraction of total RAM to use before resorting to disk storage.

    Returns:
    pandas.DataFrame or numpy.ndarray or str
        The loaded point cloud data in-memory
    """
    global globalprocessing

    def ensure_writable(array):
        if not array.flags.writeable:
            array = array.copy()
        return array

    def Load3d(directory):
        extension = directory.split('.')[-1].lower()
        if extension in ["las", "laz"]:
            with laspy.open(directory) as f:
                las = f.read()
                valid_fields = [
                    field for i, field in enumerate(las.point_format)
                    if (fields == "all" or
                        (fields == "nonull" and not np.all(las[field.name] == 0) and not np.all(np.isnan(las[field.name]))) or
                        (fields == "xyz" and field.name in ["X", "Y", "Z"]))
                ]
                data = np.vstack([
                    las[field.name] * las.header.scale[i] + las.header.offset[i]
                    if field.name in ['X', 'Y', 'Z'] else las[field.name]
                    for i, field in enumerate(valid_fields)
                ]).T
                return ensure_writable(data)
        elif extension in ["asc", "txt", "xyz"]:
            data = np.loadtxt(directory)
            if fields == "xyz":
                data = data[:, :3]
            return ensure_writable(data)
        elif extension in ["ply", "pcd", "xyzrgb", "xyzn"]:
            pcd_o3d = o3d.io.read_point_cloud(directory)
            data = np.asarray(pcd_o3d.points)
            if fields == "xyz":
                data = data[:, :3]
            return ensure_writable(data)
        else:
            print(f"[{TimeNow()}] {inspect.currentframe().f_code.co_name}: Unsupported point cloud format.")


    # Handle input types
    if isinstance(input_data, str):
        data = Load3d(input_data)
        row_size = data.itemsize * data.shape[1]  
    elif isinstance(input_data, np.ndarray):
        data = ensure_writable(input_data)
        row_size = data.itemsize * data.shape[1]  
    elif isinstance(input_data, o3d.geometry.PointCloud):
        data = ensure_writable(np.asarray(input_data.points))
        row_size = data.itemsize * data.shape[1]  
    elif isinstance(input_data, pd.DataFrame):
        # Handle DataFrame memory usage
        data_size = input_data.memory_usage(deep=True).sum()
        if len(input_data) > 0:
            row_size = data_size // len(input_data)  # Average row size for DataFrame
        else:
            raise ValueError("Input DataFrame has no rows.")
        
        numeric_dtypes = input_data.select_dtypes(include=[np.number]).dtypes
        if len(numeric_dtypes) > 0:
            # Find the dtype with the largest size
            largest_dtype = max(numeric_dtypes, key=lambda dt: np.dtype(dt).itemsize)
        else:
            # Default to float64 if no numeric columns exist
            largest_dtype = np.float64
        data = input_data.to_numpy(dtype=largest_dtype)  # Convert DataFrame to NumPy array for further processing
    else:
        raise ValueError("Unsupported input type.")

    # Filter data based on fields parameter
    if fields == "xyz":
        data = data[:, :3]  # Only keep X, Y, Z
    elif fields == "nonull":
        # Keep only columns with non-zero and non-null values
        valid_columns = ~np.all((data == 0) | np.isnan(data), axis=0)
        data = data[:, valid_columns]

    # If data is not already a DataFrame, calculate its size as NumPy array
    if not isinstance(input_data, pd.DataFrame):
        data_size = data.nbytes



    if return_type == "pddf":
        return pd.DataFrame(data)
    elif return_type == "np":
        return data

def MergePointClouds(pc1, pc2, output_path=None):
    """
    Merge two point clouds into one and save to a specified format.

    Parameters:
    pc1 : str or pd.DataFrame or o3d.geometry.PointCloud
        The first point cloud data.
    pc2 : str or pd.DataFrame or o3d.geometry.PointCloud
        The second point cloud data.
    output_path : str
        The file path to save the merged point cloud. The format is inferred from the file extension.

    Returns:
    pd.DataFrame
        The merged point cloud as a pandas DataFrame with columns ['x', 'y', 'z'].
    """
    # Load point clouds
    pc1_data = LoadPointCloud(pc1)
    pc2_data = LoadPointCloud(pc2)

    # Merge point clouds using pandas
    merged_data = pd.concat([pc1_data, pc2_data], ignore_index=True)

    # Save the merged point cloud if an output path is provided
    if output_path is not None:
        SavePointCloud(merged_data, output_path)

    return merged_data

def MergePointCloudsByClass(trees_folder, outformat=".txt", cpus_to_leave_free = 1):
    """
    Merge point cloud files by class and delete original files using multiprocessing.

    Parameters:
    trees_folder : str
        The folder containing the point cloud files.
    outformat : str
        The format for saving the merged point cloud files.

    Returns:
    None
    """
    try:
        # Group files by class
        files_by_class = {}
        for file_name in os.listdir(trees_folder):
            if file_name.endswith(outformat):
                class_name = file_name.split('$')[1]
                if class_name not in files_by_class:
                    files_by_class[class_name] = []
                files_by_class[class_name].append(os.path.join(trees_folder, file_name))

        # Use multiprocessing to merge files for each class
        with Pool(processes=max(1, cpu_count() - cpus_to_leave_free)) as pool:
            pool.starmap(MergePointCloudsByClassHelper, [(class_name, files, trees_folder, outformat) for class_name, files in files_by_class.items()])

    except Exception as e:
        print(f"[{TimeNow()}] {inspect.currentframe().f_code.co_name}: An error occurred while merging point clouds: {e}")
        print(f"[{TimeNow()}] {inspect.currentframe().f_code.co_name}: {traceback.format_exc()}")
def MergePointCloudsByClassHelper(class_name, files, trees_folder, outformat):
    """
    Merge point cloud files for a specific class and delete original files.

    Parameters:
    class_name : str
        The class name of the point clouds.
    files : list
        List of file paths to the point clouds.
    trees_folder : str
        The folder to save the merged point cloud files.
    outformat : str
        The format for saving the merged point cloud files.

    Returns:
    None
    """
    try:
        check_stop()
        merged_cloud = None
        for file in files:
            cloud = LoadPointCloud(file)  # Load the point cloud
            if merged_cloud is None:
                merged_cloud = cloud
            else:
                merged_cloud = np.concatenate((merged_cloud, cloud), axis=0)  # Merge the point clouds

        # Save the merged point cloud
        if merged_cloud is not None:
            output_path = os.path.join(trees_folder, f"tree_{class_name}{outformat}")
            SavePointCloud(merged_cloud, output_path)

            # Delete the original files
            for file in files:
                os.remove(file)

    except Exception as e:
        print(f"[{TimeNow()}] {inspect.currentframe().f_code.co_name}: An error occurred while merging point clouds for class {class_name}: {e}")
        print(f"[{TimeNow()}] {inspect.currentframe().f_code.co_name}: {traceback.format_exc()}")

def PointcloudToRaster(input_data, gridsize=1,epsg=32633 ,shiftby = None,outputdir=None):
    """
    Converts a point cloud into a single rasterized GeoTIFF file.
    For each grid cell, calculates the Z-value (e.g., minimum or maximum).

    Parameters:
        input_data (str or np.ndarray or o3d.geometry.PointCloud):
            The input point cloud data or file path.
        gridsize (float): The size of each grid cell.
        outputdir (str, optional): Directory to save the rasterized output. If None, file is not saved.

    Returns:
        None
    """

    # Load the point cloud data using LoadPointCloud
    data = LoadPointCloud(input_data)

    # Ensure there are at least three columns for X, Y, Z
    if data.shape[1] < 3:
        raise ValueError("The input point cloud must have at least three columns (X, Y, Z).")

    # Assign column names if not already present
    column_names = ["X", "Y", "Z"] + [f"Attr_{i}" for i in range(3, data.shape[1])]
    data.columns = column_names

    # Calculate grid indices
    data['Grid_X'] = (data["X"] // gridsize).astype(int)
    data['Grid_Y'] = (data["Y"] // gridsize).astype(int)

    # Group by grid indices and find the Z-value (e.g., minimum Z in this case)
    rasterized = data.loc[data.groupby(['Grid_X', 'Grid_Y'])["Z"].idxmin()].reset_index(drop=True)

    # Get the grid extents
    unique_grids = data[['Grid_X', 'Grid_Y']].drop_duplicates()
    x_unique = unique_grids['Grid_X'].unique()
    y_unique = unique_grids['Grid_Y'].unique()

    x_min, x_max = x_unique.min(), x_unique.max()
    y_min, y_max = y_unique.min(), y_unique.max()

    # Initialize the raster array
    raster = np.full((y_max - y_min + 1, x_max - x_min + 1), np.nan)

    # Populate the raster array with Z values
    for _, row in rasterized.iterrows():
        grid_x, grid_y = int(row['Grid_X']), int(row['Grid_Y'])
        raster[grid_y - y_min, grid_x - x_min] = row['Z']

    # Define output file name
    if isinstance(input_data, str):
        dir_name, base_name = os.path.split(input_data)
        file_name, ext = os.path.splitext(base_name)
    elif outputdir is not None:
        dir_name, file_name = outputdir, "cloud_raster"
    else:
        dir_name, file_name = None, None

    if dir_name is not None:
        file_path = os.path.join(dir_name, f"{file_name}.tif")
        suffix = 1
        while os.path.exists(file_path):  # Check for existing files and append a numeric suffix
            file_path = os.path.join(dir_name, f"{file_name}_{suffix}.tif")
            suffix += 1

        # Define affine transformation for the raster
        #transform = from_origin(x_min * gridsize, (y_max + 1) * gridsize, gridsize, gridsize) ####flipped results???
        transform = from_origin(x_min * gridsize, y_min * gridsize, gridsize, -gridsize)

        # Write the raster to GeoTIFF
        with rasterio.open(
            file_path,
            'w',
            driver='GTiff',
            height=raster.shape[0],
            width=raster.shape[1],
            count=1,
            dtype=raster.dtype,
            transform=transform,
        ) as dst:
            dst.write(raster, 1)
        AssignCrsToRaster(file_path,epsg=epsg)

        if shiftby is None:
            shiftby = globals().get('shiftby', [0, 0])  # Check global scope for shiftby, else default to [0, 0]

        temp_path = os.path.join(dir_name, f"{file_name}_shifted.tif")
        ShiftRasterBy(file_path, temp_path, shiftby[0], shiftby[1])
        os.replace(temp_path, file_path)   

        print(f"[{TimeNow()}] {inspect.currentframe().f_code.co_name}: Raster saved to {file_path}")

    else:
        print(f"[{TimeNow()}] {inspect.currentframe().f_code.co_name}: Output directory not specified. Results are not saved. CRS not defined.")

def RasterizeZminZmax(input_data, gridsize=1, outputdir=None, shiftby=[0,0]):
    """
    Finds the lowest point in each coarse grid section and the highest point in finer grid sections (4x finer).
    Saves the rasterized outputs with the lowest and highest Z-values to new files and returns the DataFrames.

    Parameters:
        input_data (str or np.ndarray or o3d.geometry.PointCloud):
            The input point cloud data or file path.
        gridsize (float): The size of each grid cell for Z-min.
        outputdir (str, optional): Directory to save the output files. If None, files are not saved.

    Returns:
        pd.DataFrame, pd.DataFrame: Two DataFrames containing the rasterized point cloud with the lowest and highest Z-values.
    """

    # Load the point cloud data using LoadPointCloud
    original_data = LoadPointCloud(input_data)

    # Ensure there are at least three columns for X, Y, Z
    if original_data.shape[1] < 3:
        raise ValueError("The input point cloud must have at least three columns (X, Y, Z).")

    # Assign column names if not already present
    column_names = ["X", "Y", "Z"] + [f"Attr_{i}" for i in range(3, original_data.shape[1])]
    original_data.columns = column_names

    # Make a copy of the data for processing to avoid modifying the original input
    data = original_data.copy()
    data[["X", "Y", "Z"]] = data[["X", "Y", "Z"]].astype(np.float64)
    # Calculate coarse grid indices for Z-min
    data['Grid_X_min'] = (data["X"] // gridsize).astype(int)
    data['Grid_Y_min'] = (data["Y"] // gridsize).astype(int)

    # Group by coarse grid indices and find the minimum Z value in each grid
    rasterizedmin = data.loc[data.groupby(['Grid_X_min', 'Grid_Y_min'])["Z"].idxmin()].reset_index(drop=True)

    # Drop the grid index columns for Z-min
    rasterizedmin = rasterizedmin.drop(columns=['Grid_X_min', 'Grid_Y_min'])

    # Calculate finer grid indices for Z-max (4x finer grid)
    finer_gridsize = gridsize / 4
    data['Grid_X_max'] = (data["X"] // finer_gridsize).astype(int)
    data['Grid_Y_max'] = (data["Y"] // finer_gridsize).astype(int)

    # Group by finer grid indices and find the maximum Z value in each grid
    rasterizedmax = data.loc[data.groupby(['Grid_X_max', 'Grid_Y_max'])["Z"].idxmax()].reset_index(drop=True)

    # Drop the grid index columns for Z-max
    rasterizedmax = rasterizedmax.drop(columns=['Grid_X_max', 'Grid_Y_max'])

    if isinstance(input_data, str):
        # Use pointcloudpath to derive directory and base name
        dir_name, base_name = os.path.split(input_data)
        file_name, ext = os.path.splitext(base_name)
    elif outputdir is not None:
        # Use outputdir with a default base name
        dir_name, base_name = outputdir, "cloud.txt"
        file_name, ext = os.path.splitext(base_name)
    else:
        # Neither pointcloudpath nor outputdir is provided; no saving
        dir_name, file_name, ext = None, None, None
        print(f"[{TimeNow()}] {inspect.currentframe().f_code.co_name}: Done")

    # Save the files if a directory is determined
    if dir_name is not None:
        output_file_min = os.path.join(dir_name, f"{file_name}_rasterize_min{ext}")
        output_file_max = os.path.join(dir_name, f"{file_name}_rasterize_max{ext}")

        SavePointCloud(rasterizedmin,output_file_min,shiftby=shiftby)
        SavePointCloud(rasterizedmax,output_file_max,shiftby=shiftby)
        # rasterizedmin.to_csv(output_file_min, sep=' ', index=False, header=False)
        # rasterizedmax.to_csv(output_file_max, sep=' ', index=False, header=False)

        print(f"[{TimeNow()}] {inspect.currentframe().f_code.co_name}: Point cloud Z-min has been saved to {output_file_min}")
        print(f"[{TimeNow()}] {inspect.currentframe().f_code.co_name}: Point cloud Z-max has been saved to {output_file_max}")

    # Always return the data
    return rasterizedmin, rasterizedmax

def RemoveField(input_data, field_index=-1):
    """
    Removes a column from a NumPy array or Pandas DataFrame based on its index or name.
    
    Parameters:
    input_data (np.ndarray or pd.DataFrame): The input data.
    field_index (int or str): The index or name of the column to remove. Defaults to -1 (last column).
    
    Returns:
    np.ndarray or pd.DataFrame: The modified data with the specified column removed.
    """
    input_data = LoadPointCloud(input_data, "np")
    if isinstance(input_data, np.ndarray):
        if not isinstance(field_index, int):
            raise ValueError("For NumPy arrays, field_index must be an integer.")
        print(f"[{TimeNow()}] {inspect.currentframe().f_code.co_name}: Done")
        return np.delete(input_data, field_index, axis=1)
    elif isinstance(input_data, pd.DataFrame):
        if isinstance(field_index, int):
            field_index = input_data.columns[field_index]
        print(f"[{TimeNow()}] {inspect.currentframe().f_code.co_name}: Done")
        return input_data.drop(columns=[field_index])
    else:
        raise TypeError("Input data must be a NumPy array or Pandas DataFrame.")

def RenameField(input_data, fieldindex, newname):
    """
    Rename an existing field in a point cloud DataFrame.

    Parameters:
    -----------
    input_data : pandas.DataFrame
        The input point cloud data as a pandas DataFrame.
    fieldindex : int
        The index of the column to rename.
    newname : str
        The new name for the column.

    Returns:
    --------
    pandas.DataFrame
        The updated point cloud DataFrame with the renamed column.

    Raises:
    -------
    ValueError:
        If the fieldindex is out of range or the newname already exists in the DataFrame.
    """
    # Check if input_data is a DataFrame
    input_data = LoadPointCloud(input_data)
    if not isinstance(input_data, pd.DataFrame):
        raise ValueError("Input data must be a pandas DataFrame.")
    
    # Validate field index
    if fieldindex < 0 or fieldindex >= len(input_data.columns):
        raise ValueError(f"Field index {fieldindex} is out of range.")

    # Get the current column name
    current_name = input_data.columns[fieldindex]

    # Check for conflicts with the new column name
    if newname in input_data.columns:
        raise ValueError(f"Column name '{newname}' already exists in the DataFrame.")

    # Rename the column
    input_data = input_data.rename(columns={current_name: newname})

    # Return the updated DataFrame
    return input_data

def SavePointCloud(input_data, savepath, fields="all", shiftby = [0,0,0]):
    """
    Save the point cloud to a specified file path and format.

    Parameters:
    ----------- 
    input_data : pd.DataFrame
        A pandas DataFrame containing the point cloud data directly. Must contain at least x, y, z columns.
    savepath : str
        The file path to save the point cloud. The format is inferred from the file extension.
    """
    if shiftby is None:
        shiftby = globals().get('shiftby', [0, 0, 0])  # Check global scope for shiftby, else default to [0, 0, 0]

    input_data = LoadPointCloud(input_data, "np", fields = fields)  # Ensure input_data is a DataFrame
    # Create a copy to avoid modifying the original data
    #input_data = input_data.copy()

    # Apply shifting to x and y coordinates
    input_data = np.array(input_data, dtype="float64")
    input_data[:, :3] = input_data[:, :3] + shiftby
    # input_data[:, 0] += shiftby[0]  # Shift x #######
    # input_data[:, 1] += shiftby[1]  # Shift y
    # input_data[:, 2] += shiftby[2]  # Shift z

    # Detect file extension and infer format
    _, file_extension = os.path.splitext(savepath)
    format = file_extension.lower()[1:]  # Remove the leading dot

    if format in ["ply", "pcd", "pts", "xyzn", "xyzrgb"]: #WARNING: this if doesnt work #######
        # Convert DataFrame to Open3D PointCloud
        point_cloud = o3d.geometry.PointCloud()
        points = input_data[:, :3] + np.array(shiftby)
        point_cloud.points = o3d.utility.Vector3dVector(points)  # Use x, y, z columns

        if input_data.shape[1] >= 6:  # Assuming 4th, 5th, and 6th columns are RGB
            colors = input_data[:, 3:6] / 255.0  # Normalize to [0, 1]
            point_cloud.colors = o3d.utility.Vector3dVector(colors) 

        o3d.io.write_point_cloud(savepath, point_cloud)

    elif format in ["txt", "xyz", "asc"]: #######
        # Save as space-delimited text including all fields 
        np.savetxt(savepath, input_data, fmt='%f', delimiter=' ')

    elif format in ["las", "laz"]: #######
        try:
            # Ensure the first three columns are x, y, z
            points = input_data[:, :3] + np.array(shiftby)  # x, y, z are mandatory

            # Create LAS header and data
            header = laspy.LasHeader(point_format=6)  # Point format 6 supports additional fields
            las = laspy.LasData(header)
            las.x = points[:, 0]
            las.y = points[:, 1]
            las.z = points[:, 2]

            if input_data.shape[1] > 3:
                extra_fields = input_data[:, 3:]
                for i, col_data in enumerate(extra_fields.T, start=1):
                    field_name = f"extra_{i}"
                    dtype = col_data.dtype

                    # Determine LAS-compatible field type
                    if dtype.kind in {'i', 'u'}:  # Integer types
                        field_type = 'int32' if dtype.itemsize >= 4 else 'int16'
                    elif dtype.kind == 'f':  # Floating-point types
                        field_type = 'float64' if dtype.itemsize >= 8 else 'float32'
                    else:  # Fallback for unsupported types
                        field_type = 'uint8'

                    # Add as extra dimension
                    las.add_extra_dim(laspy.ExtraBytesParams(
                        name=field_name,
                        type=field_type
                    ))
                    las[field_name] = col_data

            las.write(savepath)
        except ImportError:
            raise ImportError("Saving to LAS/LAZ format requires the 'laspy' library. Install it via pip.")
    else:
        raise ValueError(f"Unsupported file format '{format}'. Supported formats: ply, pcd, txt, xyz, asc, las, laz, pts, xyzn, xyzrgb.")

    print(f"[{TimeNow()}] {inspect.currentframe().f_code.co_name}: Point cloud successfully saved to: {savepath}. Shifted back by {shiftby}.")

def SaveMultipleClouds(clouds_iterable, save_dir, base_name="point_cloud", extension = ".txt"):
    """
    Save multiple point clouds with unique names.

    Parameters:
    discs : list
        List of point clouds to save. Each item should be compatible with the SavePointCloud function.
    save_dir : str
        Directory to save the point clouds.
    base_name : str, optional
        Base name for the saved files. Default is "point_cloud".

    Returns:
    None
    """
    os.makedirs(save_dir, exist_ok=True)  # Ensure the directory exists

    for i, d in enumerate(clouds_iterable):
        try:
            unique_name = f"{base_name}_{i + 1}{extension}"  # Unique name for each point cloud
            save_path = os.path.join(save_dir, unique_name)
            SavePointCloud(d, save_path)
            print(f"[{TimeNow()}] {inspect.currentframe().f_code.co_name}: Saved: {save_path}")
        except:
            unique_name = f"{base_name}_{i + 1}{extension}"  # Unique name for each point cloud
            save_path = os.path.join(save_dir, unique_name)
            print(f"[{TimeNow()}] {inspect.currentframe().f_code.co_name}: Failed saving file: {save_path}")

def ShiftPointCloud(input_data, cpus_to_leave_free=1, sample_size=10000):
    '''Checks the mean XY coordinates of the point cloud and shifts the point cloud if the mean coordinates exceed 100,000.
    Output is the shifted pointcloud and the mean XY coordinates it is shifted by.'''
    global shared_pointcloud
    global shiftby
    result = LoadPointCloud(input_data, return_type="np")

    pointcloud = result
    n_points = pointcloud.shape[0]

    # Compute mean XY using a random sample
    sample_indices = np.random.choice(n_points, size=min(sample_size, n_points), replace=False)
    sample_indices = np.sort(sample_indices)

    sample_points = pointcloud[sample_indices, :3]

    mean_coords = sample_points.mean(axis=0)
    print(f"[{TimeNow()}] {inspect.currentframe().f_code.co_name}: Mean XYZ coordinates: {mean_coords}")

    # Check if mean coordinates exceed 1000
    if all(abs(mean_coords) <= 1000):
        print(f"[{TimeNow()}] {inspect.currentframe().f_code.co_name}: Mean coordinates are within limits. No shifting applied.")
        return pointcloud.astype(np.float32), [0,0,0]

    print(f"[{TimeNow()}] {inspect.currentframe().f_code.co_name}: Shifting point cloud due to large mean coordinates.")
    
       
    # Create shared memory
    shared_array = Array('d', pointcloud.size, lock=False)
    shared_np = np.frombuffer(shared_array, dtype=np.float64).reshape(pointcloud.shape)
    np.copyto(shared_np, pointcloud)  # Copy data into shared memory
    pshape = pointcloud.shape
    del pointcloud

    # Prepare chunk arguments
    chunk_size = n_points // (os.cpu_count() - cpus_to_leave_free)
    chunks = [(i, min(i + chunk_size, n_points)) for i in range(0, n_points, chunk_size)]
    args = [(start, end, mean_coords) for start, end in chunks]

    # Initialize pool and pass shared memory to workers
    with Pool(processes=os.cpu_count() - cpus_to_leave_free, initializer=init_shared_array, initargs=(shared_array, pshape)) as pool:
        pool.map(shiftpointcloud_helper_shiftchunks_ram, args)

    # Retrieve modified data from shared memory
    shifted_pointcloud = np.frombuffer(shared_array, dtype=np.float64).reshape(pshape) 
    print(f"[{TimeNow()}] {inspect.currentframe().f_code.co_name}: Point cloud XYZ coordinates shifted by {mean_coords}.")
    
    # Return the modified pointcloud along with the mean_coords
    return shifted_pointcloud.astype(np.float32), mean_coords

def shiftpointcloud_helper_shiftchunks_h5(args):
    start, end, mean_coords, filepath, dataset_name = args
    with h5py.File(filepath, 'r+') as f:
        shared_data = f[dataset_name]
        shared_data[start:end, :3] -= mean_coords
def shiftpointcloud_helper_shiftchunks_ram(args):
    start, end, mean_coords = args
    # Access the global shared memory
    shared_np = np.frombuffer(shared_pointcloud, dtype=np.float64).reshape(array_shape)
    shared_np[start:end, :3] -= mean_coords

def SORFilter(input_data, npoints = 6, sd = 1):
    """
    Apply a Statistical Outlier Removal (SOR) filter to the input point cloud.

    Parameters:
    input_data : str or np.ndarray or o3d.geometry.PointCloud or pandas.DataFrame
        The input point cloud data. It can be:
        - A string representing the file path to point cloud data (txt, xyz, asc).
        - A numpy array containing the point cloud data directly.
        - An Open3D PointCloud object.
        - A pandas DataFrame.
    npoints : int
        Number of neighboring points to use for mean distance estimation.
    sd : float
        Standard deviation multiplier threshold (nSigma).

    Returns:
    pandas.DataFrame
        The filtered point cloud as a pandas DataFrame.
    """
    # Load the input data using the LoadPointCloud function
    input_data = LoadPointCloud(input_data, return_type="pddf")
    
    # Ensure input_data is a pandas DataFrame
    if not isinstance(input_data, pd.DataFrame):
        raise ValueError("The loaded point cloud data must be a pandas DataFrame.")

    # Compute the nearest neighbors
    nbrs = NearestNeighbors(n_neighbors=npoints + 1).fit(input_data)
    distances, _ = nbrs.kneighbors(input_data)

    # Exclude the point itself from the distance calculation (first column)
    mean_distances = distances[:, 1:].mean(axis=1)

    # Calculate the threshold for outliers
    mean = mean_distances.mean()
    std_dev = mean_distances.std()
    threshold = mean + sd * std_dev

    # Filter out the points that are considered outliers
    inlier_mask = mean_distances <= threshold
    filtered_points = input_data[inlier_mask]
    print(f"[{TimeNow()}] {inspect.currentframe().f_code.co_name}: SOR filter applied.")
    # Return the filtered points as a pandas DataFrame
    return filtered_points

def SortPointCloudByXY(input_data):
    """
    Sort a point cloud based on XY coordinates, with flexibility to handle any number of fields.

    Parameters:
    input_data: Input data used by the LoadPointCloud function to retrieve a point cloud

    Returns:
    pd.DataFrame: Sorted point cloud as a Pandas DataFrame based on XY coordinates
    """
    # Load point cloud as a Pandas DataFrame
    point_cloud = LoadPointCloud(input_data, "pddf")

    # Ensure the DataFrame has at least two columns for X and Y coordinates
    if point_cloud.shape[1] < 2:
        raise ValueError("Point cloud must have at least two columns for X and Y coordinates.")

    # Sort by X (first column), then Y (second column)
    sorted_point_cloud = point_cloud.sort_values(by=[point_cloud.columns[0], point_cloud.columns[1]])

    return sorted_point_cloud.reset_index(drop=True)

def SubsamplePointCloud(input_data, method="random", percentage=50, voxel_size=0.005, min_distance = 0.01, cpus_to_leave_free=1, return_type="pddf", fields="all"):
    
    #global shared_pointcloud
    result = LoadPointCloud(input_data, return_type="np",fields=fields)
    num_cores = os.cpu_count() - cpus_to_leave_free

    if method != "spatial":
        # In-memory processing
        n_points = result.shape[0]


        # Initialize shared memory
        shared_array = Array('d', result.size, lock=False)
        shared_np = np.frombuffer(shared_array, dtype=np.float64).reshape(result.shape)
        np.copyto(shared_np, result)  # Copy data to shared memory

        # Create chunks
        chunk_size = n_points // num_cores
        chunks = [(i, min(i + chunk_size, n_points)) for i in range(0, n_points, chunk_size)]
        offsets = [0] + np.cumsum([chunk[1] - chunk[0] for chunk in chunks]).tolist()

        # Prepare arguments for workers
        if method == "random":
            args = [(start, end, offsets[i], percentage) for i, (start, end) in enumerate(chunks)]
        elif method == "voxel":
            args = [(start, end, offsets[i], voxel_size) for i, (start, end) in enumerate(chunks)]
        else:
            raise ValueError(f"Unsupported method '{method}'. Use 'random', 'spatial' or 'voxel'.")

        # Use multiprocessing.Pool for parallel processing
        with Pool(processes=num_cores, initializer=init_shared_array, initargs=(shared_array, result.shape)) as pool:
            if method == "random":
                sampled_lengths = pool.map(subsamplepointcloud_helper_random_ram, args)
            elif method == "voxel":
                sampled_lengths = pool.map(subsamplepointcloud_helper_voxel_ram, args)



        # Retrieve sampled data from shared memory
        sampled_cloud = shared_np[:sum(sampled_lengths)]
        print(f"[{TimeNow()}] {inspect.currentframe().f_code.co_name}: Subsampled point cloud to {sampled_cloud.shape[0]} points.")
        # Return the result
        if return_type == "pddf":
            return pd.DataFrame(sampled_cloud)
        return sampled_cloud

    elif method == 'spatial':
        result = subsamplepointcloud_helper_spatial_ram(result, min_distance, cpus_to_leave_free=cpus_to_leave_free)
        print(f"[{TimeNow()}] {inspect.currentframe().f_code.co_name}: Subsampled point cloud to {result.shape[0]} points.")
        return result   

def subsamplepointcloud_helper_random_h5(args):
    start, end, percentage, input_filepath, output_filepath, dataset_name, current_index = args
    with h5py.File(input_filepath, 'r') as input_file, h5py.File(output_filepath, 'r+') as output_file:
        dataset = input_file[dataset_name]
        writable_slice = dataset[start:end]
        indices = np.random.choice(len(writable_slice), size=int(len(writable_slice) * (percentage / 100)), replace=False)
        sampled_points = writable_slice[indices]
        output_file[dataset_name][current_index:current_index + len(indices)] = sampled_points
        return len(indices)
def subsamplepointcloud_helper_voxel_h5(args):
    start, end, voxel_size, input_filepath, output_filepath, dataset_name, current_index = args
    with h5py.File(input_filepath, 'r') as input_file, h5py.File(output_filepath, 'r+') as output_file:
        dataset = input_file[dataset_name]
        writable_slice = dataset[start:end]
        voxel_keys = (writable_slice[:, :3] // voxel_size).astype(int)
        unique_voxels, indices = np.unique(voxel_keys, axis=0, return_index=True)
        sampled_points = writable_slice[indices]
        output_file[dataset_name][current_index:current_index + len(indices)] = sampled_points
        return len(indices)
def subsamplepointcloud_helper_random_ram(args):
    global shared_pointcloud, array_shape
    start, end, current_index, percentage = args
    
    # Convert global shared memory to NumPy array
    shared_np = np.frombuffer(shared_pointcloud, dtype=np.float64).reshape(array_shape)
    
    # Work on the relevant slice
    writable_slice = shared_np[start:end]
    
    # Perform random sampling
    indices = np.random.choice(len(writable_slice), size=int(len(writable_slice) * (percentage / 100)), replace=False)
    num_selected = len(indices)
    
    # Write results back to shared memory
    shared_np[current_index:current_index + num_selected] = writable_slice[indices]
    
    return num_selected
def subsamplepointcloud_helper_voxel_ram(args):
    global shared_pointcloud, array_shape
    start, end, current_index, voxel_size = args
    
    # Convert global shared memory to NumPy array
    shared_np = np.frombuffer(shared_pointcloud, dtype=np.float64).reshape(array_shape)
    
    # Work on the relevant slice
    writable_slice = shared_np[start:end]
    
    # Perform voxel grid sampling
    voxel_keys = (writable_slice[:, :3] // voxel_size).astype(int)
    unique_voxels, indices = np.unique(voxel_keys, axis=0, return_index=True)
    sampled_points = writable_slice[indices]
    num_selected = sampled_points.shape[0]
    
    # Write results back to shared memory
    shared_np[current_index:current_index + num_selected] = sampled_points
    
    return num_selected
def subsamplepointcloud_helper_spatial_ram(points, min_distance, cpus_to_leave_free = 1):
    """
    Subsample a point cloud using parallel processing with in-place modifications in shared memory.
    
    Parameters:
        points (numpy.ndarray): Input point cloud.
        min_distance (float): Minimum distance between points.
    
    Returns:
        numpy.ndarray: Subsampled point cloud.
    """
    points = LoadPointCloud(points, "np", "xyz")
    num_cores = max(1, cpu_count() - cpus_to_leave_free)
    num_points = len(points)
    
    # Create shared memory for the point cloud
    shm = shared_memory.SharedMemory(create=True, size=points.nbytes)
    shared_array = np.ndarray(points.shape, dtype=points.dtype, buffer=shm.buf)
    np.copyto(shared_array, points)
    
    # Create shared memory for flags
    flag_shm = shared_memory.SharedMemory(create=True, size=num_points)
    flags = np.ndarray((num_points,), dtype=bool, buffer=flag_shm.buf)
    flags[:] = True  # Initially keep all points
    
    # Split indices into chunks
    indices_chunks = np.array_split(np.arange(num_points), num_cores)
    
    # Prepare arguments for each subprocess
    args = [(chunk, min_distance, shm.name, flag_shm.name, points.shape, points.dtype.name) for chunk in indices_chunks]
    
    try:
        # Process chunks in parallel
        with Pool(processes=num_cores) as pool:
            pool.map(subsamplepointcloud_helper2_spatial_ram, args)
        
        # Extract subsampled points based on flags
        subsampled_points = points[flags]
    finally:
        # Clean up shared memory
        shm.close()
        shm.unlink()
        flag_shm.close()
        flag_shm.unlink()
    
    return subsampled_points
def subsamplepointcloud_helper2_spatial_ram(args):
    """
    Subsample a specific set of indices from the shared point cloud in-place.
    
    Parameters:
        args (tuple): A tuple containing:
                      - indices (list): Indices of points to process.
                      - min_distance (float): Minimum distance between points.
                      - shm_name (str): Name of the shared memory object for points.
                      - flag_shm_name (str): Name of the shared memory object for flags.
                      - shape (tuple): Shape of the shared array.
                      - dtype (str): Data type of the shared array.
    
    Returns:
        None: Modifies the flags array in-place.
    """
    indices, min_distance, shm_name, flag_shm_name, shape, dtype = args
    
    # Attach to the shared memory for points
    shm = shared_memory.SharedMemory(name=shm_name)
    points = np.ndarray(shape, dtype=dtype, buffer=shm.buf)
    
    # Attach to the shared memory for flags
    flag_shm = shared_memory.SharedMemory(name=flag_shm_name)
    flags = np.ndarray((shape[0],), dtype=bool, buffer=flag_shm.buf)
    
    # Extract the points corresponding to the indices
    chunk = points[indices]
    tree = cKDTree(chunk)
    
    # Process the chunk and modify flags in-place
    for i, point in enumerate(chunk):
        if flags[indices[i]]:
            neighbors = tree.query_ball_point(point, min_distance)
            neighbors.remove(i)
            for neighbor in neighbors:
                flags[indices[neighbor]] = False  # Mark neighbors as False
    
    # Detach from shared memory
    shm.close()
    flag_shm.close()

def RemoveDuplicatePoints(cloud, min_distance=0.01, fields = "all"):
    """
    Removes duplicate points using an Octree approach (voxel downsampling) 
    while retaining additional scalar fields.

    Parameters:
        cloud (np.ndarray or str): NxM array or file path of the 3D point cloud.
        min_distance (float): Minimum allowable distance (defines voxel size).

    Returns:
        np.ndarray: Filtered point cloud with all attributes retained.
    """
    cloud = LoadPointCloud(cloud, "np", "all")  # Ensure it loads XYZ and scalar fields
    original_dtype = cloud.dtype
    xyz = cloud[:, :3]  # Extract XYZ
    extra_fields = cloud[:, 3:]  # Extract additional fields

    # Convert to Open3D format
    pcd = o3d.geometry.PointCloud()
    pcd.points = o3d.utility.Vector3dVector(xyz)

    # Voxel downsampling
    downsampled_pcd = pcd.voxel_down_sample(voxel_size=min_distance)
    downsampled_xyz = np.asarray(downsampled_pcd.points)

    # Map downsampled points to their nearest original points
    tree = cKDTree(xyz)
    _, indices = tree.query(downsampled_xyz)

    # Retrieve the corresponding attributes from the original cloud
    downsampled_fields = extra_fields[indices]

    # Concatenate XYZ with the retained attributes
    filtered_cloud = np.hstack((downsampled_xyz, downsampled_fields))
    filtered_cloud = filtered_cloud.astype(original_dtype)
    return filtered_cloud

def UnflattenPointCloud(input_data):
    """
    Reverts a flattened point cloud file by restoring the original Z values and removing the zero Z column.

    Parameters:
        pointcloudpath (str): The path to the input flattened point cloud file.
    """
    # Read the input file into a pandas DataFrame
    # Assuming the file is space-separated
    data = LoadPointCloud(input_data, "np", "all")

    # Ensure there are at least four columns (X, Y, Z, Original_Z)
    if data.shape[1] < 4:
        raise ValueError("The input file must have at least four columns (X, Y, Z, Original_Z) to unflatten.")



    data = np.delete(data, 2, axis=1)


    return data



###Mesh###
def Load3DMesh(ply_path):
    """
    Load a 3D mesh from a PLY file using PyVista.

    Args:
        ply_path (str): Path to the PLY file.

    Returns:
        pyvista.PolyData: The loaded 3D mesh object.
    """
    try:
        # Load the 3D mesh using PyVista
        mesh = pv.read(ply_path)
        
        # Check if the mesh is valid
        if not mesh.is_all_triangles():
            print("Warning: The mesh is not composed entirely of triangles.")
        
        return mesh
    
    except Exception as e:
        print(f"Error loading mesh: {e}")
        return None




def MeshToPointCloud(mesh, ptsdensity=1):
    """
    Samples points from a given PyVista mesh at a specified density per square unit.

    Parameters:
        mesh (pv.PolyData): The input 2.5D mesh.
        ptsdensity (float): Points per unit area.

    Returns:
        np.ndarray: Sampled point cloud (N, 3).
    """
    
    def sample_points_in_triangle(v0, v1, v2, num_samples):
        """
        Sample points uniformly inside a triangle using barycentric coordinates.
        
        Parameters:
            v0, v1, v2 (np.ndarray): The three vertices of the triangle.
            num_samples (int): Number of points to sample.

        Returns:
            np.ndarray: Sampled points (N, 3).
        """
        r1 = np.sqrt(np.random.uniform(0, 1, num_samples))
        r2 = np.random.uniform(0, 1, num_samples)

        points = (1 - r1)[:, None] * v0 + (r1 * (1 - r2))[:, None] * v1 + (r1 * r2)[:, None] * v2
        return points
    
    if not isinstance(mesh, pv.PolyData):
        raise ValueError("Input mesh must be a PyVista PolyData object.")

    faces = mesh.faces.reshape(-1, 4)[:, 1:]  # Extract triangle indices
    vertices = mesh.points

    sampled_points = []

    for face in faces:
        v0, v1, v2 = vertices[face]
        
        # Compute triangle area using cross product
        area = 0.5 * np.linalg.norm(np.cross(v1 - v0, v2 - v0))
        
        # Number of points to sample in this triangle
        num_samples = max(1, int(ptsdensity * area))
        
        # Sample points in the triangle
        points = sample_points_in_triangle(v0, v1, v2, num_samples)
        sampled_points.append(points)

    # Combine all sampled points
    sampled_points = np.vstack(sampled_points)
    
    return sampled_points


def MeshToShapefile(mesh, shapefile_path: str, epsg: int = 32633):
    """
    Converts a 3D or 2.5D mesh (PyVista object or .ply file) into a polygon shapefile
    matching the shape of the mesh in the XY plane, including area information and
    assigns the specified EPSG code.

    Parameters:
        mesh (str or pv.PolyData): Input mesh as a .ply file path or PyVista PolyData object.
        shapefile_path (str): Path to the output shapefile (.shp).
        epsg (int): EPSG code for the shapefile's coordinate system. Default is 32633.

    Returns:
        None
    """
    # Check if the input is a file path or a PyVista object
    if isinstance(mesh, str):
        mesh = pv.read(mesh)
    elif not isinstance(mesh, pv.PolyData):
        raise ValueError("Input mesh must be a file path or a PyVista PolyData object.")

    # Ensure the mesh points have 3 coordinates
    if mesh.points.shape[1] == 2:
        # Add a dummy Z-coordinate (e.g., zeros)
        points = np.hstack((mesh.points, np.zeros((mesh.points.shape[0], 1))))
    else:
        points = mesh.points

    # Extract XY for the convex hull
    xy_points = points[:, :2]

    # Create a 2D convex hull of the points to form the polygon
    hull = ConvexHull(xy_points)
    hull_points = points[hull.vertices]

    # Create a Polygon using Shapely
    polygon = Polygon(hull_points[:, :2])  # Only use X, Y for the polygon

    # Calculate the area of the mesh
    area = CalculateMeshArea(mesh)

    # Define the schema for the shapefile
    schema = {
        'geometry': 'Polygon',
        'properties': {'ID': 'int', 'Area': 'float'},
    }

    # Assign the specified EPSG code to the shapefile
    crs = CRS.from_epsg(epsg)

    # Write the polygon to a shapefile using Fiona
    with fiona.open(shapefile_path, mode='w', driver='ESRI Shapefile', schema=schema, crs=crs) as shp:
        shp.write({
            'geometry': mapping(polygon),
            'properties': {'ID': 1, 'Area': area},
        })

    print(f"[{TimeNow()}] {inspect.currentframe().f_code.co_name}: Polygon shapefile saved to {shapefile_path} with EPSG:{epsg}")

###Rasters###
def AssignCrsToRaster(rasterpath, epsg=32633):
    """
    Assign a Coordinate Reference System (CRS) to a raster file.

    Parameters:
        rasterpath (str): The file path of the raster to update.
        epsg (int): The EPSG code of the CRS to assign. Default is 32633.

    Returns:
        None
    """
    try:
        # Open the raster file in update mode
        with rasterio.open(rasterpath, 'r+') as dataset:
            # Assign the CRS using the EPSG code
            dataset.crs = CRS.from_epsg(epsg)
            print(f"[{TimeNow()}] {inspect.currentframe().f_code.co_name}: Successfully assigned EPSG:{epsg} to the raster.")

    except Exception as e:
        print(f"[{TimeNow()}] {inspect.currentframe().f_code.co_name}: An error occurred: {e}")

def RasterToPointCloud(raster_path):
    with rasterio.open(raster_path) as src:
        band = src.read(1)
        rows, cols = np.where(band != src.nodata)
        z = band[rows, cols]
        x, y = src.xy(rows, cols)
        points = np.column_stack((x, y, z))

        # Filter out points with NaN values
        points = points[~np.isnan(points).any(axis=1)]
    return points

def ShiftRasterBy(input_path, output_path, x_shift_meters, y_shift_meters):
    """
    Shifts a GeoTIFF raster by specified x and y offsets in meters.

    Parameters:
    - input_path: str, path to the input GeoTIFF file
    - output_path: str, path to save the shifted GeoTIFF file
    - x_shift_meters: float, shift in x direction (meters)
    - y_shift_meters: float, shift in y direction (meters)
    """
    with rasterio.open(input_path) as src:
        # Get the original transform
        original_transform = src.transform
        
        # Extract pixel size from the transform
        pixel_size_x = original_transform.a  # Pixel width
        pixel_size_y = -original_transform.e  # Pixel height (negative because Y decreases upwards)
        
        # Convert meter shifts to pixel shifts
        pixel_shift_x = x_shift_meters / pixel_size_x
        pixel_shift_y = y_shift_meters / pixel_size_y
        
        # Create the new transform by applying the pixel shifts
        new_transform = original_transform * Affine.translation(pixel_shift_x, -pixel_shift_y)
        
        # Update metadata for the new file
        new_meta = src.meta.copy()
        new_meta.update({"transform": new_transform})
        
        # Write the output file
        with rasterio.open(output_path, 'w', **new_meta) as dst:
            dst.write(src.read())
    
    print(f"Raster shifted and saved to {output_path}")

def SubtractRasters(raster1_path, raster2_path, output_path, epsg=32633):
    '''
    Values in raster1 minus raster2. Saves into a new file.
    ex.: DSM-DTM=CHM
    '''
    # Open the first raster
    with rasterio.open(raster1_path) as src1:
        raster1_data = src1.read(1)  # Read the first band
        raster1_meta = src1.meta  # Get metadata

    # Open the second raster
    with rasterio.open(raster2_path) as src2:
        raster2_data = src2.read(1, out_shape=raster1_data.shape, resampling=Resampling.bilinear)  # Read and resample to match the first raster shape

    # Ensure the two rasters have the same shape
    if raster1_data.shape != raster2_data.shape:
        raise ValueError("The rasters do not have the same shape")

    # Subtract the raster values
    result_data = raster1_data - raster2_data

    # Update metadata for the output raster
    raster1_meta.update(dtype=rasterio.float32)

    # Write the result to a new raster
    with rasterio.open(output_path, 'w', **raster1_meta) as dst:
        dst.write(result_data.astype(rasterio.float32), 1)

    AssignCrsToRaster(output_path,epsg=epsg)

    print(f"[{TimeNow()}] {inspect.currentframe().f_code.co_name}: Rasters subtracted.")
    return result_data

def WatershedCrownDelineation(rastertif, mintreeheight=5, smoothing_sigma=4, peak_local_max_footprint=(20, 20), epsg=32633):
    """
    Crown delineation using automatic marker generation (no seed points).
    Saves outputs as GeoTIFF and Shapefile with polygon areas included.
    """
    def close_holes(image, fill_value=0):
        filled_image = image.copy()
        mask = (image == 0) | np.isnan(image)
        filled_image[mask] = fill_value
        return filled_image

    # Load the raster data
    with rasterio.open(rastertif) as src:
        raster_data = src.read(1)
        transform = src.transform
        crs = src.crs

    # Close holes and apply smoothing
    raster_data_filled = close_holes(raster_data, fill_value=0)
    raster_data_smoothed = gaussian_filter(raster_data_filled, sigma=smoothing_sigma) if smoothing_sigma > 0 else raster_data_filled

    # Mask the raster to ignore areas below the minimum tree height
    mask = raster_data_smoothed >= mintreeheight
    masked_data = np.where(mask, raster_data_smoothed, 0)

    # Generate markers using local maxima
    distance = np.zeros_like(raster_data_smoothed)
    distance[mask] = raster_data_smoothed[mask]
    local_maxi = peak_local_max(distance, footprint=np.ones(peak_local_max_footprint), labels=mask)
    markers = np.zeros_like(raster_data_smoothed, dtype=int)
    markers[tuple(local_maxi.T)] = np.arange(1, local_maxi.shape[0] + 1)

    # Perform watershed segmentation
    labels = watershed(-distance, markers, mask=mask)

    # Fix data type for `shapes` function
    labels = labels.astype(np.int32)

    # Convert raster labels to polygons
    shapes_list = list(shapes(labels, transform=transform))
    polygons = [shape(geom) for geom, value in shapes_list if value != 0]
    values = [value for geom, value in shapes_list if value != 0]

    # Prepare output shapefile with polygon areas
    output_shapefile = os.path.join(os.path.dirname(rastertif), "TreeCrowns.shp") #os.path.splitext(os.path.basename(rastertif))[0] + "_TreeCrowns.shp")
    schema = {
        'geometry': 'Polygon',
        'properties': {'CrownID': 'int', 'Area': 'float'}
    }
    with fiona.open(output_shapefile, 'w', driver='ESRI Shapefile', crs=CRS.from_epsg(epsg), schema=schema) as shp:
        for poly, value in zip(polygons, values):
            shp.write({
                'geometry': mapping(poly),
                'properties': {
                    'CrownID': int(value),
                    'Area': poly.area  # Calculate polygon area
                }
            })

    print(f"[{TimeNow()}] {inspect.currentframe().f_code.co_name}: Done.")
    return labels, polygons

###Wrappers###
def MeasureProcessingTime(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        start_time = time.time()
        result = func(*args, **kwargs)
        end_time = time.time()
        processing_time = end_time - start_time

        hours, rem = divmod(processing_time, 3600)
        minutes, seconds = divmod(rem, 60)

        print(f"Processed in {int(hours)} hours, {int(minutes)} minutes, and {seconds:.2f} seconds.")
        return result
    return wrapper

###MAIN###
@MeasureProcessingTime
def EstimatePlotParameters(pointcloudpath, epsg=32633, reevaluate=False, segmentate=False, debug=False, rasterizestep=1, XSectionThickness=0.07, XSectionCount = 3, RANSACn=1000, RANSACd=0.01, WATERSHEDminheight = 5, dbhlimit = 1.5, subsamplestep=0.05, datatype= "raw", keepfields="xyz",outpcdformat="txt", cpus_to_leave_free = 4):
#    try:
        StopDropbox(), StopGoogleDrive(), StopiCloud(), StopOnedrive()
        print(f"[{TimeNow()}] {inspect.currentframe().f_code.co_name}: Started processing.")
        epsg = CheckEPSGIsMetric(epsg) #validate metric EPSG code was used. Angular projections are not supported
        check_stop() 
        #Looking for necessary files for all possible processing kinds
        if segmentate == True:
            folder = os.path.dirname(pointcloudpath)
            crownshapes = os.path.join(folder,f"{os.path.splitext(os.path.basename(pointcloudpath))[0]}-Processing", f"{os.path.splitext(os.path.basename(pointcloudpath))[0]}_TreeCrowns.shp")
            cropcloud = os.path.join(folder,f"{os.path.splitext(os.path.basename(pointcloudpath))[0]}-Processing", f"{os.path.splitext(os.path.basename(pointcloudpath))[0]}_cloud_density.txt")
            if os.path.exists(crownshapes) and os.path.exists(cropcloud):
                print(f"[{TimeNow()}] {inspect.currentframe().f_code.co_name}: Necessary files found. Segmentation started...")
                SegmentateTrees(pcdpath=cropcloud,outpcdformat=".txt", debug=True)
                return 
            else:
                check_stop()
                print(f"[{TimeNow()}] {inspect.currentframe().f_code.co_name}: Necessary files not found. New processing started, segmentation will start afterwards...")
                EstimatePlotParameters(pointcloudpath=pointcloudpath,epsg=epsg,reevaluate=False,segmentate=False,debug=False,rasterizestep=rasterizestep,XSectionThickness=XSectionThickness,XSectionCount=XSectionCount,RANSACn=RANSACn,RANSACd=RANSACd,WATERSHEDminheight=WATERSHEDminheight,subsamplestep=subsamplestep,dbhlimit=dbhlimit,datatype=datatype,outpcdformat=outpcdformat)
                EstimatePlotParameters(pointcloudpath=pointcloudpath,epsg=epsg,reevaluate=False,segmentate=True,debug=False,rasterizestep=rasterizestep,XSectionThickness=XSectionThickness,XSectionCount=XSectionCount,RANSACn=RANSACn,RANSACd=RANSACd,WATERSHEDminheight=WATERSHEDminheight,subsamplestep=subsamplestep,dbhlimit=dbhlimit,datatype=datatype,outpcdformat=outpcdformat)
                return
        check_stop() 
        if reevaluate == False:           
            cloud, folder = initial_cleanup(pointcloudpath, debug=debug, reevaluate=reevaluate,keepfields="xyz") #0
        elif reevaluate == True: #REEVALUATE IS DISCONTINUED, DOESNT WORK ANYMORE
            try:
                cloud, folder = initial_cleanup(pointcloudpath, debug=debug, reevaluate=reevaluate) #0
            except FileNotFoundError:
                print(f"[{TimeNow()}] {inspect.currentframe().f_code.co_name}: Data from previous run not found. Starting basic estimation.")
                folder = os.path.dirname(pointcloudpath)
                filename = os.path.splitext(os.path.basename(pointcloudpath))[0]
                reprocessingfolder = os.path.join(folder, f"{filename}-Processing-reevaluate")
                os.rmdir(reprocessingfolder)
                check_stop() 
                EstimatePlotParameters(pointcloudpath=pointcloudpath, epsg=epsg, reevaluate=False, segmentate=False, debug=debug, rasterizestep=rasterizestep, XSectionThickness=XSectionThickness, XSectionCount=XSectionCount, RANSACn=RANSACn, RANSACd=RANSACd,WATERSHEDminheight=WATERSHEDminheight,dbhlimit=dbhlimit,datatype=datatype,outpcdformat=outpcdformat)
                return
       
        #Setting up variables for processing functions
        if debug == False:
            debugdir = None
        else: 
            debugdir = folder #some function save outputs only if a directory is provided. Other functions save using SavePointCloud function

        check_stop()
        #Some parameters are set based on the data type provided
        pts = len(cloud)
        if datatype == "raw":
            sorpts = math.ceil(pts/250000)
            #sorpts = 100 ###Tweak
            sorsd = 0
        elif datatype == "cropped": ###Tweak
            sorpts = 6
            sorsd = 3 

        elif datatype == "iphone" or datatype == "CRP": ###Tweak
            XSectionCount = 2
            sorpts = 6
            sorsd = 1
        else:
            sorpts = 6
            sorsd = 1
            print("DATA TYPE NOT SELECTED!")
        

        subsamplestep, XSectionThickness, CCstep, CCfinestep, ptsfilter = [subsamplestep, XSectionThickness, 0.5, 0.05, 15]
        check_stop() 
        #Main processing begins here
        if reevaluate == False:
            global shiftby #Used for shifting point cloud coordinates if it is too large
            cloud, shiftby = ShiftPointCloud(cloud)
            check_stop()

            cloud = LabelConnectedComponents(cloud, CCstep, min_points=pts/100, keep_indices=1) #should filter out parts of the scan that are far from the main sample plot
            check_stop()
            cloud = RemoveField(cloud, -1) #removes the label field from LabelConnectedComponents
            #Trying to subsample clouds to united density and process with the filtering parameters
            global shared_densecloud #original pointcloud is kept in memory for later repopulation to the original density
            shared_densecloud = LoadPointCloud(cloud,"np") #Using original densecloud takes a lot of time for processing, increasing processing time by hours, but potentially improving results
            cloud = RemoveDuplicatePoints(cloud, min_distance=subsamplestep, fields=keepfields) #subsamples cloud to save computing resources in the following steps
            check_stop()
            if debug == True:
                SavePointCloud(cloud, os.path.join(folder, "SubsampledPointCloud.txt"), shiftby=shiftby) #1

            check_stop()
            minima, maxima = RasterizeZminZmax(cloud, gridsize=rasterizestep, outputdir=debugdir, shiftby=shiftby) #2 #finding DTM
            minima = SORFilter(minima,sorpts,sorsd) #3 #Filtering DTM outliers, to make the dtm accurate #Tweak once for cropped or crp iphone
            check_stop() 
            if datatype != "cropped":
                minima = SORFilter(minima,15,1) #Twice for better filtering
                minima = SORFilter(minima,15,1) #Three times for even better filtering, which is really crucial for correct cross sections extraction
            check_stop()
            if debug == True:
                SavePointCloud(minima, os.path.join(folder, "SOR.txt"),shiftby=shiftby) #3
                PointcloudToRaster(minima,1,epsg=epsg,outputdir=folder) #DTM to raster
                RenameFile(os.path.join(folder,"cloud_raster.tif"),"DTMcrude") #4
                check_stop()
            dtmmesh = DelaunayMesh25D(minima, outputdir=folder) #5 #DTM to mesh for better ground to point distance estimation
            MeshToShapefile(dtmmesh, os.path.join(folder, "PlotInfo.shp"), epsg=epsg) #6 #Turning the plot extent into a polygon shapefile
            dtmmesh = MeshToPointCloud(dtmmesh, 50)
            check_stop() 
            if debug == True:
                SavePointCloud(dtmmesh,os.path.join(folder,"DTMrefined.txt"),shiftby=[0,0,0])

            PointcloudToRaster(dtmmesh,1,epsg=epsg,shiftby=[0,0,0],outputdir=folder) #DTM to raster
            try:
                RenameFile(os.path.join(folder,"cloud_raster.tif"),"DTM") #4
            except:
                print()
            check_stop()

            cloud = CropCloudByExtent(cloud, os.path.join(folder,"cloud_delaunay_SHIFT.ply" ), cpus_to_leave_free=cpus_to_leave_free) #crop point cloud to only contain points within the reliable DTM area
            check_stop()
            if debug == True:
                SavePointCloud(cloud,os.path.join(folder, "CropByDTM.txt"),shiftby=shiftby) #7

            if datatype != "iphone": #iphone and CRP doesnt need this processing as it does not contain info about tree heights and crowns
                minima, maxima = RasterizeZminZmax(cloud, gridsize=rasterizestep, outputdir=debugdir, shiftby=shiftby) #8 finding DSM
                PointcloudToRaster(maxima,0.25,epsg=epsg,outputdir=folder) #9 DSM
                check_stop()
                try:
                    RenameFile(os.path.join(folder,"cloud_raster.tif"),"DSM") 
                except:
                    print()
                check_stop()
                chm = SubtractRasters(os.path.join(folder, "DSM.tif"), os.path.join(folder, "DTM.tif"), os.path.join(folder, "CHM.tif"),epsg=epsg) #10 #Making CHM by subtracting DSM and DTM
                check_stop()
                WatershedCrownDelineation(rastertif=os.path.join(folder,"CHM.tif"), mintreeheight=WATERSHEDminheight,epsg=epsg,smoothing_sigma=1,peak_local_max_footprint=(20,20)) #11 #Finding tree crowns to use for crown area calculation and individual tree segmentation #Tweak
            check_stop()
            
            flat = FlattenPointCloud(cloud, outputdir=debugdir,shiftby=shiftby) #12 #Flattening the point cloud to later detect tree stems
            check_stop()
            density = ComputeDensity(flat, outputdir=debugdir, shiftby=shiftby, cpus_to_leave_free=cpus_to_leave_free) #13 #Detecting tree stems based on number of points in neighborhood
            del cloud, flat, minima, maxima   #Tweak if iphone nebude to hazet chybu?
            if datatype != "iphone":
                del chm

        elif reevaluate == True: #Reusing the point cloud with density from previous run to save resources if possible
            density = cloud
            del cloud
            density, shiftby = ShiftPointCloud(density, cpus_to_leave_free=cpus_to_leave_free)
            check_stop()
            dtmmesh = os.path.join(os.path.dirname(pointcloudpath), f"{os.path.splitext(os.path.basename(pointcloudpath))[0]}-Processing", f"{os.path.splitext(os.path.basename(pointcloudpath))[0]}_cloud_delaunay.ply")
            MeshToShapefile(dtmmesh, os.path.join(folder, "PlotInfo.shp"), epsg=epsg) #NEW

            

        # from here the processing for reevaluation and evaluation is the same again
        check_stop()

        density = ChunkPointCloudBySize(density,10)
        filterdensity = []
        for c in density:
            check_stop() 
            if c.size == 0:
                continue

            c = FilterByValue(c,-1,"12.5%","100%",outputdir=None,shiftby=shiftby) #XX  #keeping only points with large number of neighbors-finding trees #Tweak the 15 % percentage, maybe create options for filtering aggresivity in future. Small agressivity leads to incomlete butts and omitted trees in some cases, but the individual stems are harder to determine and may fall into a single tree if closely neighboured.
            filterdensity.append(c)
        
        del density

        filterdensity = np.concatenate(filterdensity, axis=0)
        check_stop()
        filterdensity = LabelConnectedComponents(filterdensity, voxel_size=0.05, min_points=1, keep_indices=-1) #13 #splitting the found stems to individual pointclouds
        component_labels = np.unique(filterdensity[:, -1])
        finefilterdensity = []

        for label in component_labels:
            check_stop() 
            ccpoints = filterdensity[filterdensity[:, -1] == label][:, :] #this should take points from filterdensity variable
            cbox = GetBoundingBox(ccpoints)
            if (cbox[1] - cbox[0] >= 4) or (cbox[3] - cbox[2] >= 4): #tweak
                ccpoints = FilterByValue(ccpoints,-2,"25%","100%",outputdir=None,shiftby=shiftby) #tweak
                finefilterdensity.append(ccpoints)
            else:
                finefilterdensity.append(ccpoints)
        del filterdensity
        finefilterdensity = np.concatenate(finefilterdensity, axis=0)
        finefilterdensity = RemoveField(finefilterdensity, -1) #removing the field with old component labels
        finefilterdensity = RemoveField(finefilterdensity, -1) #removing the field with density values
        check_stop() 
        finefilterdensity = LabelConnectedComponents(finefilterdensity, voxel_size=0.05, min_points=1, keep_indices=-1)


        if debug == True:
            SavePointCloud(finefilterdensity, os.path.join(folder, os.path.join(folder,"FilterDensity.txt")),shiftby=shiftby) #14


        finefilterdensity = RemoveField(finefilterdensity,2)
        check_stop()
        if int(XSectionCount) == 1: # Define cross section disc center heights
            disc_heights = 1.3
        else:
            disc_heights = [1.3] + list(range(2, int(XSectionCount) + 1))  

        # Extract unique connected component labels
        unique_labels = set()
        unique_labels = np.unique(finefilterdensity[:, -1]) 

        discsall = []
        dtmmesh = os.path.join(folder,"cloud_delaunay_SHIFT.ply")
        if debug == True:
            SavePointCloud(finefilterdensity, os.path.join(folder, "PreprocessedTrees.txt"),shiftby=shiftby) #15

        # ###SINGLE CPU###
        check_stop() 
        discsall = process_trees(repopulated_trees=finefilterdensity, shiftby=shiftby, unique_labels=unique_labels, dtmmesh = dtmmesh, debugdir = debugdir, disc_heights = disc_heights, XSectionThickness = XSectionThickness, folder = folder, RANSACn = RANSACn, RANSACd = RANSACd, CCfinestep = CCfinestep, ptsfilter = ptsfilter, debug = debug)

        ###DONT DELETE###
        # with joblib.parallel_backend("loky", temp_folder=joblib_temp_folder):
        #     discsall = process_trees_parallel(
        #         repopulated_trees=finefilterdensity, shiftby=shiftby, unique_labels=unique_labels, dtmmesh=dtmmesh, debugdir=debugdir, disc_heights=disc_heights, 
        #         XSectionThickness=XSectionThickness, folder=folder, RANSACn=RANSACn, RANSACd=RANSACd, CCfinestep=CCfinestep, ptsfilter=ptsfilter, debug=debug,
        #         shared_densecloud=shared_densecloud, cpus_to_leave_free=cpus_to_leave_free 
        #     )


        check_stop()
        discs_results = process_discsall(discsall, folder, debug=debug, shiftby=shiftby) #15 #sorting the cross sections
        discs_results = filter_and_transform(discs_results, max_d=dbhlimit) # modifying the data for export to shapefile
        check_stop()
        save_to_shapefile(discs_results,folder,"TreeDiscs",epsg=epsg, shiftby=shiftby) #16 #saves all cross sections at all heights
        discs_dbh = filter_disc_height(discs_results, dbhlim=dbhlimit) #keeps only cross sections at DBH and if it is not avaliable, uses data from higher levels to estimate DBH based on linear taper
        check_stop()
        if datatype != "iphone":
            save_to_shapefile(discs_dbh,folder,"DetectedTrees",epsg=epsg, shiftby=[0,0]) #17 #saves the tree info (DBH, height, etc.)
            UpdateCrownIDs(os.path.join(folder, "DetectedTrees.shp"),os.path.join(folder, "TreeCrowns.shp")) #Assigns crown ID to delineated crows based on the ids of trees within the polygon

        ###Final cleanup###
        check_stop()
        if debug == False and segmentate == False:
            if datatype != "iphone":
                #os.remove(os.path.join(folder, "DTM.tif"))
                #os.remove(os.path.join(folder, "DSM.tif"))
                os.remove(os.path.join(folder, "cloud_delaunay_SHIFT.ply"))
                os.remove(os.path.join(folder, "cloud_delaunay.ply"))



        if debug == True and datatype != "iphone":
                os.rename (os.path.join(folder, "SubsampledPointCloud.txt"),os.path.join(folder, "01SubsamplePointCloud-CloudSS.txt")) #1
                os.rename (os.path.join(folder, "cloud_rasterize_min.txt"),os.path.join(folder, "02RasterizeZminZmax-CloudMin.txt")) #2
                os.rename (os.path.join(folder, "SOR.txt"),os.path.join(folder, "03SORFilter-CloudSOR.txt")) #3
                os.rename (os.path.join(folder, "DTM.tif"),os.path.join(folder, "04PointcloudToRaster-RasterDTM.tif")) #4
                os.rename (os.path.join(folder, "DTMcrude.tif"),os.path.join(folder, "04PointcloudToRaster-RasterDTMcrude.tif")) #4
                os.rename (os.path.join(folder, "DTMrefined.txt"),os.path.join(folder, "04PointcloudToRaster-CloudDTMrefined.txt")) #4
                os.rename (os.path.join(folder, "cloud_delaunay.ply"),os.path.join(folder, "05DelaunayMesh25D-MeshDTM.ply")) #5
                os.rename (os.path.join(folder, "cloud_delaunay_SHIFT.ply"),os.path.join(folder, "05DelaunayMesh25D-MeshDTMShifted.ply")) #5
                #6
                os.rename (os.path.join(folder, "CropByDTM.txt"),os.path.join(folder, "07CropCloudByExtent-CloudCrop.txt")) #7
                os.rename (os.path.join(folder, "cloud_rasterize_max.txt"),os.path.join(folder, "08RasterizeZminZmax-CloudMax.txt")) #8
                os.rename (os.path.join(folder, "DSM.tif"),os.path.join(folder, "09PointcloudToRaster-RasterDSM.tif")) #9
                os.rename (os.path.join(folder, "CHM.tif"),os.path.join(folder, "10SubtractRasters-RasterCHM.tif")) #10
                #11     
                os.rename (os.path.join(folder, "cloud_flat.txt"),os.path.join(folder, "12FlattenPointCloud-CloudFlat.txt")) #12
                os.rename (os.path.join(folder, "cloud_density.txt"),os.path.join(folder, "13ComputeDensity-CloudDensity.txt")) #13          
                os.rename (os.path.join(folder, "FilterDensity.txt"),os.path.join(folder, "14FilterByValue-CloudFilterDensity.txt")) #14 
                os.rename (os.path.join(folder, "PreprocessedTrees.txt"),os.path.join(folder, "15UnflattenPointCloud-CloudPreprocessedTrees.txt")) #15
                os.rename (os.path.join(folder, "CloudTerrainDistances.txt"),os.path.join(folder, "16process_discsall-CloudTerrainDistances.txt"))#16
                os.rename (os.path.join(folder, "StemDiscsUnprocessed.txt"),os.path.join(folder, "16process_discsall-CloudStemDiscsUnprocessed.txt"))#16
                os.rename (os.path.join(folder, "StemDiscsProcessed.txt"),os.path.join(folder, "16process_discsall-CloudStemDiscsProcessed.txt"))#16

                for f in os.listdir(os.path.join(folder)):
                    #1
                    #2
                    #3
                    #4
                    #5
                    if "PlotInfo" in f:
                        os.rename(os.path.join(folder,f),os.path.join(folder,f"06MeshToShapefile-{f}")) #6
                    #6
                    #7
                    #8
                    #9
                    #10
                    if "TreeCrowns" in f:
                        os.rename(os.path.join(folder,f),os.path.join(folder,f"11WatershedCrownDelineation-{f}")) #11
                    #12
                    #13
                    #14
                    #15
                    #16 in loop as well
                    if "grouped_" in f: 
                        os.rename(os.path.join(folder,f),os.path.join(folder,f"16process_discsall-{f}")) #16 in renames as well
                    if "TreeDiscs." in f: 
                        os.rename(os.path.join(folder,f),os.path.join(folder,f"17filter_and_transform-{f}")) #17
                    if "DetectedTrees." in f: 
                        os.rename(os.path.join(folder,f),os.path.join(folder,f"18filter_disc_height-{f}")) #18 

        # Renaming iPhone / CRP case
        if debug == True and datatype == "iphone":
                os.rename (os.path.join(folder, "SubsampledPointCloud.txt"),os.path.join(folder, "01SubsamplePointCloud-CloudSS.txt")) #1
                os.rename (os.path.join(folder, "cloud_rasterize_min.txt"),os.path.join(folder, "02RasterizeZminZmax-CloudMin.txt")) #2
                os.rename (os.path.join(folder, "SOR.txt"),os.path.join(folder, "03SORFilter-CloudSOR.txt")) #3
                os.rename (os.path.join(folder, "DTM.tif"),os.path.join(folder, "04PointcloudToRaster-RasterDTM.tif")) #4
                os.rename (os.path.join(folder, "DTMcrude.tif"),os.path.join(folder, "04PointcloudToRaster-RasterDTMcrude.tif")) #4
                os.rename (os.path.join(folder, "DTMrefined.txt"),os.path.join(folder, "04PointcloudToRaster-CloudDTMrefined.txt")) #4
                os.rename (os.path.join(folder, "cloud_delaunay.ply"),os.path.join(folder, "05DelaunayMesh25D-MeshDTM.ply")) #5
                os.rename (os.path.join(folder, "cloud_delaunay_SHIFT.ply"),os.path.join(folder, "05DelaunayMesh25D-MeshDTMShifted.ply")) #5
                #6
                os.rename (os.path.join(folder, "CropByDTM.txt"),os.path.join(folder, "07CropCloudByExtent-CloudCrop.txt")) #7
                os.rename (os.path.join(folder, "cloud_rasterize_max.txt"),os.path.join(folder, "08RasterizeZminZmax-CloudMax.txt")) #8
                os.rename (os.path.join(folder, "cloud_flat.txt"),os.path.join(folder, "09FlattenPointCloud-CloudFlat.txt")) #9
                os.rename (os.path.join(folder, "cloud_density.txt"),os.path.join(folder, "10ComputeDensity-CloudDensity.txt")) #10        
                os.rename (os.path.join(folder, "FilterDensity.txt"),os.path.join(folder, "11FilterByValue-CloudFilterDensity.txt")) #11
                os.rename (os.path.join(folder, "PreprocessedTrees.txt"),os.path.join(folder, "12UnflattenPointCloud-CloudPreprocessedTrees.txt")) #12
                os.rename (os.path.join(folder, "CloudTerrainDistances.txt"),os.path.join(folder, "13process_discsall-CloudTerrainDistances.txt"))#13
                os.rename (os.path.join(folder, "StemDiscsUnprocessed.txt"),os.path.join(folder, "13process_discsall-CloudStemDiscsUnprocessed.txt"))#13
                os.rename (os.path.join(folder, "StemDiscsProcessed.txt"),os.path.join(folder, "13process_discsall-CloudStemDiscsProcessed.txt"))#13

                for f in os.listdir(os.path.join(folder)):
                    #1
                    #2
                    #3
                    #4
                    #5
                    if "PlotInfo" in f:
                        os.rename(os.path.join(folder,f),os.path.join(folder,f"06MeshToShapefile-{f}")) #6
                    #6
                    #7
                    #8
                    #9
                    #10
                    #11
                    #12
                    #13 in loop as well
                    if "grouped_" in f: 
                        os.rename(os.path.join(folder,f),os.path.join(folder,f"13process_discsall-{f}")) #13 in renames as well
                    if "TreeDiscs." in f: 
                        os.rename(os.path.join(folder,f),os.path.join(folder,f"14filter_and_transform-{f}")) #14
                    if "DetectedTrees." in f: 
                        os.rename(os.path.join(folder,f),os.path.join(folder,f"15filter_disc_height-{f}")) #15



        elif debug == False:
            rename_files_in_directory(pointcloudpath)

        if segmentate == "Debug" and reevaluate== False:
            print("segmentate with debug files")
        if segmentate == "SegmentateOnly":
            print("segmentate with reevaluation to ensure polygons are available, debug trees available")


def DendRobotGUI():
    dendrobot_image_data = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAABAAAAAQACAYAAAB/HSuDAAAACXBIWXMAAA7EAAAOxAGVKw4bAAAJsmlUWHRYTUw6Y29tLmFkb2JlLnhtcAAAAAAAPD94cGFja2V0IGJlZ2luPSLvu78iIGlkPSJXNU0wTXBDZWhpSHpyZVN6TlRjemtjOWQiPz4KPHg6eG1wbWV0YSB4bWxuczp4PSJhZG9iZTpuczptZXRhLyIgeDp4bXB0az0iWE1QIENvcmUgNS41LjAiPgogICA8cmRmOlJERiB4bWxuczpyZGY9Imh0dHA6Ly93d3cudzMub3JnLzE5OTkvMDIvMjItcmRmLXN5bnRheC1ucyMiPgogICAgICA8cmRmOkRlc2NyaXB0aW9uIHJkZjphYm91dD0iIgogICAgICAgICAgICB4bWxuczp4bXA9Imh0dHA6Ly9ucy5hZG9iZS5jb20veGFwLzEuMC8iPgogICAgICAgICA8eG1wOkNyZWF0b3JUb29sPlpvbmVyIFBob3RvIFN0dWRpbyBYPC94bXA6Q3JlYXRvclRvb2w+CiAgICAgIDwvcmRmOkRlc2NyaXB0aW9uPgogICA8L3JkZjpSREY+CjwveDp4bXBtZXRhPgogICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgCiAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAKICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgIAogICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgCiAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAKICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgIAogICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgCiAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAKICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgIAogICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgCiAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAKICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgIAogICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgCiAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAKICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgIAogICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgCiAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAKICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgIAogICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgCiAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAKICAgICAgICAgICAgICAgICAgICAgICAgICAgCjw/eHBhY2tldCBlbmQ9InciPz7Ou9kTlQAAIABJREFUeF7s/XvQbdl22IX9xphr7f09zjn9uv24t++9kmxLlmVJ15ZskJEsjO3Y4WHABAgVUuRRpjChiqJChXIqVFxA7CpSwUqABCcEBxMM2MRgEyPLOBALiBHI6GELSVdXurqvfpw+fftxHt/37b3WHCN/jLnWXnt/3zndfft2n+5zxu/Ud/Zea6/HXHPNNdd4zTE7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")
    dendrobot_image = Image.open(BytesIO(dendrobot_image_data))
    # List to store Point Cloud Data Path entries
    pcdpath_entries = []
    tick_labels = []  # List to store tick labels for each entry
    # Variables to track the current position for adding new paths
    current_column = 7  # Start the first added row at column 7 (next to the initial entry)
    current_row = 0  # All entries start from row 0
    row_widgets = []
    # Global flag to track if the window has been resized
    has_resized_once = False
    processing_thread = None
    console_logs = []
    pause_event = threading.Event()
    pause_event.set()  # Initially set to allow processing




    def run_estimation():
        global stop_processing
        stop_processing = False  # Reset the stop flag at the start of each run
        pause_event.set()

        # Reset all tick labels before starting a new estimation run
        for label in tick_labels:
            label.config(text="")

        # Gather the input values from the GUI
        try:
            rasterizestep = float(rasterizestep_spinbox.get())
            RANSACn = int(RANSACn_spinbox.get())
            RANSACd = float(RANSACd_spinbox.get())
            XSectionThickness = float(XSectionThickness_spinbox.get())
            WATERSHEDminheight = float(WATERSHEDminheight_spinbox.get())
            XSectionCount = int(XSectionCount_spinbox.get())
            subsamplestep = float(SubsampleStep_spinbox.get())
            epsg = int(epsg_var.get().split()[0])
            segmentate = segmentate_var.get()
            #reevaluate = reevaluate_var.get()
            debug = debug_var.get()
            datatype = datatype_menu.get()
            CPUcount = int(CPUcount_spinbox.get())
            dbhlim = float(maxdbh_spinbox.get())
        except Exception as e:
            status_label.config(text=f"Invalid Parameter Values: {str(e)}")
            return


        # Process each Point Cloud Data Path
        for i, entry in enumerate(pcdpath_entries):
            try:
                check_stop()  # Check for stop or pause before processing each file

                pointcloudpath = os.path.normpath(entry.get())  # Normalize the path
                if pointcloudpath:  # Only run if there's a path set
                    # Update the status label to "Processing {pcdpath}" and force the GUI to update
                    status_label.config(text=f"Processing {shorten_path(pointcloudpath)}")
                    root.update()  # Force the GUI to update immediately

                    # Track the start time
                    start_time = time.time()

                    # Call the function to estimate plot parameters
                    EstimatePlotParameters(
                        pointcloudpath=pointcloudpath,
                        segmentate=segmentate,
                        debug=debug,
                        epsg=epsg,
                        subsamplestep=subsamplestep,
                        rasterizestep=rasterizestep,
                        XSectionThickness=float(XSectionThickness),
                        XSectionCount=int(XSectionCount),
                        RANSACn=RANSACn,
                        RANSACd=RANSACd,
                        WATERSHEDminheight=WATERSHEDminheight,
                        datatype=datatype,
                        cpus_to_leave_free=CPUcount,
                        dbhlimit = dbhlim,
                    )

                    # Track the end time
                    end_time = time.time()
                    processing_time = end_time - start_time  # Calculate the duration

                    # Format the processing time to hours, minutes, seconds
                    formatted_time = format_time(processing_time)

                    # Update the tick label to show a checkmark
                    tick_labels[i].config(text="✔️", fg="green")  # Tick mark with green color
                    # Attach the tooltip with the processing time to the tick label
                    create_tooltip(tick_labels[i], f"Processing time: {formatted_time}")
                    status_label.config(text=f"Completed processing {shorten_path(pointcloudpath)}")
                    pause_button.config(state=tk.DISABLED)
                    root.update()  # Force the GUI to update immediately

            except StopProcessException as e:
                tick_labels[i].config(text="❌", fg="red")  # Cross mark with red color
                create_tooltip(tick_labels[i], f"Aborted by user")
                status_label.config(text=f"Status: {str(e)}")
                break
            except Exception as e:
                tick_labels[i].config(text="❌", fg="red")  # Cross mark with red color
                create_tooltip(tick_labels[i], f"Error: {str(e)}")
                status_label.config(text=f"Error processing: {str(e)}")
                root.update()  # Force the GUI to update immediately

            # Check for stop after each file
            check_stop()

    def open_file_dialog(entry_field, tick_label=None):
        """Open file dialog and set the selected path to the entry. Clear the tick label if provided."""
        filename = filedialog.askopenfilename()
        entry_field.delete(0, tk.END)
        entry_field.insert(0, filename)
        
        # Clear the corresponding tick label if it exists
        if tick_label is not None:
            tick_label.config(text="")

    def get_short_path_name(long_path):
        """Convert a long path to its short path format (Windows)."""
        buffer = ctypes.create_unicode_buffer(260)
        ctypes.windll.kernel32.GetShortPathNameW(long_path, buffer, 260)
        return buffer.value

    def on_entry_change(event, tick_label):
        """Event handler to clear the tick label when the entry content changes."""
        tick_label.config(text="")

    def open_folder_dialog():
        folder_selected = filedialog.askdirectory()
        return folder_selected

    def autofill_folder(extension):
        root_dir = open_folder_dialog()  # Open dialog for folder selection
        if not root_dir:
            return  # If no folder is selected, return without further actions

        file_paths = []
        
        # Walk through the selected directory structure and find files with the given extension
        for root, dirs, files in os.walk(root_dir):
            for file in files:
                if file.endswith(f".{extension}"):
                    file_paths.append(os.path.join(root, file))
        
        # Check if any files were found
        if not file_paths:
            messagebox.showinfo("No Files Found", f"No .{extension} files were found in the selected folder.")
            return

        # Limit the number of files to 20 (including the first one for initial_pcdpath_entry)
        file_paths = file_paths[:20]

        # Populate the initial entry with the first file, if any
        if file_paths:
            initial_pcdpath_entry.delete(0, tk.END)  # Clear any existing content
            initial_pcdpath_entry.insert(0, file_paths[0])  # Insert the first file

        # Repopulate existing entries and add new ones if necessary
        for i, file_path in enumerate(file_paths[1:], start=1):
            if i < len(pcdpath_entries):  # If the entry already exists, repopulate it
                entry = pcdpath_entries[i]
                entry.delete(0, tk.END)  # Clear existing content
                entry.insert(0, file_path)  # Insert new file path
            else:  # If the entry doesn't exist, create a new one
                add_pcdpath(os.path.normpath(file_path))

        # Show a message if more than 20 files were found but only the first 20 are used
        if len(file_paths) == 20 and len(file_paths) < len(file_paths):
            show_centered_message(root,"Limit Reached", "Only the first 20 files were added.")

    def add_pcdpath(file_path=None):
        global current_column, current_row, has_resized_once, widget_width  # Ensure widget_width is global

        # Check if the limit of rows has been reached
        if current_row > 18:
            show_centered_message(root, "Limit reached", "Maximal number of entries (20) reached.")
            return  # Exit the function, preventing further execution

        # Create widgets for the new row
        entry = tk.Entry(root, width=50)
        entry.grid(row=current_row, column=current_column, padx=0, pady=5)
        pcdpath_entries.append(entry)

        browse_button = tk.Button(root, text="Browse", command=lambda: open_file_dialog(entry, tick_label))
        browse_button.grid(row=current_row, column=current_column + 1, padx=0, pady=1)

        tick_label = tk.Label(root, text="")  # Create an empty tick label
        tick_label.grid(row=current_row, column=current_column + 2, padx=1, pady=1, sticky="")
        tick_labels.append(tick_label)  # Add the tick label to the list
        
        for tick_label in tick_labels:
            tick_label.config(text="")  # Clear the tick label

        # If a file_path is provided (from autofill), insert it into the entry
        if file_path:
            entry.insert(0, file_path)

        # Save widgets to row_widgets for future removal
        row_widgets.append([entry, browse_button, tick_label])

        # Bind the event to clear the tick mark when the entry content changes
        entry.bind("<KeyRelease>", lambda event, tick_label=tick_label: on_entry_change(event, tick_label))

        # Update row and column for the next entry
        current_row += 1

        # Only resize the window once after the first entry is added
        if not has_resized_once:
            # Calculate widget width for resizing based on the first added entry
            widget_width = entry.winfo_reqwidth() + browse_button.winfo_reqwidth() + tick_label.winfo_reqwidth()
            current_width = root.winfo_width()
            root.geometry(f"970x{initial_height}")
            has_resized_once = True  # Ensure the resizing only happens once

    def show_centered_message(window, title, message):
        # Get the size and position of the main window (root)
        main_window_x = window.winfo_x()
        main_window_y = window.winfo_y()
        main_window_width = window.winfo_width()
        main_window_height = window.winfo_height()

        # Approximate the size of the message box (you can adjust these values based on your needs)
        messagebox_width = 250
        messagebox_height = 100

        # Calculate the position of the message box to center it on the root window
        x = main_window_x + (main_window_width // 2) - (messagebox_width // 2)
        y = main_window_y + (main_window_height // 2) - (messagebox_height // 2)

        # Create a Toplevel window to simulate a messagebox centered at the calculated position
        message_window = tk.Toplevel(window)
        message_window.title(title)
        message_window.geometry(f"{messagebox_width}x{messagebox_height}+{x}+{y}")
        message_window.resizable(False, False)  # Disable resizing

        # Label to display the message
        label = tk.Label(message_window, text=message, padx=10, pady=10)
        label.pack(expand=True)

        # OK button to close the window
        ok_button = tk.Button(message_window, text="   OK   ", command=message_window.destroy)
        ok_button.pack(pady=10)

        # Ensure the window is modal (blocks interaction with other windows)
        message_window.grab_set()

    def ask_for_extension():
        extension_window = tk.Toplevel(root)  # Create a new window on top of the main window
        extension_window.title("Select File Extension")
        
        # Set the size of the new window (e.g., 300x150)
        window_width = 300
        window_height = 150
        
        # Get the size of the main window (root) and calculate the position to center the new window
        main_window_x = root.winfo_x()
        main_window_y = root.winfo_y()
        main_window_width = root.winfo_width()
        main_window_height = root.winfo_height()

        # Calculate the position of the new window
        x = main_window_x + (main_window_width // 2) - (window_width // 2)
        y = main_window_y + (main_window_height // 2) - (window_height // 2)

        # Set the geometry of the new window to center it
        extension_window.geometry(f"{window_width}x{window_height}+{x}+{y}")

        # Label to guide the user
        label = tk.Label(extension_window, text="Select files extension:")
        label.pack(pady=10)

        # Dropdown (combobox) for file extensions
        extension_var = tk.StringVar(value="laz")  # Default value is "laz"
        extension_dropdown = ttk.Combobox(extension_window, textvariable=extension_var)
        extension_dropdown['values'] = ("laz", "las", "txt", "ply", "pcd","xyz", "asc", "pts", "xyzn", "xyzrgb")
        extension_dropdown.pack(pady=10)
        # Function to confirm the extension selection and proceed to folder selection
        def confirm_extension():
            selected_extension = extension_var.get()
            extension_window.destroy()  # Close the pop-up window
            autofill_folder(selected_extension)  # Pass the selected extension to the autofill function

        # Confirm button to close the pop-up and start autofill
        confirm_button = tk.Button(extension_window, text="Confirm", command=confirm_extension)
        confirm_button.pack(pady=10)

    def remove_pcdpath():
        global current_row, current_column, has_resized_once, widget_width

        # Only remove if there are added entries (keep at least the first entry)
        if len(pcdpath_entries) > 1:
            # Remove the last added row widgets
            last_row_widgets = row_widgets.pop()
            for widget in last_row_widgets:
                widget.grid_forget()  # Remove each widget from the grid

            # Remove the last entry and tick label from their respective lists
            pcdpath_entries.pop()
            tick_labels.pop()

            # Update row and column
            if current_row >0:
                current_row -= 1

            # If we are back to only the initial entry, reset the flag and resize the window
            if len(pcdpath_entries) == 1:
                current_width = root.winfo_width()
                root.geometry(initial_window_geometry)
                has_resized_once = False  # Reset the flag so the window can resize again when new entries are added

    def shorten_path(file_path):
        """
        Shorten the file path to display only the drive, parent folder, and file name.
        """
        drive, tail = os.path.splitdrive(file_path)  # Extract the drive
        head, file_name = os.path.split(tail)  # Extract the file name and the rest of the path
        parent_folder = os.path.basename(os.path.dirname(file_path))  # Get the parent folder name

        # Construct the shortened path: Drive:\...ParentFolder\FileName
        shortened_path = f"{drive}\\...\\{parent_folder}\\{file_name}"
        return shortened_path

    def open_dir_dialog(entry_field):
        dirname = filedialog.askdirectory()
        entry_field.delete(0, tk.END)
        entry_field.insert(0, dirname)

    def reset_ui():
        # Define the initial values for all fields

        initial_values = {
            'pointcloudpath': "",  # Initial Point Cloud Data Path
            'reevaluate': False,
            'segmentate': False,
            'debug': False,
            'epsg': "32633 (UTM-Czechia, Slovakia, Poland, Austria, Croatia, Denmark, Germany)",
            'subsamplestep': 0.05,
            'rasterizestep': 1,
            'XSectionThickness': 0.07,
            'XSectionCount': 3,
            'WATERSHEDminheight': 5,
            'RANSACn': 1000,
            'RANSACd': 0.01,
            'datatype': "raw",
            'CPUcount' : os.cpu_count()-4,
            'maxdbh' : 1.5,

        }

        # Clear and reset tick labels
        for label in tick_labels:
            label.config(text="")  # Clear tick marks
            # Unbind tooltips from labels
            label.unbind("<Enter>")
            label.unbind("<Leave>")

        # Reset the initial Point Cloud Data Path entry
        if pcdpath_entries:
            pcdpath_entries[0].delete(0, tk.END)
            pcdpath_entries[0].insert(0, initial_values['pointcloudpath'])

        # Remove all additional PCD path entries, leaving only the first
        while len(pcdpath_entries) > 1:
            remove_pcdpath()

        # Reset all parameter entries to their default values
        rasterizestep_spinbox.config(state="normal")
        rasterizestep_spinbox.set(initial_values['rasterizestep'])

        RANSACn_spinbox.config(state="normal")
        RANSACn_spinbox.set(initial_values['RANSACn'])

        RANSACd_spinbox.config(state="normal")
        RANSACd_spinbox.set(initial_values['RANSACd'])

        XSectionThickness_spinbox.config(state="normal")
        XSectionThickness_spinbox.set(initial_values['XSectionThickness'])

        maxdbh_spinbox.delete(0, tk.END)
        maxdbh_spinbox.insert(0, initial_values['maxdbh'])

        # Enable the spinbox and reset its value
        XSectionCount_spinbox.config(state="normal")  # Enable the spinbox
        XSectionCount_var.set(initial_values['XSectionCount'])  # Update the value using IntVar


        SubsampleStep_spinbox.config(state="normal") 
        SubsampleStep_spinbox.set(initial_values['subsamplestep'])


        epsg_menu.set( initial_values['epsg'])

        datatype_menu.set(initial_values['datatype'])
        
        WATERSHEDminheight_spinbox.config(state="normal")
        WATERSHEDminheight_spinbox.set(initial_values['WATERSHEDminheight'])

        CPUcount_spinbox.config(state="normal")  # Enable the spinbox
        CPUcount_var.set(initial_values['CPUcount'])  # Update the value using IntVar


        # Reset all Boolean (checkbox) values
        #reevaluate_var.set(initial_values['reevaluate'])
        segmentate_var.set(initial_values['segmentate'])
        debug_var.set(initial_values['debug'])

        # Reset the status label
        status_label.config(text="Status: Waiting to start processing...")

        # Reset the window geometry to its initial dimensions
        root.geometry(initial_window_geometry)

        # Inform the user that the UI has been reset
        show_centered_message(root, "Reset", "UI has been reset to its initial values.")

    class StopProcessException(Exception):
        """Custom exception to stop the processing."""
        pass

    class ListHandler(logging.Handler):
        """Custom logging handler to store log messages in a list."""
        def emit(self, record):
            log_entry = self.format(record)
            console_logs.append(log_entry)  # Append log entry to the list
            # Limit log entries to the last 50 for efficiency
            if len(console_logs) > 50:
                console_logs.pop(0)

    class PrintRedirect:
        def write(self, msg):
            if msg.strip():  # Ignore empty messages
                logging.info(msg.strip())
        def flush(self):
            pass
    
    def stop_estimation():
        global stop_processing

        stop_processing = True  # Set the flag to stop the current running process
        pause_button.config(text="Pause")
        pause_button.config(state=tk.DISABLED)
        raise StopProcessException("Processing stopped by user")

    def start_run_estimation_thread():
        # Disable the Run button to prevent multiple clicks
        run_button.config(state=tk.DISABLED)
        add_button.config(state=tk.DISABLED)
        remove_button.config(state=tk.DISABLED)
        stop_button.config(state=tk.NORMAL)
        pause_button.config(state=tk.NORMAL)
        # Start the estimation in a new thread
        thread = threading.Thread(target=run_estimation_with_reset)
        thread.start()

    def run_estimation_with_reset():
        try:
            run_estimation()
        finally:
            # Re-enable the Run button when estimation completes
            run_button.config(state=tk.NORMAL)
            add_button.config(state=tk.NORMAL)
            remove_button.config(state=tk.NORMAL)
            stop_button.config(state=tk.DISABLED)

    def create_tooltip(widget, text_getter):
        """Attach a tooltip to a widget that appears on hover."""
        def show_tooltip(event):
            tooltip = tk.Toplevel()
            tooltip.wm_overrideredirect(True)
            tooltip.geometry(f"+{event.x_root + 20}+{event.y_root + 10}")
            log_text = '\n'.join(console_logs[-5:]) if callable(text_getter) else text_getter
            label = tk.Label(tooltip, text=log_text, background="light green", relief="solid", borderwidth=1, wraplength=255, anchor="w", justify="left")
            label.pack()
            widget.tooltip = tooltip

        def hide_tooltip(event):
            if hasattr(widget, 'tooltip'):
                widget.tooltip.destroy()

        widget.bind("<Enter>", show_tooltip)
        widget.bind("<Leave>", hide_tooltip)


    def create_tooltip_terminal(widget, text_getter):
        """Attach a tooltip to a widget that appears on hover and updates its content in real time."""
        
        def show_tooltip(event):
            # Create the tooltip window
            tooltip = tk.Toplevel()
            tooltip.wm_overrideredirect(True)
            tooltip.geometry(f"+{event.x_root + 20}+{event.y_root + 10}")
            
            # Get the initial text; if text_getter is callable, call it to get the text.
            log_text = text_getter() if callable(text_getter) else text_getter
            label = tk.Label(
                tooltip,
                text=log_text,
                background="light green",
                relief="solid",
                borderwidth=1,
                wraplength=750,
                anchor="w",
                justify="left"
            )
            label.pack()
            
            # Store a reference to the tooltip in the widget
            widget.tooltip = tooltip

            def update_tooltip():
                # Only update if the tooltip still exists
                if tooltip.winfo_exists():
                    new_text = text_getter() if callable(text_getter) else text_getter
                    label.config(text=new_text)
                    # Schedule the next update (e.g., every 500 milliseconds)
                    tooltip.after(500, update_tooltip)
            
            # Start updating the tooltip text
            update_tooltip()

        def hide_tooltip(event):
            # Destroy the tooltip when the mouse leaves the widget
            if hasattr(widget, 'tooltip'):
                widget.tooltip.destroy()

        widget.bind("<Enter>", show_tooltip)
        widget.bind("<Leave>", hide_tooltip)


    def format_time(seconds):
        hours, remainder = divmod(seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        return f"{int(hours)}h {int(minutes)}m {int(seconds)}s"

    def toggle_pause():
        """Toggle the pause state and update the button label."""
        if pause_event.is_set():
            # Pause the process
            pause_event.clear()
            pause_button.config(text="Continue")
            status_label.config(text="Status: Processing paused...")
        else:
            # Resume the process
            pause_event.set()
            pause_button.config(text="Pause")
            status_label.config(text="Status: Processing resumed...")

    def on_datatype_change(event):
        if datatype_var.get() == "iphone":
            XSectionCount_spinbox.config(state="disabled")  # Disable the spinbox
            XSectionCount_var.set(1)  # Set the value to 1
        else:
            XSectionCount_spinbox.config(state="normal")  # Enable the spinbox
            XSectionCount_var.set(6)  # Set the value to 6
   
    def validate_xsection_input(value_if_allowed):
        if value_if_allowed.isdigit():
            value = int(value_if_allowed)
            return 1 <= value <= 6
        return False
    
    def validate_float001to02_input(value_if_allowed):
        try:
            value = float(value_if_allowed)
            return 0.01 <= value <= 0.2
        except ValueError:
            return False
        
    def validate_float001to05_input(value_if_allowed):
        try:
            value = float(value_if_allowed)
            return 0.01 <= value <= 0.5
        except ValueError:
            return False
    
    def validate_ransacn_input(value_if_allowed):
        if value_if_allowed.isdigit():
            value = int(value_if_allowed)
            return 100 <= value <= 10000
        return False

    def validate_WATERSHEDminheight_input(value_if_allowed):
        try:
            value = float(value_if_allowed)
            return 0.1 <= value <= 40
        except ValueError:
            return False

    def validate_rasterizestept_input(value_if_allowed):
        try:
            value = float(value_if_allowed)
            return 0.1 <= value <= 5
        except ValueError:
            return False  
    
    def validate_maxdbh_input(value_if_allowed):
        try:
            value = float(value_if_allowed)
            return 0.1 <= value <= 5
        except ValueError:
            return False

    def validate_cpu_input(new_value):
        if new_value.isdigit():
            return 1 <= int(new_value) <= os.cpu_count()
        return False
    


    def add_debug_borders(widget):
        """Recursively add borders to all child widgets for debugging."""
        try:
            widget.config(highlightthickness=1, highlightbackground="blue")
        except TclError:
            pass  # Skip widgets that do not support these options
        for child in widget.winfo_children():
            add_debug_borders(child)

    def on_epsg_change(event=None):
        """Handle selection or manual input of EPSG code."""
        user_input = epsg_var.get().strip()

        try:
            # Extract only the number before any text
            epsg_code = int(user_input.split()[0])
            epsg_var.set(str(epsg_code))  # Set the variable to just the number
            print("Using EPSG code:", epsg_code)
        except ValueError:
            messagebox.showerror("Invalid Input", "Please enter a valid integer EPSG code or select from the menu.")
            return

    # Create the GUI window
    root = tk.Tk()
    root.title("DendRobot v0.2")

    advanced_widgets = []  # List to track advanced widgets 
    root.columnconfigure(3, weight=0, minsize=25)
    root.columnconfigure(4, weight=0, minsize=25)

    # Initial Point Cloud Data Path Entry
    pcdpath_label = tk.Label(root, text="Point Cloud Data Path:")  # Create a label for "Point Cloud Data Path"
    pcdpath_label.grid(row=0, column=0, sticky="w", padx=10, pady=20)
    initial_pcdpath_entry = tk.Entry(root, width=50)
    initial_pcdpath_entry.grid(row=0, column=1)
    pcdpath_entries.append(initial_pcdpath_entry)  # Add to the list
    # Browse Button for Point Cloud Data Path
    tk.Button(root, text="Browse", command=lambda: open_file_dialog(initial_pcdpath_entry, initial_tick_label)).grid(row=0, column=2)
    # Attach the tooltip to the "Point Cloud Data Path" label
    create_tooltip(pcdpath_label, "Select the point cloud for processing.\n(laz, las, txt, ply, pcd,xyz, asc, pts, xyzn, xyzrgb)")

    # Add tick label for the initial entry
    initial_tick_label = tk.Label(root, text="")
    initial_tick_label.grid(row=0, column=3)
    tick_labels.append(initial_tick_label)

    # Add "+" button to allow adding more point cloud data paths
    add_button = tk.Button(root, text="+", command=add_pcdpath)
    add_button.grid(row=0, column=4,padx=5,sticky="")
    # Adding the "-" button under the "+" button
    remove_button = tk.Button(root, text="–", command=remove_pcdpath)
    remove_button.grid(row=1, column=4, padx=5, pady=5,sticky="")
    # Create the Autofill button
    autofill_button = tk.Button(root, text="▷", command=ask_for_extension)
    autofill_button.grid(row=2, column=4, padx=5, sticky="")
    create_tooltip(autofill_button, "Fill point clouds to entries from a folder and all its subfolders:\nRootFolder-\n         │-PlotFolder1\n         │-PlotFolder2\n         │-PlotFolder3\n              │-AnyOtherFiles\n              │→POINTCLOUD.laz←\n              │-Subfolder\n                   │→POINTCLOUD2.laz←")



    # Create a frame for the reevaluate, segmentate, debug, and advanced controls
    options_frame = tk.Frame(root)
    options_frame.grid(row=2, column=1, rowspan=1, pady=5)  # Adjust as needed for spacing

    # Reevaluate Checkbox and Tooltip
    # reevaluate_var = BooleanVar(value=False)
    # reevaluate_label = tk.Label(options_frame, text="Reevaluate:")
    # reevaluate_label.grid(row=0, column=0, sticky="s", padx=10)
    # reevaluate_checkbox = tk.Checkbutton(options_frame, variable=reevaluate_var)
    # reevaluate_checkbox.grid(row=1, column=0, padx=10, pady=5, sticky="s")
    # reevaluate_checkbox.config(state=tk.DISABLED)
    # create_tooltip(reevaluate_label, "Skips parts of point cloud processing, reuses data from previous run and recalculates the DBHs, heights, etc.")  # Attach tooltip to the label

    # Segmentate Checkbox and Tooltip
    segmentate_var = BooleanVar(value=False)
    segmentate_label = tk.Label(options_frame, text="Segmentate:")
    segmentate_label.grid(row=0, column=0, sticky="s", padx=10)
    segmentate_checkbox = tk.Checkbutton(options_frame, variable=segmentate_var)
    segmentate_checkbox.grid(row=1, column=0, padx=10, pady=5, sticky="s")
    segmentate_checkbox.config(state=tk.DISABLED)
    create_tooltip(segmentate_label, "NOT IMPLEMENTED YET\nIndividual trees will be extracted from the point cloud and filtered.\nUse together with debug to get unfiltered trees.")  # Attach tooltip to the label

    # Debug Checkbox and Tooltip
    debug_var = BooleanVar(value=False)
    debug_label = tk.Label(options_frame, text="Debug:")
    debug_label.grid(row=0, column=2, sticky="s", padx=10)
    debug_checkbox = tk.Checkbutton(options_frame, variable=debug_var)
    debug_checkbox.grid(row=1, column=2, padx=10, pady=5, sticky="s")
    create_tooltip(debug_label, "The outputs will contain most of the intermediate files from processing steps.")  # Attach tooltip to the label


    # Horizontal black line (separator) under the Advanced Mode checkbox
    separator = tk.Frame(root, height=2, bd=1, relief="sunken", bg="black")
    separator.grid(row=3, column=0, columnspan=5, sticky="ew", pady=15)  # Spanning all columns to touch edges



    EPSG_OPTIONS = {
        "3067 (ETRS89/TM35FIN(E,N))": 3067, 
        "5514 (S-JTSK Krovak)": 5514,
        "32631 (UTM-Belgium)": 32631,
        "32630 (UTM-UK, Ghana)": 32630,
        "32631 (UTM-France)": 32631,
        "32632 (UTM-Norway, Swiss)": 32632,
        "32633 (UTM-Czechia, Slovakia, Poland, Austria, Croatia, Denmark, Germany)": 32633,
        "32634 (UTM-Poland, Sweden)": 32634,
        "32635 (UTM-Finland)": 32635,
        "32636 (UTM-Turkey)": 32636,
        "32637 (UTM-Russia)": 32637,
        "32643 (UTM-India)": 32643,
        "32650 (UTM-China)": 32650,
        "32618 (UTM-Canada, USA)": 32618,
        "32723 (UTM-Brazil)": 32723,
    }



    epsg_label = tk.Label(root, text="EPSG Code:")
    epsg_label.grid(row=4, column=0, sticky="w", padx=10, pady=5)
    # Create the combobox for the Data Type field
    epsg_var = tk.StringVar(value="32633 (UTM-Czechia, Slovakia, Poland, Austria, Croatia, Denmark, Germany)")  # Default value is "32633"
    epsg_menu = ttk.Combobox(
        root,
        textvariable=epsg_var,
        values=list(EPSG_OPTIONS.keys()),
        width=20  # Adjust width as needed
    )
    epsg_menu.grid(row=4, column=1, padx=10, pady=5)

    epsg_menu.bind("<<ComboboxSelected>>", on_epsg_change)
    epsg_menu.bind("<Return>", on_epsg_change)
    # Data Type Dropdown (Combobox) for Selecting Only Valid Options
    create_tooltip(epsg_label, "Code of the reference system, the input point cloud is in. Use only projected systems (in metres, not angles). The output files will be assigned this reference system too. Choose from menu or write the code into the entry.")



    # Create the combobox for the Data Type field
    datatype_var = tk.StringVar(value="raw")  # Default value is "raw"
    datatype_menu = ttk.Combobox(
        root,
        textvariable=datatype_var,
        values=["raw", "cropped", "iphone"],
        state="readonly",  # Makes it readonly to prevent manual typing
        width=7
    )
    datatype_menu.grid(row=5, column=1)  # Adjust row and column to match your layout
    datatype_menu.set("raw")  # Set the default value
    # Bind the function to the Combobox's <<ComboboxSelected>> event
    datatype_menu.bind("<<ComboboxSelected>>", on_datatype_change)
    # Data Type Dropdown (Combobox) for Selecting Only Valid Options
    datatype_label = tk.Label(root, text="Data Type:")
    datatype_label.grid(row=5, column=0, sticky="w", padx=10, pady=5)
    create_tooltip(datatype_label, "Use 'raw' if the point cloud wasn't cropped. If it was cropped, use 'cropped' to avoid losing peripheral trees. In case iPhone LiDAR or terrestrial photogrammetry Data use 'iphone'")



    # Create the Spinbox for XSectionCount
    SubsampleStep_label =  tk.Label(root, text="Subsampling Step:")
    SubsampleStep_label.grid(row=6, column=0, sticky="w", padx=10, pady=5)
    SubsampleStep_var = tk.IntVar(value=os.cpu_count())  # Bind to IntVar
    SubsampleStep_spinbox = ttk.Spinbox(
        root,
        from_=0.01,  # Minimum value
        to=0.2,  # Maximum value
        increment=0.01,  # Step for up/down arrows
        textvariable=SubsampleStep_var,
        validate="key",  # Enable validation
        validatecommand=root.register(validate_float001to02_input, '%P'),  # Link validation function
        width=8
    )
    SubsampleStep_spinbox.grid(row=6, column=1,padx=10, pady=5)  # Adjust row/column for layout

    # Enable or reset the Spinbox as needed
    SubsampleStep_spinbox.config(state="normal")  # Enable the Spinbox
    SubsampleStep_var.set(0.05)  # Set the initial value
    create_tooltip(SubsampleStep_label,"After subsampling only one point per Step will be kept, for faster processing. DBH is computed from the original dense cloud.\nKeep this value smaller than Cross Section Thickness step.")


  # Create the Spinbox for XSectionCount
    XSectionThickness_label =  tk.Label(root, text="Cross Section Thickness:")
    XSectionThickness_label.grid(row=7, column=0, sticky="w", padx=10, pady=5)
    XSectionThickness_var = tk.IntVar(value=os.cpu_count())  # Bind to IntVar
    XSectionThickness_spinbox = ttk.Spinbox(
        root,
        from_=0.01,  # Minimum value
        to=0.5,  # Maximum value
        increment=0.01,  # Step for up/down arrows
        textvariable=XSectionThickness_var,
        validate="key",  # Enable validation
        validatecommand=root.register(validate_float001to05_input, '%P'),  # Link validation function
        width=8
    )
    XSectionThickness_spinbox.grid(row=7, column=1,padx=10, pady=5)  # Adjust row/column for layout

    # Enable or reset the Spinbox as needed
    XSectionThickness_spinbox.config(state="normal")  # Enable the Spinbox
    XSectionThickness_var.set(0.07)  # Set the initial value
    create_tooltip(XSectionThickness_label,"How thick disc will be used to compute DBH and tree location. Keep this value larger than Subsampling step.")



    # Register the validation function with Tkinter
    validate_command = (root.register(validate_xsection_input), '%P')
    # Create the Spinbox for XSectionCount
    XSectionCount_label =  tk.Label(root, text="Cross Sections Count:")
    XSectionCount_label.grid(row=8, column=0, sticky="w", padx=10, pady=5)
    XSectionCount_var = tk.IntVar(value=6)  # Bind to IntVar
    XSectionCount_spinbox = ttk.Spinbox(
        root,
        from_=1,  # Minimum value
        to=6,  # Maximum value
        increment=1,  # Step for up/down arrows
        textvariable=XSectionCount_var,
        validate="key",  # Enable validation
        validatecommand=validate_command,  # Link validation function
        width=8
    )
    XSectionCount_spinbox.grid(row=8, column=1,padx=10, pady=5)  # Adjust row/column for layout

    # Enable or reset the Spinbox as needed
    XSectionCount_spinbox.config(state="normal")  # Enable the Spinbox
    XSectionCount_var.set(3)  # Set the initial value
    create_tooltip(XSectionCount_label,"Determines levels above terrain in meters, at which the diameters will be calculated. Breast height (1.3 m) is always used.\nAdditional levels are used for identification of trees in case DBH level is not available in a tree scan.")

    # invisible_label = tk.Label(root, text="", bg=root["bg"])
    # invisible_label.grid(row=13, column=0,padx=10, pady=5)  # Place it in the desired row and column








    # Create the Spinbox for DTM resolution
    rasterizestep_label =  tk.Label(root, text="DTM Resolution:")
    rasterizestep_label.grid(row=9, column=0, sticky="w", padx=10, pady=5)
    rasterizestep_var = tk.IntVar(value=os.cpu_count())  # Bind to IntVar
    rasterizestep_spinbox = ttk.Spinbox(
        root,
        from_=0.5,  # Minimum value
        to=5,  # Maximum value
        increment=0.5,  # Step for up/down arrows
        textvariable=rasterizestep_var,
        validate="key",  # Enable validation
        validatecommand=root.register(validate_rasterizestept_input, '%P'),  # Link validation function
        width=8
    )
    rasterizestep_spinbox.grid(row=9, column=1,padx=10, pady=5)  # Adjust row/column for layout

    # Enable or reset the Spinbox as needed
    rasterizestep_spinbox.config(state="normal")  # Enable the Spinbox
    rasterizestep_var.set(1)  # Set the initial value
    create_tooltip(rasterizestep_label,"Defines the resolution of point cloud rasterization for DTM creation, expressed in its units.\nToo low or too high value may cause issues.")



  
    # Create the Spinbox for DTM resolution
    maxdbh_label =  tk.Label(root, text="Maximal DBH:")
    maxdbh_label.grid(row=10, column=0, sticky="w", padx=10, pady=5)
    maxdbh_var = tk.IntVar(value=os.cpu_count())  # Bind to IntVar
    maxdbh_spinbox = ttk.Spinbox(
        root,
        from_=0.1,  # Minimum value
        to=5,  # Maximum value
        increment=0.1,  # Step for up/down arrows
        textvariable=maxdbh_var,
        validate="key",  # Enable validation
        validatecommand=root.register(validate_maxdbh_input, '%P'),  # Link validation function
        width=8
    )
    maxdbh_spinbox.grid(row=10, column=1,padx=10, pady=5)  # Adjust row/column for layout
   # Enable or reset the Spinbox as needed
    maxdbh_spinbox.config(state="normal")  # Enable the Spinbox
    maxdbh_var.set(1.5)  # Set the initial value
    create_tooltip(maxdbh_label,"The limit for filtering out likely incorrectly fitted trees.")



    # Create the Spinbox for XSectionCount
    WATERSHEDminheight_label =  tk.Label(root, text="Watershed Min Tree Height:")
    WATERSHEDminheight_label.grid(row=11, column=0, sticky="w", padx=10, pady=5)
    WATERSHEDminheight_var = tk.IntVar(value=os.cpu_count())  # Bind to IntVar
    WATERSHEDminheight_spinbox = ttk.Spinbox(
        root,
        from_=0.1,  # Minimum value
        to=40,  # Maximum value
        increment=1,  # Step for up/down arrows
        textvariable=WATERSHEDminheight_var,
        validate="key",  # Enable validation
        validatecommand=root.register(validate_WATERSHEDminheight_input, '%P'),  # Link validation function
        width=8
    )
    WATERSHEDminheight_spinbox.grid(row=11, column=1,padx=10, pady=5)  # Adjust row/column for layout

    # Enable or reset the Spinbox as needed
    WATERSHEDminheight_spinbox.config(state="normal")  # Enable the Spinbox
    WATERSHEDminheight_var.set(5)  # Set the initial value
    create_tooltip(WATERSHEDminheight_label,"The lowest threshold value for CHM estimation. Features below the threshold will not be considered for tree crown detection.")







    # Create the Spinbox for XSectionCount
    RANSACn_label =  tk.Label(root, text="RANSAC Iterations:")
    RANSACn_label.grid(row=12, column=0, sticky="w", padx=10, pady=5)
    RANSACn_var = tk.IntVar(value=os.cpu_count())  # Bind to IntVar
    RANSACn_spinbox = ttk.Spinbox(
        root,
        from_=100,  # Minimum value
        to=10000,  # Maximum value
        increment=100,  # Step for up/down arrows
        textvariable=RANSACn_var,
        validate="key",  # Enable validation
        validatecommand=root.register(validate_ransacn_input, '%P'),  # Link validation function
        width=8
    )
    RANSACn_spinbox.grid(row=12, column=1,padx=10, pady=5)  # Adjust row/column for layout

    # Enable or reset the Spinbox as needed
    RANSACn_spinbox.config(state="normal")  # Enable the Spinbox
    RANSACn_var.set(1000)  # Set the initial value
    create_tooltip(RANSACn_label,"Ammount of DBH RANSAC circle fitting iterations.")








    # Create the Spinbox for XSectionCount
    RANSACd_label =  tk.Label(root, text="Outlier Distance Threshold:")
    RANSACd_label.grid(row=13, column=0, sticky="w", padx=10, pady=5)
    RANSACd_var = tk.IntVar(value=os.cpu_count())  # Bind to IntVar
    RANSACd_spinbox = ttk.Spinbox(
        root,
        from_=0.01,  # Minimum value
        to=0.2,  # Maximum value
        increment=0.01,  # Step for up/down arrows
        textvariable=RANSACd_var,
        validate="key",  # Enable validation
        validatecommand=root.register(validate_float001to02_input, '%P'),  # Link validation function
        width=8
    )
    RANSACd_spinbox.grid(row=13, column=1,padx=10, pady=5)  # Adjust row/column for layout

    # Enable or reset the Spinbox as needed
    RANSACd_spinbox.config(state="normal")  # Enable the Spinbox
    RANSACd_var.set(0.01)  # Set the initial value
    create_tooltip(RANSACd_label,"Severity of DBH RANSAC outlier estimation. Lower is more severe and may exclude non-noise points too.")








    # Create the Spinbox for XSectionCount
    CPUcount_label =  tk.Label(root, text="Used CPU cores:")
    CPUcount_label.grid(row=14, column=0, sticky="w", padx=10, pady=5)
    CPUcount_var = tk.IntVar(value=os.cpu_count())  # Bind to IntVar
    CPUcount_spinbox = ttk.Spinbox(
        root,
        from_=1,  # Minimum value
        to=os.cpu_count(),  # Maximum value
        increment=1,  # Step for up/down arrows
        textvariable=CPUcount_var,
        validate="key",  # Enable validation
        validatecommand=root.register(validate_cpu_input, '%P'),  # Link validation function
        width=8
    )
    CPUcount_spinbox.grid(row=14, column=1,padx=10, pady=5)  # Adjust row/column for layout

    # Enable or reset the Spinbox as needed
    CPUcount_spinbox.config(state="normal")  # Enable the Spinbox
    CPUcount_var.set(os.cpu_count()-4)  # Set the initial value
    create_tooltip(CPUcount_label,"Determines how many CPU cores will be used for processing. Larger count increases speed, but also RAM usage and might cause memory error.")









    # Horizontal black line (separator) above the Run Estimation button
    separator_above_run = tk.Frame(root, height=2, bd=1, relief="sunken", bg="black")
    separator_above_run.grid(row=15, column=0, columnspan=5, sticky="ew", pady=15)  # Spanning all columns to touch edges

    # Create a frame to hold the "Run" and "Stop" buttons for better layout control
    button_frame = tk.Frame(root)
    button_frame.grid(row=16, column=1,columnspan=3, rowspan=2, pady=(10, 30), sticky="nsew")

    # Run button
    bold_font = font.Font(weight="bold")
    run_button = tk.Button(button_frame, text="Run Estimation", font=bold_font, command=start_run_estimation_thread)
    run_button.grid(row=0, column=0, padx=5, sticky="s")
    create_tooltip(run_button,"Start processing")
    
    # Pause/Continue button
    pause_button = tk.Button(button_frame, text="Pause", font=bold_font, command=toggle_pause)
    pause_button.grid(row=0, column=1, padx=5, sticky="s")
    pause_button.config(state=tk.DISABLED)

    # Stop button
    stop_button = tk.Button(button_frame, text="Stop", font=bold_font, command=stop_estimation)
    stop_button.grid(row=0, column=2, padx=5, sticky="s")
    stop_button.config(state=tk.DISABLED) #Initially disabled

    # Reset button
    reset_button = tk.Button(root, text="↻", command=reset_ui)
    reset_button.grid(row=2, column=0, padx=5, sticky= "")
    create_tooltip(reset_button, "Reset to default values.")

    # Horizontal black line (separator) above the Run Estimation button
    separator_above_status = tk.Frame(root, height=2, bd=1, relief="sunken", bg="black")
    separator_above_status.grid(row=18, column=0, columnspan=5, sticky="ew", pady=15)  # Spanning all columns to touch edges

    # Status Label (Global)
    log_handler = ListHandler()
    log_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
    logging.getLogger().addHandler(log_handler)
    logging.getLogger().setLevel(logging.INFO)
    sys.stdout = PrintRedirect()
    sys.stderr = PrintRedirect()
    status_label = tk.Label(root, text="Status: Waiting to start processing...", font=("Helvetica", 10), wraplength=580)
    status_label.grid(row=19, column=0, columnspan=5, padx=10, pady=10, sticky="w")
    create_tooltip_terminal(status_label, lambda: '\n'.join(console_logs[-5:]))


    # Resize image (adjust these dimensions as per your space calculations, maintaining aspect ratio)
    max_width, max_height = 150, 200  # Example dimensions to fit into the right space
    dendrobot_image.thumbnail((max_width, max_height), Image.Resampling.LANCZOS)
    # Convert the image to a format Tkinter can use
    dendrobot_photo = ImageTk.PhotoImage(dendrobot_image)

    # Create a label to hold the image and place it at the designated position
    dendrobot_label = tk.Label(root, image=dendrobot_photo)
    dendrobot_label.image = dendrobot_photo  # Keep a reference to avoid garbage collection
    dendrobot_label.grid(row=11,rowspan=4, column=1, sticky="e", columnspan=3)
    dendrobot_label.lower()
    # Resize image while maintaining aspect ratio for the window icon
    icon_max_size = (32, 32)  # Icons are usually small, so 32x32 is a good size
    dendrobot_icon_image = dendrobot_image.copy()  # Make a copy for the icon use
    dendrobot_icon_image.thumbnail(icon_max_size)  # Resize for the icon

    # Convert the icon image to a format Tkinter can use
    dendrobot_icon_photo = ImageTk.PhotoImage(dendrobot_icon_image)

    # Set the window icon to the DendRobot image
    root.iconphoto(False, dendrobot_icon_photo)
    initial_width = "590"
    initial_height = "750"
    initial_window_geometry = f"{initial_width}x{initial_height}"
    root.geometry(initial_window_geometry)



    # Call the function for the root window
    #add_debug_borders(root)
    # Start the GUI event loop
    root.mainloop()


###Run from Code###
##Since multiprocessing is used, if __name__ =="__main__" is necessary
# if __name__ == "__main__": 
##Multiple files in directory
#     folder = r"D:\MarekHrdina\Dropbox\Projekty\OrbisTurku\03Processed"
#     for file in os.listdir(folder):
#         if file.endswith(".laz"):
#             filepath = os.path.join(folder, file)
#             #print(filepath)
#             EstimatePlotParameters(filepath, debug=False, epsg=3067, datatype="raw", reevaluate=False, segmentate=False)

##Single file
    # cloud = r"D:\MarekHrdina\Dropbox\Projekty\testing-pcds\farotree.laz"
    # EstimatePlotParameters(cloud, debug=True, epsg=3067, datatype="iphone", reevaluate=False, segmentate=False)


###Run GUI###
if __name__ == '__main__':
    mp.freeze_support()
    DendRobotGUI() 
