import h5py
import os
import pytest
from ..utils import raw_data, path_utils


def test_get_dataset_filters_should_raise_exception_if_invalid_format(tmp_path):
    with pytest.raises(
        NotImplementedError, match="Raw data format 'unknown' is not supported"
    ):
        raw_data.get_dataset_filters(
            raw_root_dir=str(tmp_path), raw_data_format="unknown"
        )


@pytest.mark.parametrize("format", ["esrfv1", "esrfv2", "esrfv3", "id16bspec"])
def test_get_dataset_filters_should_return_expected_path_for_esrf_id16bspec_format(
    tmp_path,
    format,
):
    result = raw_data.get_dataset_filters(
        raw_root_dir=str(tmp_path), raw_data_format=format
    )
    expected_path = os.path.join(str(tmp_path), "*", "*")
    assert result == [path_utils.markdir(expected_path)]


def test_get_dataset_filters_should_return_expected_path_for_mx_format(tmp_path):
    create_folder(tmp_path / "collection")
    create_folder(tmp_path / "workflow", False, False)
    create_folder(tmp_path / "workflow" / "line")
    create_folder(tmp_path / "workflow" / "mesh", False, True)

    result = raw_data.get_dataset_filters(
        raw_root_dir=str(tmp_path), raw_data_format="mx"
    )
    expected_paths = {
        os.path.join(str(tmp_path), "collection", ""),
        os.path.join(str(tmp_path), "workflow", "line", ""),
    }

    assert set(result) == expected_paths


def create_folder(folder_path, with_metadata=True, with_master=True):
    folder_path.mkdir(parents=True, exist_ok=True)
    if with_metadata:
        metadata_path = folder_path / "metadata.json"
        with open(metadata_path, "w"):
            pass
    if with_master:
        master_h5_path = folder_path / "master.h5"
        with h5py.File(master_h5_path, "w"):
            pass
