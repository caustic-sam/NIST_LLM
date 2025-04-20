import os
import re
from glob import glob
from collections import abc
from numbers import Integral
from typing import Any

import numpy
from numpy.typing import DTypeLike
from silx.utils.retry import RetryError
from silx.io import h5py_utils

from . import types

SEP = "/"


class LimaGroup(abc.Mapping):
    def __init__(self, name, *args, **kwargs) -> None:
        self._name = name
        self._dset_args = args
        self._dset_kwargs = kwargs
        self._dataset = None
        super().__init__()

    def close(self):
        if self._dataset is None:
            return
        self._dataset.close()
        self._dataset = None

    def reset(self):
        if self._dataset is None:
            return
        self._dataset.reset()

    @property
    def name(self) -> str:
        return self._name

    @property
    def attrs(self) -> dict:
        return {"type": "lima"}

    def __getitem__(self, key: str):
        if key == "data":
            if self._dataset is None:
                self._dataset = LimaDataset(
                    self.name + SEP + "data", *self._dset_args, **self._dset_kwargs
                )
            return self._dataset
        raise KeyError

    def __iter__(self):
        yield "data"

    def __len__(self) -> int:
        return 1


class LimaDataset(abc.Sequence):
    def __init__(
        self,
        name: str,
        dirname: str,
        url_template: str | None = None,
        url_template_args: dict[str, Any] | None = None,
    ) -> None:
        parts = [s for s in name.split(SEP) if s]
        scan_number = int(parts[0].split(".")[0])
        bliss_detector_name = parts[-2]

        url_template = lima_url_template(
            dirname,
            scan_number,
            bliss_detector_name,
            url_template=url_template,
            url_template_args=url_template_args,
        )
        filename_template, self._path_in_file = url_template.split("::")
        self._search_pattern = filename_template.format(file_index="*")
        self._match_pattern = re.compile(
            os.path.basename(filename_template.format(file_index="([0-9]+)"))
        )

        self._files = list()
        self._dtype = None
        self._shape = None
        self._size = None
        self._ndim = None
        self.__points_per_file = None

        self._active_filename = None
        self._active_file = None
        self._active_dset = None
        self._name = name
        self.is_complete = False

    @property
    def name(self) -> str:
        return self._name

    @property
    def attrs(self) -> dict:
        return dict()

    def reset(self):
        """Close the activate HDF5 file, cleanup all HDF5 objects and search for lima files"""
        self._cleanup()
        self.search()

    def close(self):
        """Close the activate HDF5 file and cleanup all HDF5 objects"""
        self._cleanup()

    def _cleanup(self):
        if self._active_file is None:
            return
        self._active_file.close()
        self._active_filename = None
        self._active_file = None
        self._active_dset = None

    def search(self):
        # NFS: globbing might return nothing.
        lima_files = glob(self._search_pattern)
        lima_nrs = [
            int(self._match_pattern.search(os.path.basename(s)).group(1))
            for s in lima_files
        ]
        self._files = [filename for _, filename in sorted(zip(lima_nrs, lima_files))]
        self.__points_per_file = None
        self._dtype = None
        self._shape = None
        self._size = None
        self._ndim = None

    def _open(self, file_index: int):
        try:
            filename = self._files[file_index]
        except IndexError:
            raise RetryError(f"lima file {file_index} does not exist (yet)")
        if self._active_filename == filename:
            return
        self.close()

        # Lima has the file open until all data points are written.
        # Lima does not flush which means the file is never readable while it is open.
        # To be sure about the later, try locking the file when reading.
        # If it succeeds it means lima is done with the file.
        self._active_file = h5py_utils.File(
            filename, mode="r"
        )  # , locking=h5py_utils.HAS_LOCKING_ARGUMENT)
        self._active_dset = self._active_file[self._path_in_file]
        self._active_filename = filename

    def __getitem__(self, idx: types.DataIndexType) -> types.DataType:
        if not self._files:
            self._cache_dataset_info()
        if isinstance(idx, tuple):
            first_dim_indx = idx[0]
            single_point_idx = idx[1:]
        else:
            first_dim_indx = idx
            single_point_idx = tuple()

        dim0scalar = False
        if isinstance(first_dim_indx, Integral):
            first_dim_indx = numpy.asarray([first_dim_indx])
            dim0scalar = True
        elif isinstance(first_dim_indx, slice):
            first_dim_indx = numpy.array(range(*first_dim_indx.indices(self.shape[0])))
        elif isinstance(first_dim_indx, abc.Sequence):
            first_dim_indx = numpy.asarray(first_dim_indx)
        elif first_dim_indx is Ellipsis:
            first_dim_indx = numpy.array(range(self.shape[0]))
        else:
            raise TypeError

        result = list()

        for scan_index in first_dim_indx:
            file_index = scan_index // self._points_per_file
            self._open(file_index)
            first_dim_file_indx = scan_index % self._points_per_file
            idx = (first_dim_file_indx,) + single_point_idx
            try:
                result.append(self._active_dset[idx])
            except IndexError:
                raise RetryError(f"Failed slice lima dataset {self._active_dset}")

        if dim0scalar:
            return result[0]
        else:
            return numpy.array(result)

    def __iter__(self) -> abc.Iterator[types.DataType]:
        if not self._files:
            self._cache_dataset_info()
        for file_index in range(len(self._files)):
            self._open(file_index)
            yield from iter(self._active_dset)

    def _cache_dataset_info(self):
        if not self._files:
            self.reset()
        if not self._files:
            raise RetryError("no lima files exist (yet)")

        files = self._files
        first_filename = files[0]
        with h5py_utils.File(first_filename, mode="r") as f:
            dset = f[self._path_in_file]
            first_shape = dset.shape
            dtype = dset.dtype

        nfiles = len(files)
        if nfiles == 1:
            shape = first_shape
        else:
            last_filename = files[-1]
            try:
                # When the last file is not flushed by Lima
                # this raises an h5py error which causes a retry.
                with h5py_utils.File(last_filename, mode="r") as f:
                    dset = f[self._path_in_file]
                    last_shape = dset.shape
            except Exception:
                if self.is_complete:
                    raise
                last_shape = first_shape
                files = files[:-1]
            ndatapoints = first_shape[0] * (nfiles - 1) + last_shape[0]
            shape = (ndatapoints,) + first_shape[1:]

        self._files = files
        self._shape = shape
        self._dtype = dtype
        self._size = numpy.prod(shape, dtype=int)
        self._ndim = len(shape)
        self.__points_per_file = first_shape[0]

    def __len__(self) -> int:
        return self.shape[0]

    @property
    def _points_per_file(self) -> int:
        if self.__points_per_file is None:
            self._cache_dataset_info()
        return self.__points_per_file

    @property
    def dtype(self) -> DTypeLike:
        if self._dtype is None:
            self._cache_dataset_info()
        return self._dtype

    @property
    def shape(self) -> tuple[int]:
        if self._shape is None:
            self._cache_dataset_info()
        return self._shape

    @property
    def size(self) -> int:
        if self._size is None:
            self._cache_dataset_info()
        return self._size

    @property
    def ndim(self) -> int:
        if self._ndim is None:
            self._cache_dataset_info()
        return self._ndim


def _generate_default_url_template(
    user_instrument_name: str | None, user_detector_name: str
):
    filepath_template = os.path.join(
        "{dirname}",
        "scan{scan_number:04d}",
        "{bliss_detector_name}_{{file_index}}.h5",
    )
    if user_instrument_name is None:
        return filepath_template + SEP.join(("::", "entry_0000", "measurement", "data"))

    return filepath_template + SEP.join(
        ("::", "entry_0000", user_instrument_name, user_detector_name, "data")
    )


def lima_url_template(
    dirname: str,
    scan_number: int,
    bliss_detector_name: str,
    url_template: str | None = None,
    url_template_args: dict[str, Any] | None = None,
) -> str:
    if url_template_args is None:
        url_template_args = {}

    if not url_template:
        user_instrument_name = url_template_args.get("instrument_name")
        user_detector_name = url_template_args.get("detector_name", bliss_detector_name)
        url_template = _generate_default_url_template(
            user_instrument_name, user_detector_name
        )

    common_args = {
        "dirname": dirname,
        "scan_number": scan_number,
        "bliss_detector_name": bliss_detector_name,
    }
    url_template_args = {
        k: v.format(**common_args) for k, v in url_template_args.items()
    }
    url_template = url_template.format(**common_args, **url_template_args)

    if "{file_index}" not in url_template:
        raise ValueError("A lima template URL needs '{{file_index}}'")

    return url_template
