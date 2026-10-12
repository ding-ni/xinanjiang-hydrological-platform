"""Fail before serving a platform backed by an incompatible numerical runtime."""
import sys
from catalog import case_catalog
from common import MODEL


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
    print(f"Runtime OK: Python {sys.version.split()[0]}, HDF5 {h5py.version.hdf5_version}; real model and inputs available.")
    return 0


if __name__ == '__main__':
    sys.exit(main())
