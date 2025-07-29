# -*- mode: python ; coding: utf-8 -*-

import os, numpy
from PyInstaller.utils.hooks import collect_dynamic_libs

a = Analysis(
    ['DendRobot.py'],
    pathex=['D:\\MarekHrdina\\OneDrive - CZU v Praze\\Programovani\\DendRobot\\.venv\\Lib\\site-packages'],
    binaries=[
        # Copy the gdal DLL to the 'gdaldll' folder in the bundled app
        ('D:/MarekHrdina/OneDrive - CZU v Praze/Programovani/DendRobot/ostatni/rasterio/rasterio.libs/gdal-f326048d09ce3aebce08c2a62b7337d6.dll', 'gdaldll'),],
    datas=[
        ('D:/MarekHrdina/OneDrive - CZU v Praze/Programovani/DendRobot/ostatni/fiona/fiona', 'fiona'),
        ('D:/MarekHrdina/OneDrive - CZU v Praze/Programovani/DendRobot/ostatni/fiona/fiona.libs', 'fiona.libs'),
        ('D:/MarekHrdina/OneDrive - CZU v Praze/Programovani/DendRobot/ostatni/fiona/fiona-1.10.1.dist-info', 'fiona-1.10.1.dist-info'),
        ('D:/MarekHrdina/OneDrive - CZU v Praze/Programovani/DendRobot/ostatni/open3d/open3d', 'open3d'),
        ('D:/MarekHrdina/OneDrive - CZU v Praze/Programovani/DendRobot/ostatni/open3d/open3d-0.18.0.data', 'open3d-0.18.0.data'),
        ('D:/MarekHrdina/OneDrive - CZU v Praze/Programovani/DendRobot/ostatni/open3d/open3d-0.18.0.dist-info', 'open3d-0.18.0.dist-info'),
        ('D:/MarekHrdina/OneDrive - CZU v Praze/Programovani/DendRobot/ostatni/pyogrio/pyogrio', 'pyogrio'),
        ('D:/MarekHrdina/OneDrive - CZU v Praze/Programovani/DendRobot/ostatni/pyogrio/pyogrio.libs', 'pyogrio.libs'),
        ('D:/MarekHrdina/OneDrive - CZU v Praze/Programovani/DendRobot/ostatni/pyogrio/pyogrio-0.10.0.dist-info', 'pyogrio-0.10.0.dist-info'),
        ('D:/MarekHrdina/OneDrive - CZU v Praze/Programovani/DendRobot/ostatni/pyproj/pyproj', 'pyproj'),
        ('D:/MarekHrdina/OneDrive - CZU v Praze/Programovani/DendRobot/ostatni/pyproj/pyproj.libs', 'pyproj.libs'),
        ('D:/MarekHrdina/OneDrive - CZU v Praze/Programovani/DendRobot/ostatni/pyproj/pyproj-3.7.0.dist-info', 'pyproj-3.7.0.dist-info'),
        ('D:/MarekHrdina/OneDrive - CZU v Praze/Programovani/DendRobot/ostatni/rasterio/rasterio', 'rasterio'),
        ('D:/MarekHrdina/OneDrive - CZU v Praze/Programovani/DendRobot/ostatni/rasterio/rasterio.libs', 'rasterio.libs'),
        ('D:/MarekHrdina/OneDrive - CZU v Praze/Programovani/DendRobot/ostatni/rasterio/rasterio-1.4.1.dist-info', 'rasterio-1.4.1.dist-info'),
        ('D:/MarekHrdina/OneDrive - CZU v Praze/Programovani/DendRobot/ostatni/connected_components_3d', 'connected_components_3d'),
    ],

    hiddenimports=[
        'numpy', 'numpy.core', 'numpy.core.multiarray','numpy.core._methods',
        'numpy.lib.format', 'multiprocessing',
        'cc3d', 'connected_components_3d', 'fiona', 'pyproj', 'rasterio',
        'open3d', 'pyogrio', 'ml3d', 'numpy._core', 'crackle-codec',
    ],
    
    hookspath=[],
    hooksconfig={},
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
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="D:/MarekHrdina/OneDrive - CZU v Praze/Programovani/DendRobot/ostatni/dendrobot.ico",
)


