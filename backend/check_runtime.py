"""Fail before serving a platform backed by an incompatible numerical runtime."""
import sys
from catalog import case_catalog
from common import MODEL


def check_spatial_dependencies():
    # Exercise the same import boundary used by the real section-query worker.
    # Importing just "shapely" misses PyInstaller's lazy submodule omissions.
    sys.path.insert(0, str(MODEL / 'code'))
    from integration.runoff_adapter import aggregate_runoff_to_reach_segments
    from shapely.geometry import Point, box
    from shapely.ops import transform
    from shapely.strtree import STRtree
    from pyproj import Transformer
    area = box(119.4, 30.0, 119.6, 30.2)
    projection = Transformer.from_crs('EPSG:4326', 'EPSG:32650', always_xy=True)
    projected = transform(projection.transform, area)
    if projected.area <= 0 or len(STRtree([area]).query(Point(119.5, 30.1))) != 1:
        raise ValueError('Spatial runtime geometry/coordinate validation failed')
    if not callable(aggregate_runoff_to_reach_segments):
        raise ValueError('Section runoff adapter unavailable')
    print('Spatial runtime OK: section adapter, Shapely ops/STRtree and PROJ transform.')


def main():
    case = case_catalog()["cases"]["nananxi-xufan:1km"]
    if not case["ready"]:
        print("Missing runtime/input: " + ", ".join(case["missingInputs"]))
        return 1
    # Import and read a real file: find_spec alone cannot detect DLL conflicts.
    import h5py
    import xarray as xr
    with xr.open_dataset(MODEL / "data_interface/precipitation_qc_20260819/1km_hourly/nananxi_precip_hourly_1km_2017.nc", engine="h5netcdf") as dataset:
        if not dataset["precipitation"].size:
            raise ValueError("Empty precipitation input")
    import worker
    check_spatial_dependencies()
    import numba
    import llvmlite
    print(f"Numerical acceleration available: Numba {numba.__version__}, llvmlite {llvmlite.__version__}; first use compiles the cached kernel.")
    print(f"Runtime OK: Python {sys.version.split()[0]}, HDF5 {h5py.version.hdf5_version}; real model and inputs available.")
    return 0


if __name__ == '__main__':
    sys.exit(main())
