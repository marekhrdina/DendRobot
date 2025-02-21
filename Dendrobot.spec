import os
from glob import glob
from PyInstaller.utils.hooks import collect_submodules

block_cipher = None

# Set wheels directory
wheels_dir = "C:/Users/hrdinam/OneDrive - CZU v Praze/Programovani/ENV-HT/wheels"

# Add all `.whl` files to datas so they are included in the bundle
whl_files = glob(os.path.join(wheels_dir, "*.whl"))
datas = [(whl, os.path.basename(whl)) for whl in whl_files]

a = Analysis(
    ['DendRobot.py'],
    pathex=[wheels_dir],
    binaries=[('C:/Users/hrdinam/OneDrive - CZU v Praze/Programovani/ENV-HT/ostatni/rasterio/rasterio.libs/gdal-f326048d09ce3aebce08c2a62b7337d6.dll', 'gdaldll')],
    datas=datas +       [('C:/Users/hrdinam/OneDrive - CZU v Praze/Programovani/ENV-HT/ostatni/rasterio/rasterio', 'rasterio'),
        ('C:/Users/hrdinam/OneDrive - CZU v Praze/Programovani/ENV-HT/ostatni/rasterio/rasterio.libs', 'rasterio.libs'),
        ('C:/Users/hrdinam/OneDrive - CZU v Praze/Programovani/ENV-HT/ostatni/rasterio/rasterio-1.4.1.dist-info', 'rasterio-1.4.1.dist-info'),]
        ,
    hiddenimports=[
        'fiona', 'pyproj', 'rasterio', 'rasterio.sample', 'rasterio.features', 'rasterio.mask', 'rasterio.plot', 'rasterio.vrt' 'open3d', 'pyogrio', 'numpy', 'numpy.core', 'numpy.core.multiarray',
        'scipy', 'scipy.spatial', 'scipy.sparse', 'scipy.linalg', 'scipy.optimize',
        'multiprocessing', 'alphashape', 'cc3d'
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='DendRobot',
    debug=True,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='C:/Users/hrdinam/OneDrive - CZU v Praze/Programovani/ENV-HT/ostatni/dendrobot.ico',
    onefile=True
)
