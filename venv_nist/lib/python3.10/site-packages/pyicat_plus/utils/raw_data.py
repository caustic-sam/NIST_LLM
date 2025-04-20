import os
from typing import Optional, Dict, Any, Tuple, List

import h5py
import json

from . import path_utils

_DATA_FORMATS = ("esrfv1", "esrfv2", "esrfv3", "id16bspec", "mx")

_BEAMLINE_DIR_TO_NAME = {
    "id30a1": "id30a-1",
    "id30a3": "id30a-3",
    "id23eh1": "id23-1",
    "id23eh2": "id23-2",
}
_BEAMLINE_NAME_TO_DIR = {
    "id30a-1": "id30a1",
    "id30a-3": "id30a3",
    "id23-1": "id23eh1",
    "id23-2": "id23eh2",
}

MX_METADATA_FILENAME = "metadata.json"


def get_session_dir(
    proposal: str,
    beamline: str,
    session: str,
    root_dir: Optional[str] = None,
    raw_data_format: str = "esrfv3",
) -> str:
    """Get the session directory from the proposal, beamlines and session name."""
    if raw_data_format in _DATA_FORMATS:
        if root_dir is None:
            root_dir = os.path.join(os.sep, "data", "visitor")
        session_dir = path_utils.markdir(
            os.path.join(root_dir, proposal, beamline, session)
        )
        if beamline not in _BEAMLINE_NAME_TO_DIR:
            return session_dir
        # Sometimes the directory name is equal to the beamline name and sometimes not
        if os.path.exists(session_dir):
            return session_dir
        bldirname = _BEAMLINE_NAME_TO_DIR[beamline]
        return path_utils.markdir(os.path.join(root_dir, proposal, bldirname, session))
    _raise_raw_data_format_error(raw_data_format)


def parse_session_dir(
    session_dir: str, raw_data_format: str = "esrfv3"
) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """Get proposal, beamline and session name from the session directory."""
    proposal, beamline_dir, session = path_utils.split(session_dir)[-3:]
    if not session.isdigit():
        return None, None, None
    beamline = _BEAMLINE_DIR_TO_NAME.get(beamline_dir, beamline_dir)
    return proposal, beamline, session


def get_raw_data_dir(session_dir: str, raw_data_format: str = "esrfv3") -> str:
    """Get the raw data directory from proposal, beamline and session name.
    This is the directory when Bliss saves the raw data.
    """
    if raw_data_format in ("esrfv3", "id16bspec", "mx"):
        return path_utils.markdir(os.path.join(session_dir, "RAW_DATA"))
    if raw_data_format == "esrfv2":
        return path_utils.markdir(os.path.join(session_dir, "raw"))
    if raw_data_format == "esrfv1":
        return path_utils.markdir(session_dir)
    _raise_raw_data_format_error(raw_data_format)


def get_dataset_filters(
    raw_root_dir: str, raw_data_format: str = "esrfv3"
) -> List[str]:
    """Get the dataset directory search filters from the raw data directory."""
    if raw_data_format in ("esrfv1", "esrfv2", "esrfv3", "id16bspec"):
        return [path_utils.markdir(os.path.join(raw_root_dir, "*", "*"))]
    elif raw_data_format == "mx":
        filters = []
        for root, dirs, files in os.walk(raw_root_dir):
            if MX_METADATA_FILENAME in files:
                filters.append(path_utils.markdir(root))
        return filters
    _raise_raw_data_format_error(raw_data_format)


def get_raw_dataset_name(
    dataset_dir: str, raw_data_format: str = "esrfv3"
) -> Optional[str]:
    """Get the raw data dataset name from the dataset directory."""
    if raw_data_format in ("esrfv1", "esrfv2", "esrfv3"):
        collection, collection_dataset = path_utils.split(dataset_dir)[-2:]
        if not collection_dataset.startswith(collection):
            return None
        return collection_dataset[len(collection) + 1 :]
    if raw_data_format in ("id16bspec", "mx"):
        return path_utils.split(dataset_dir)[-1]
    _raise_raw_data_format_error(raw_data_format)


def get_raw_dataset_metadata(
    dataset_dir: str, raw_data_format: str = "esrfv3"
) -> Dict[str, str]:
    """Get dataset info from the raw dataset directory."""
    if raw_data_format in ("esrfv1", "esrfv2", "esrfv3"):
        return _raw_dataset_metadata_esrf(dataset_dir)
    if raw_data_format == "id16bspec":
        return _raw_dataset_metadata_id16bspec(dataset_dir)
    if raw_data_format == "mx":
        return _raw_dataset_metadata_mx(dataset_dir)
    _raise_raw_data_format_error(raw_data_format)


def _raise_raw_data_format_error(raw_data_format: str) -> None:
    if raw_data_format in _DATA_FORMATS:
        raise RuntimeError(f"Implementation error for '{raw_data_format}'")
    else:
        raise NotImplementedError(
            f"Raw data format '{raw_data_format}' is not supported"
        )


def _raw_dataset_metadata_esrf(dataset_dir: str) -> Dict[str, str]:
    basename = path_utils.basename(dataset_dir)
    dataset_file = os.path.join(dataset_dir, f"{basename}.h5")
    if not os.path.exists(dataset_file):
        raise FileNotFoundError("HDF5 file does not exist")

    dataset_metadata = dict()
    enddate = None
    try:
        with h5py.File(dataset_file, "r", locking=False) as f:
            if not _is_bliss_raw_dataset_file(f):
                raise ValueError("HDF5 file not created by Bliss")
            startdate = f.attrs.get("file_time")
            for scan in map(str, sorted(map(float, list(f)))):
                sample_name = _read_hdf5_dataset(
                    f, f"/{scan}/sample/name", default=None
                )
                if sample_name is not None:
                    dataset_metadata["Sample_name"] = sample_name
                enddate = _read_hdf5_dataset(f, f"/{scan}/end_time", default=enddate)
    except Exception as e:
        raise RuntimeError(f"HDF5 reading error ({e})") from e

    if startdate is not None:
        dataset_metadata["startDate"] = startdate
    if enddate is not None:
        dataset_metadata["endDate"] = enddate

    return dataset_metadata


def _raw_dataset_metadata_id16bspec(dataset_dir: str) -> Dict[str, str]:
    dataset_metadata = dict()

    proposal, _, _, _, sample_name, dataset = path_utils.split(dataset_dir)[-6:]
    filename = f"{proposal}-{sample_name}-{dataset}.h5"
    dataset_file = os.path.join(dataset_dir, filename)

    if not os.path.exists(dataset_file):
        raise FileNotFoundError("HDF5 file does not exist")

    startdate = None
    enddate = None
    try:
        with h5py.File(dataset_file, "r", locking=False) as f:
            for name in f:
                entry = f[name]
                try:
                    startdate = _read_hdf5_dataset(entry, "start_time", default=None)
                    enddate = _read_hdf5_dataset(entry, "end_time", default=None)
                except KeyError as e:
                    raise ValueError(f"Time could not be read from HDF5 ({e})") from e
                break
    except Exception as e:
        raise RuntimeError(f"HDF5 reading error ({e})") from e

    if startdate is not None:
        dataset_metadata["startDate"] = startdate
    if enddate is not None:
        dataset_metadata["endDate"] = enddate
    dataset_metadata["Sample_name"] = sample_name
    return dataset_metadata


def _raw_dataset_metadata_mx(dataset_dir: str) -> Dict[str, str]:
    """Read metadata from MX_METADATA_FILENAME for 'mx' format."""
    metadata_file = os.path.join(dataset_dir, MX_METADATA_FILENAME)

    if not os.path.isfile(metadata_file):
        raise FileNotFoundError(f"{MX_METADATA_FILENAME} not found in {dataset_dir}")

    with open(metadata_file, "r") as f:
        metadata = json.load(f)

    return {key: str(value) for key, value in metadata.items() if value is not None}


def _is_bliss_raw_dataset_file(f: h5py.File) -> bool:
    return f.attrs.get("creator", "").lower() in ("bliss", "blissdata", "blisswriter")


def _read_hdf5_dataset(parent: h5py.Group, name: str, default=None) -> Any:
    try:
        value = parent[name][()]
    except KeyError:
        return default
    try:
        return value.decode()
    except AttributeError:
        pass
    return value
