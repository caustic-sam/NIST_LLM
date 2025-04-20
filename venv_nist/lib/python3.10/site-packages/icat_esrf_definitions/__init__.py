import sys

if sys.version_info < (3, 9):
    import importlib_resources
else:
    import importlib.resources as importlib_resources

DEFINITIONS_FILE = str(
    importlib_resources.files("icat_esrf_definitions").joinpath("hdf5_cfg.xml")
)
