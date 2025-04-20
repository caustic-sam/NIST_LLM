"""The `File` class in each context has specific arguments other than the `h5py.File`
arguments which are specific for that context (static HDF5, dynamic HDF5, ...).
"""

_CONTEXT_SPECIFIC_KEYS = {
    "h5py": {"mode"},
    "static_hdf5": set(),
    "dynamic_hdf5": {
        "hdf5_retry_handler",
        "retry_timeout",
        "retry_period",
        "lima_names",
        "instrument_name",
        "lima_url_template",
        "lima_url_template_args",
        "prioritize_non_native_h5items",
    },
}


def ignore_file_arguments(context: str) -> set[str]:
    """Returns all keys with that should be ignore when instantiating the `File`
    in a specific context"""
    ignore = set()
    for key, value in _CONTEXT_SPECIFIC_KEYS.items():
        if key == context:
            continue
        ignore |= value
    return ignore
