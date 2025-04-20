from pathlib import Path
from dataclasses import dataclass

import h5py
import numpy
import pytest
from silx.utils.retry import RetryError

from blissdata.h5api.utils import lima


@dataclass
class _LimaFileInfo:
    tmp_path: Path
    scan_number_as_str: str = "00008"
    images_prefix: str = "sample_dataset_data_"
    nfiles: int = 3
    nimages_per_file: int = 4
    image_shape: tuple[int, int] = (10, 11)
    nimages_empty: int = 1
    h5_path: str = "/entry_0000/measurement/data"

    @property
    def url_template(self) -> str:
        return (
            "{dirname}/scan{scan_number_as_str}/{images_prefix}{{file_index}}.h5::"
            + self.h5_path
        )

    @property
    def nimages(self) -> int:
        return self.nfiles * self.nimages_per_file

    @property
    def total_shape(self) -> tuple[int, int, int]:
        return (self.nimages, *self.image_shape)

    @property
    def nimages_saved(self) -> int:
        assert self.nimages_empty < self.nimages_per_file
        return self.nimages - self.nimages_empty

    @property
    def dataset_args(self) -> dict:
        return {
            "dirname": str(self.tmp_path),
            "name": "/8.1/instrument/eiger/data",
            "url_template": self.url_template,
            "url_template_args": {
                "images_prefix": self.images_prefix,
                "scan_number_as_str": self.scan_number_as_str,
            },
        }

    def generate_data(self) -> None:
        lima_dirname = self.tmp_path / f"scan{self.scan_number_as_str}"
        lima_dirname.mkdir()
        for file_index in range(self.nfiles):
            lima_filename = str(
                lima_dirname / f"{self.images_prefix}{file_index:04d}.h5"
            )
            with h5py.File(lima_filename, mode="w") as fh:
                dset = fh.create_dataset(
                    self.h5_path,
                    shape=(self.nimages_per_file, *self.image_shape),
                    dtype=int,
                    fillvalue=-1,
                )
                for i in range(self.nimages_per_file):
                    image_index = file_index * self.nimages_per_file + i
                    if image_index < self.nimages_saved:
                        dset[i] = image_index

    def lima_dataset(self):
        return lima.LimaDataset(**self.dataset_args)


@pytest.fixture()
def lima_file_info(tmp_path: Path) -> _LimaFileInfo:
    return _LimaFileInfo(tmp_path=tmp_path)


def test_lima_dataset_iteration(lima_file_info: _LimaFileInfo):
    lima_dataset = lima_file_info.lima_dataset()

    with pytest.raises(RetryError, match=r"no lima files exist \(yet\)"):
        for _ in lima_dataset:
            assert False, "Iteration should raise a RetryError"

    lima_file_info.generate_data()

    first_pixels = [img[0, 0] for img in lima_dataset]

    expected = list(range(lima_file_info.nimages_saved))
    expected += [-1] * lima_file_info.nimages_empty
    assert first_pixels == expected


def test_lima_dataset_slice(lima_file_info: _LimaFileInfo):
    lima_dataset = lima_file_info.lima_dataset()

    with pytest.raises(RetryError, match=r"no lima files exist \(yet\)"):
        _ = lima_dataset[1, 0, 0]

    lima_file_info.generate_data()

    data = lima_dataset[1, 0, 0]
    assert data == 1

    data = lima_dataset[:]
    assert data.shape == lima_file_info.total_shape


def test_lima_dataset_shape(lima_file_info: _LimaFileInfo):
    lima_dataset = lima_file_info.lima_dataset()

    with pytest.raises(RetryError, match=r"no lima files exist \(yet\)"):
        _ = lima_dataset.shape

    lima_file_info.generate_data()

    assert lima_dataset.shape == lima_file_info.total_shape


def test_lima_dataset_ndim(lima_file_info: _LimaFileInfo):
    lima_dataset = lima_file_info.lima_dataset()

    with pytest.raises(RetryError, match=r"no lima files exist \(yet\)"):
        _ = lima_dataset.ndim

    lima_file_info.generate_data()

    assert lima_dataset.ndim == len(lima_file_info.total_shape)


def test_lima_dataset_size(lima_file_info: _LimaFileInfo):
    lima_dataset = lima_file_info.lima_dataset()

    with pytest.raises(RetryError, match=r"no lima files exist \(yet\)"):
        _ = lima_dataset.size

    lima_file_info.generate_data()

    assert lima_dataset.size == numpy.prod(lima_file_info.total_shape)


def test_lima_dataset_len(lima_file_info: _LimaFileInfo):
    lima_dataset = lima_file_info.lima_dataset()

    with pytest.raises(RetryError, match=r"no lima files exist \(yet\)"):
        _ = len(lima_dataset)

    lima_file_info.generate_data()

    assert len(lima_dataset) == lima_file_info.total_shape[0]


def test_lima_dataset_dtype(lima_file_info: _LimaFileInfo):
    lima_dataset = lima_file_info.lima_dataset()

    with pytest.raises(RetryError, match=r"no lima files exist \(yet\)"):
        _ = lima_dataset.dtype

    lima_file_info.generate_data()

    assert numpy.issubdtype(lima_dataset.dtype, int)
