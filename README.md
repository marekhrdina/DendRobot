# DendRobot
<p align="center">
    <img src="/images/dbvlese.png" alt="DendRobot" >
</p>
Accurate and fast forest structure assessment is important for ecological research and adaptable forest management especially in the time of global climatic change. Hereby DendRobot is presented, an innovative software pipeline developed to automate the evaluation of forest sample plots or entire stands using terrestrial LiDAR scans. DendRobot employs a new stem-detection algorithm and individual tree detection algorithm, together with other trusted methods, to process 3D LiDAR data, delivering essential forestry metrics such as Diameter at Breast Height (DBH), tree height, locations and crown projection area at fine level scale. Additionally, Digital Terrain Models (DTM), Digital Surface Models (DSM), and Canopy Height Models (CHM) with user-defined precision are also outputs of the processing, that may contribute to more advanced analyses of forest environment and management planning.

DendRobot should serve as a comprehensive tool for researchers, forest managers and students, enabling efficient, data-driven decision-making with minimal required manual intervention. Initial tests conducted in complex forest environments demonstrate the pipeline's ability to streamline workflows and produce large-scale forest inventory data, achieving accuracy comparable to other state-of-the-art methods.

# How to use
DendRobot can be used in both **GUI mode** and **code form**. By installing the required dependencies from `requirements.txt` and running `DendRobot.py`, the GUI will appear. However, it is possible to disable the GUI by commenting out its call and using the main function **`EstimatePlotParameters(pointcloud)`**.

Another option is to download the `.exe` file from [DendRobot Download Page](https://dendrobot.czu.cz/download). It provides the same functionalities within simple graphical user interface.


# Input Parameters
The input parameters should be adjusted according to the specific input data, either by changing the **Data Type** parameter or by modifying individual parameters manually. The default settings are optimized for most terrestrial or mobile LiDAR scans.

| Parameter | Type | Options / Range | Default | Meaning |
|---|---|---|---|---|
| Point Cloud Data Path | String (path) | N/A | empty | Path(s) to input point cloud(s). Multiple paths can be added. |
| Debug | Enum | Off / On, .laz / On, .txt | Off | Controls debug exports and the point‑cloud format for intermediates. |
| Segmentation | Enum | Off / Keep all / Keep trees only / Keep all steps | Off | Controls tree segmentation output. “Keep all steps” requires Debug On. |
| EPSG Code | Integer | Projected CRS (meters) | 32633 | Output CRS for all georeferenced results. |
| Data Type | Enum (base + quality) | Base: MLS/TLS, iPhone LiDAR, CRP, UAV LiDAR, ALS (1000 pts/m²); Quality: Raw/Cropped | MLS/TLS + Raw | Source type and preset tuning (Raw/Cropped affects extra DTM filtering). |
| Maximal DBH | Float | 0.1 – 5.0 | 1.5 | Upper DBH threshold (m) to filter unrealistic trees. |
| Subsampling Step | Float | 0.01 – 0.2 | 0.05 | Min spacing for subsampled cloud. Keep smaller than Cross Section Thickness. |
| Filter‑Chunk Size | Float | 1.0 – 100.0 | 10.0 | Chunk size for density filtering. Smaller detects a larger proportion of understory, larger favours the main canopy. |
| DTM Resolution | Float | 0.5 – 5.0 | 1.0 | Raster grid step for DTM creation. |
| Segmentation Gap | Float | 0.01 – 1.0 | 0.05 | Spatial gap for filtering non‑tree objects. |
| Segmentation Min Height | Float | 0.0 – 5.0 | 1.0 | Minimum above‑ground height used in segmentation. Points below this height are ignored.|
| Cross Section Thickness | Float | 0.01 – 1.0 | 0.07 | Disc thickness for DBH and stem location. |
| Cross Sections Count | Integer | 1 – 5000 | 3 | Number of height levels (1.3 m always included). |
| Cross Section Step | Float | 0.01 – 2.0 | 1.0 | Vertical spacing between additional slices above 1.3 m. |


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
• Nothing known at this time.

# Autorship
**Authors:**  
Marek Hrdina¹\*  

¹ Faculty of Forestry and Wood Sciences, Czech University of Life Sciences Prague,  
Kamýcká 129, 16500 Prague, Czech Republic  

\* **Corresponding author:** [hrdinam@fld.czu.cz](mailto:hrdinam@fld.czu.cz)
