import os
ROOT=os.path.abspath(os.path.join(SPECPATH,'..'))
hiddenimports=['numpy','scipy','pandas','xarray','h5py','numba','llvmlite','netCDF4','pyproj','shapely','PIL','matplotlib']
a=Analysis([os.path.join(ROOT,'app_entry.py')],pathex=[os.path.join(ROOT,'backend')],binaries=[],datas=[],hiddenimports=hiddenimports,hookspath=[],hooksconfig={},runtime_hooks=[],excludes=['tensorflow','torch','IPython','pytest'],noarchive=False)
pyz=PYZ(a.pure)
exe=EXE(pyz,a.scripts,[],exclude_binaries=True,name='南安溪徐畈平台',debug=False,bootloader_ignore_signals=False,strip=False,upx=False,console=True,disable_windowed_traceback=False,argv_emulation=False,target_arch='x86_64',codesign_identity=None,entitlements_file=None)
coll=COLLECT(exe,a.binaries,a.datas,strip=False,upx=False,name='南安溪徐畈平台')
