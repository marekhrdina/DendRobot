# DendRobot
<p align="center">
    <img src="/images/dbvlese.png" alt="DendRobot" >
</p>
Accurate and fast forest structure assessment is important for ecological research and adaptable forest management especially in the time of global climatic change. Hereby DendRobot is presented, an innovative software pipeline developed to automate the evaluation of forest sample plots or entire stands using terrestrial LiDAR scans. DendRobot employs a new tree-detection algorithm, together with other trusted methods, to process 3D LiDAR data, delivering essential forestry metrics such as Diameter at Breast Height (DBH), tree height, locations and crown projection area at fine level scale. Additionally, Digital Terrain Models (DTM), Digital Surface Models (DSM), and Canopy Height Models (CHM) with user-defined precision are also outputs of the processing, that may contribute to more advanced analyses of forest environment and management planning.

DendRobot should serve as a comprehensive tool for researchers, forest managers and students, enabling efficient, data-driven decision-making with minimal required manual intervention. Initial tests conducted in complex forest environments demonstrate the pipeline's ability to streamline workflows and produce large-scale forest inventory data, achieving accuracy comparable to other state-of-the-art methods.

# How to use
DendRobot can be used in both **GUI mode** and **code form**. By installing the required dependencies from `requirements.txt` and running `DendRobot.py`, the GUI will appear. However, it is possible to disable the GUI by commenting out its call and using the main function **`EstimatePlotParameters(pointcloud)`**, which should be placed inside a guard statement as described in the code.

Another, though slower, option is to download the `.exe` file from [DendRobot Download Page](https://dendrobot.czu.cz/download). It provides the same functionalities but runs slower due to multiprocessing backend issues related to packaging the program into an executable file.


# Input Parameters
The input parameters should be adjusted according to the specific input data, either by changing the **Data Type** parameter or by modifying individual parameters manually. The default settings are optimized for most terrestrial or mobile LiDAR scans.

| Parameter Name               | Type    | Recommended Range                          | Default Value | Meaning |
|------------------------------|---------|--------------------------------------------|--------------|---------|
| Point Cloud Data Path        | String  | N/A                                        | None         | Path of a point cloud file to be processed. |
| Debug                        | Boolean | True / False                               | False        | If set to True, intermediate files at each processing step will be generated and saved to the computer. |
| EPSG Code                    | Integer | Projected Coordinate Systems only          | 32633        | If the point cloud is georeferenced, the processing outputs will be assigned the specified EPSG code. |
| Data Type                    | String  | 'MLS Raw'; 'MLS Cropped'; 'iPhone LiDAR'; 'CRP'; 'UAV LiDAR' | 'MLS Raw'  | The source of the point cloud. Based on the selected option, other parameters will be adjusted to more appropriate default values, and the processing algorithm will vary accordingly. |
| Subsampling Step             | Float   | 0.01 to 0.2                                | 0.05         | The minimum step size between points in the subsampled point cloud. |
| Cross Section Thickness      | Float   | 0.01 to 1.0                                | 0.07         | The size of the cross-section, which will later be used for RANSAC Circle Fitting. |
| Cross Sections Count         | Integer | 1 to Any                                     | 3            | The number of cross-sections extracted for each tree, which will be used for RANSAC Circle Fitting. |
| Cross Section Step      | Float   | 0.1 to 1.0                                | 1         | Determines how far from each other the cross-sections will be made. |
| DTM Resolution               | Float   | 0.5 to 5                                   | 1            | The grid step for detecting minima within each grid cell. The DSM is generated at four times finer resolution. |
| Filter-Chunk Size            | Float   | 1 to 100                                   | 10           | The sensitivity of density filtering to smaller objects. Higher values result in detecting only larger trees, whereas excessively low values may lead to improper terrain removal. |
| Maximal DBH                 | Float   | 0.1 to 5                                   | 1.5          | A threshold for filtering out trees with unrealistic DBH estimate. |
| Watershed Min. Tree Height   | Float   | 0.1 to 40                                  | 5            | A threshold for eliminating crown detection in trees below a certain size. |
| RANSAC Iterations           | Integer | 100 to 10000                               | 1000         | The number of attempts to find the best-fitting DBH for a given cross-section. |
| Outlier Distance Threshold   | Float   | 0.01 to 0.1                                | 0.01         | The outlier threshold, specifying how far from the currently fitted circle a point must be to be considered an outlier. |
| Used CPU cores               | Integer | 1 to All                                   | All - 4      | Defines the number of CPU cores allocated to reduce processing time. Excessively high values may lead to RAM overuse and processing failure. |

# Outputs
Basic outputs are provided in **.shp** and **.tiff** formats. However, by enabling **"Debug"**, even point clouds from intermediate results will be saved in the processing folder in **.txt** format as well.
<p align="center">
    <img src="/images/raw.png" alt="illustration1" width="45%">
    <img src="/images/identificationdetail.png" alt="illustration2" width="45%">
</p>

<p align="center">
    <img src="/images/processing.png" alt="illustration3" width="45%">
    <img src="/images/processingdetail.png" alt="illustration4" width="45%">
</p>

<p align="center">
    <img src="/images/shapefiles.png" alt="illustration5" width="45%">
    <img src="/images/treetaper.png" alt="illustration6" width="45%">
</p>

# Known issues
• def ComputeDensity(), def GetTerrainDistances() and def FitCrossSections() need to use the "threading" backend instead of "multiprocessing" while packed into exe. This is slower.

# Autorship
**Authors:**  
Marek Hrdina¹\*  

¹ Faculty of Forestry and Wood Sciences, Czech University of Life Sciences Prague,  
Kamýcká 129, 16500 Prague, Czech Republic  

\* **Corresponding author:** [hrdinam@fld.czu.cz](mailto:hrdinam@fld.czu.cz)

