"""Implementation of the h5py-like Bliss Data API with dynamic HDF5 files"""

from collections.abc import Iterator
from typing import Any

from numpy.typing import DTypeLike
import h5py

from . import abstract
from .utils import hdf5
from .utils import bliss
from .utils import types
from .file_arguments import ignore_file_arguments


class Attributes(abstract.Attributes):
    """Node attributes."""

    def __init__(self, retry_handler: hdf5.DynamicHDF5Handler, name: str) -> None:
        self.__retry_handler = retry_handler
        self.__name = name
        super().__init__()

    def __getitem__(self, key: str) -> Any:
        return self.__retry_handler.get_attr(self.__name, key)

    def __iter__(self) -> Iterator[Any]:
        yield from self.__retry_handler.iter_attrs(self.__name)

    def __len__(self) -> int:
        return self.__retry_handler.len_attrs(self.__name)


class Node:
    """Node in the data tree."""

    def __init__(self, retry_handler: hdf5.DynamicHDF5Handler, name: str) -> None:
        self._retry_handler = retry_handler
        if not name:
            name = hdf5.SEP
        self.__name = name
        self.__attrs = None
        self.__parent = None
        self.__file = None
        super().__init__()

    @property
    def name(self) -> str:
        return self.__name

    @property
    def parent(self) -> "Group":
        if self.__parent is None:
            parent_name = self.name.rpartition(hdf5.SEP)[0]
            self.__parent = Group(self._retry_handler, parent_name)
        return self.__parent

    @property
    def file(self) -> "File":
        if self.__file is None:
            self.__file = File(self._retry_handler)
        return self.__file

    @property
    def attrs(self) -> Attributes:
        if self.__attrs is None:
            self.__attrs = Attributes(self._retry_handler, self.name)
        return self.__attrs


class Group(Node, abstract.Group):
    """Node in the data tree which contains other nodes."""

    def __getitem__(self, key: str) -> Node:
        if not key.startswith(hdf5.SEP):
            if self.name == hdf5.SEP:
                key = f"{hdf5.SEP}{key}"
            else:
                key = f"{self.name}{hdf5.SEP}{key}"
        h5py_obj = self._retry_handler.get_item(key)
        if isinstance(h5py_obj, h5py.Group):
            return Group(self._retry_handler, h5py_obj.name)
        else:
            return Dataset(self._retry_handler, h5py_obj.name)

    def __iter__(self) -> Iterator[Node]:
        yield from self._retry_handler.iter_item(self.name)

    def __len__(self) -> int:
        return self._retry_handler.len_item(self.name)


class Dataset(Node, abstract.Dataset):
    """Node in the data tree which contains data."""

    def __getitem__(self, idx: types.DataIndexType) -> types.DataType:
        return self._retry_handler.slice_dataset(self.name, idx)

    def __iter__(self) -> Iterator[types.DataType]:
        yield from self._retry_handler.iter_item(self.name)

    def __len__(self) -> int:
        return self._retry_handler.len_item(self.name)

    @property
    def dtype(self) -> DTypeLike:
        return self._retry_handler.getattr_item(self.name, "dtype")

    @property
    def shape(self) -> tuple[int]:
        return self._retry_handler.getattr_item(self.name, "shape")

    @property
    def size(self) -> int:
        return self._retry_handler.getattr_item(self.name, "size")

    @property
    def ndim(self) -> int:
        return self._retry_handler.getattr_item(self.name, "ndim")


class File(Group, abstract.File):
    """Root node in the data tree."""

    def __init__(
        self,
        file: str,
        hdf5_retry_handler: type[
            hdf5.DynamicHDF5Handler
        ] = bliss.BlissDynamicHDF5Handler,
        **openargs,
    ) -> None:
        if isinstance(file, str):
            openargs = {
                k: v
                for k, v in openargs.items()
                if k not in ignore_file_arguments("dynamic_hdf5")
            }
            file = hdf5_retry_handler(file, **openargs)
        super().__init__(file, hdf5.SEP)

    def close(self) -> None:
        self._retry_handler.close()

    @property
    def parent(self) -> None:
        return None

    @property
    def file(self) -> "File":
        return self

    @property
    def filename(self) -> str:
        return self._retry_handler.filename
