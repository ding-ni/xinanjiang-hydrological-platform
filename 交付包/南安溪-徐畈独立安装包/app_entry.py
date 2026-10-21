from __future__ import annotations
import os, runpy, sys
from pathlib import Path

_DLL_DIRECTORY_HANDLES = []
_PRELOADED_HDF5 = []

def preload_bundled_hdf5(app_root):
    """Pin h5py's matching HDF5 before optional xarray plugins load other copies."""
    if os.name != 'nt' or not getattr(sys, 'frozen', False):
        return
    internal = Path(getattr(sys, '_MEIPASS', app_root))
    h5py_dir = internal / 'h5py'
    hdf5_path = h5py_dir / 'hdf5.dll'
    hdf5_hl_path = h5py_dir / 'hdf5_hl.dll'
    if not hdf5_path.is_file() or not hdf5_hl_path.is_file():
        raise RuntimeError(f'Bundled h5py HDF5 libraries are missing under {h5py_dir}')

    import ctypes
    if hasattr(os, 'add_dll_directory'):
        _DLL_DIRECTORY_HANDLES.append(os.add_dll_directory(str(h5py_dir)))
    # Use the exact package-local pair; ecmwflibs and rasterio ship other DLLs
    # with the same generic names but different HDF5 ABI versions.
    _PRELOADED_HDF5.append(ctypes.WinDLL(str(hdf5_path), winmode=0x1100))
    _PRELOADED_HDF5.append(ctypes.WinDLL(str(hdf5_hl_path), winmode=0x1100))

    import h5py
    expected = tuple(int(part) for part in h5py.version.hdf5_version.split('.'))
    major = ctypes.c_uint()
    minor = ctypes.c_uint()
    release = ctypes.c_uint()
    get_version = _PRELOADED_HDF5[0].H5get_libversion
    get_version.argtypes = [ctypes.POINTER(ctypes.c_uint)] * 3
    if get_version(ctypes.byref(major), ctypes.byref(minor), ctypes.byref(release)) != 0:
        raise RuntimeError('Could not verify the bundled HDF5 runtime version')
    loaded = (major.value, minor.value, release.value)
    if loaded != expected:
        raise RuntimeError(f'Bundled HDF5 ABI mismatch: h5py expects {expected}, loaded {loaded}')
    extension_loaded = tuple(h5py.h5.get_libversion())
    if extension_loaded != expected:
        raise RuntimeError(
            f'h5py extension HDF5 ABI mismatch: expected {expected}, loaded {extension_loaded}'
        )

def main():
    app_root = Path(sys.executable).resolve().parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent
    os.environ.setdefault('XAJ_APP_ROOT', str(app_root))
    os.environ.setdefault('XAJ_BUNDLED', '1')
    os.environ.setdefault('MPLBACKEND', 'Agg')
    os.environ.setdefault('XAJ_RUNTIME_DIR', str(Path(os.environ.get('LOCALAPPDATA', app_root))/'Nananxi-Xufan-Platform'/'workspace'))
    os.environ.setdefault('XAJ_CONFIG', str(app_root/'config/platform.json'))
    preload_bundled_hdf5(app_root)
    backend=app_root/'backend'; sys.path.insert(0,str(backend))
    args=sys.argv[1:]
    if args and args[0]=='--xaj-script':
        if len(args)<2: raise SystemExit('--xaj-script requires a script path')
        script=Path(args[1]).resolve()
        if not script.is_file(): raise SystemExit(f'Bundled worker script not found: {script}')
        sys.argv=[str(script),*args[2:]]; runpy.run_path(str(script),run_name='__main__')
    elif args==['--warm']:
        from numeric_runtime import warm; warm()
    else:
        from server import main as serve
        sys.argv=[str(backend/'server.py'),*args]; serve()

if __name__=='__main__': main()
