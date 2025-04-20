"""Implementation of the h5py-like Bliss Data API with dynamic HDF5 files"""

import os
from numbers import Number
from collections.abc import Iterator
from typing import Any

import h5py
from silx.io import h5py_utils

from . import types
from . import lima

from .hdf5_retry import RetryError
from .hdf5_retry import RetryTimeoutError
from .hdf5_retry import retry_with_reset
from .hdf5_retry import stop_iter_on_retry_timeout
from .hdf5_retry import return_on_retry_timeout
from .hdf5_retry import RetryWithoutResetError

# Native HDF5 item types
NativeHDF5Item = h5py.Dataset | h5py.Group | h5py.File
NativeHDF5Group = h5py.Group | h5py.File
NativeHDF5Dataset = h5py.Dataset

# Non-native HDF5 item types
NonNativeHDF5Item = lima.LimaDataset | lima.LimaGroup
NonNativeHDF5Group = lima.LimaGroup
NonNativeHDF5Dataset = lima.LimaDataset

# All HDF5 item types
HDF5Item = NativeHDF5Item | NonNativeHDF5Item
HDF5Group = NativeHDF5Group | NonNativeHDF5Group
HDF5Dataset = NativeHDF5Dataset | NonNativeHDF5Dataset


SEP = "/"


class DynamicHDF5Handler:
    """Object to access an HDF5 file which is re-opened upon re-trying failed IO operations"""

    def __init__(
        self,
        file: str,
        retry_timeout: Number | None = None,
        retry_period: Number | None = None,
        **openargs,
    ):
        r"""
        :param file: HDF5 file name for `h5py_utils.File`.
        :param retry_timeout: Timeout for failed HDF5 read operations.
        :param retry_period: Re-try failed HDF5 read operations every `x` seconds.
        :param \**openargs: Optional arguments for `h5py_utils.File`.
        """
        if self._initialized:
            return
        self._file = file
        self._openargs = openargs
        self._file_obj = None
        self._closed = False
        self._retry_period = retry_period
        self._retry_options = {
            "retry_timeout": retry_timeout,
            "retry_period": retry_period,
        }
        self._native_items = dict()
        self._initialized = True

    _INSTANCES = {}

    def __new__(cls, file, *_, **kwargs):
        """Only one handler globally for each HDF5 file"""
        key = os.path.realpath(file)
        instance, ref_count = DynamicHDF5Handler._INSTANCES.get(key, (None, 0))
        if instance is None:
            instance = super().__new__(cls)
            instance._initialized = False
        DynamicHDF5Handler._INSTANCES[key] = instance, ref_count + 1
        return instance

    @classmethod
    def _close_instance(cls, file: str) -> None:
        """Close the HDF5 file, cleanup all HDF5 objects, do not allow re-opening and cleanup globally"""
        key = os.path.realpath(file)
        instance, ref_count = DynamicHDF5Handler._INSTANCES.get(key, (None, 0))
        if instance is None:
            return
        ref_count -= 1
        if ref_count == 0:
            instance._closed = True
            try:
                instance._cleanup()
            finally:
                del DynamicHDF5Handler._INSTANCES[key]
        else:
            DynamicHDF5Handler._INSTANCES[key] = instance, ref_count

    def close(self) -> None:
        """Close the HDF5 file, cleanup all HDF5 objects and do not allow re-opening"""
        self._close_instance(self._file)

    def reset(self) -> None:
        """Close the HDF5 file, cleanup all HDF5 objects but allow re-opening"""
        self._cleanup()

    def _cleanup(self) -> None:
        """Close the HDF5 file, cleanup all HDF5 objects"""
        if self._file_obj is None:
            return
        self._file_obj.close()
        self._file_obj = None
        self._native_items = dict()

    @property
    def file_obj(self) -> h5py.File:
        if self._file_obj is None:
            if self._closed:
                raise RuntimeError("File was closed")
            try:
                self._file_obj = h5py_utils.File(self._file, **self._openargs)
            except FileNotFoundError:
                raise RetryError(f"File {self._file} does not exist (yet)")
        return self._file_obj

    @property
    def filename(self) -> h5py.File:
        return self.file_obj.filename

    def get_item(self, name: str) -> HDF5Item:
        try:
            return self._retry_get_item(name)
        except RetryTimeoutError as e:
            raise KeyError(name) from e

    def slice_dataset(self, name: str, idx: types.DataIndexType) -> types.DataType:
        return self._retry_slice_dataset(name, idx)

    def get_attr(self, name: str, key: str) -> Any:
        try:
            return self._retry_get_attr(name, key)
        except RetryTimeoutError as e:
            raise KeyError(name) from e

    @stop_iter_on_retry_timeout
    def iter_attrs(self, name: str) -> Iterator[str]:
        yield from self._retry_iter_attrs(name)

    @return_on_retry_timeout(default=0)
    def len_attrs(self, name: str) -> int:
        return self._retry_len_attrs(name)

    @stop_iter_on_retry_timeout
    def iter_item(self, name: str) -> Iterator[str | types.DataType]:
        yield from self._retry_iter_item(name)

    @return_on_retry_timeout(default=0)
    def len_item(self, name: str) -> int:
        return self._retry_len_item(name)

    def getattr_item(self, name: str, attr_name: str) -> Any:
        return self._retry_getattr_item(name, attr_name)

    @retry_with_reset
    def _retry_get_item(self, name: str) -> HDF5Item:
        return self._get_item(name)

    @retry_with_reset
    def _retry_slice_dataset(
        self, name: str, idx: types.DataIndexType
    ) -> types.DataType:
        return self._slice_dataset(name, idx)

    @retry_with_reset
    def _retry_get_attr(self, name: str, key: str) -> Any:
        return self._get_attr(name, key)

    @retry_with_reset
    def _retry_iter_attrs(self, name: str, start_index: int = 0) -> Iterator[str]:
        yield from self._iter_attrs(name, start_index=start_index)

    @retry_with_reset
    def _retry_len_attrs(self, name: str) -> int:
        return self._len_attrs(name)

    @retry_with_reset
    def _retry_iter_item(
        self, name: str, start_index: int = 0
    ) -> Iterator[str | types.DataType]:
        yield from self._iter_item(name, start_index=start_index)

    @retry_with_reset
    def _retry_len_item(self, name: str) -> int:
        return self._len_item(name)

    @retry_with_reset
    def _retry_getattr_item(self, name: str, attr_name: str) -> Any:
        return self._getattr_item(name, attr_name)

    def _get_item(self, name: str) -> NativeHDF5Item:
        item = self._native_items.get(name)
        if item is not None:
            return item
        try:
            item = self.file_obj[name]
        except KeyError:
            parent_name, _, item_name = name.rpartition(SEP)
            if not parent_name:
                parent_name = SEP
            if parent_name == name:
                raise
            parent_item = self._get_item(parent_name)
            is_complete = self._is_finished(parent_item)
            parent_item.is_complete = is_complete
            if not is_complete:
                raise  # item could still be created
            try:
                item = parent_item[item_name]  # try one more time
            except KeyError as e:
                if not _key_does_not_exist(e):
                    raise
                # item will never be created
                raise RetryTimeoutError(str(e)) from e
        self._native_items[name] = item
        return item

    def _slice_dataset(self, name: str, idx: types.DataIndexType) -> types.DataType:
        item = self._get_item(name)
        try:
            self._check_dataset_before_read(item)
            return item[idx]
        except RetryError as e:
            is_complete = self._is_finished(item)
            item.is_complete = is_complete
            if is_complete:
                raise IndexError(f"Index ({idx}) out of range") from e
            raise

    def _check_dataset_before_read(self, item: HDF5Dataset):
        pass

    def _get_attr(self, name: str, key: str) -> Any:
        item = self._get_item(name)
        try:
            return item.attrs[key]
        except KeyError:
            is_complete = self._is_finished(item)
            item.is_complete = is_complete
            if not is_complete:
                raise  # attribute could still be created
            try:
                return item.attrs[key]  # try one more time
            except KeyError as e:
                if not _key_does_not_exist(e):
                    raise
                # attribute will never be created
                raise RetryTimeoutError(str(e)) from e

    def _iter_attrs(self, name: str, start_index: int = 0) -> Iterator[str]:
        item = self._get_item(name)
        is_complete = self._is_initialized(item)
        item.is_complete = is_complete
        if start_index == 0:
            yield from item.attrs
        else:
            yield from list(item.attrs)[start_index:]
        if not is_complete:
            raise RetryError("attributes could still be added")

    def _len_attrs(self, name: str) -> int:
        item = self._get_item(name)
        return len(item.attrs)

    def _iter_item(
        self, name: str, start_index: int = 0
    ) -> Iterator[str | types.DataType]:
        item = self._get_item(name)
        self._check_dataset_before_read(item)
        if self.is_group(item):
            is_complete = self._is_initialized(item)
            item.is_complete = is_complete
            for key in self._iter_group(item, start_index):
                is_complete = False
                yield key
            if not is_complete:
                raise RetryError("children could still be added to the group")
        else:
            is_complete = self._is_finished(item)
            item.is_complete = is_complete
            for data in self._iter_dataset(item, start_index):
                is_complete = False
                yield data
            if not is_complete:
                raise RetryError("dataset could still grow")

    @staticmethod
    def is_group(h5item: HDF5Item):
        return isinstance(h5item, (h5py.Group, h5py.File))

    def _iter_group(self, h5group: HDF5Group, start_index: int) -> Iterator[str]:
        try:
            if start_index == 0:
                yield from h5group
            else:
                yield from list(h5group.keys())[start_index:]
        except ValueError:
            raise RetryWithoutResetError(f"Failed accessing group {h5group}")

    def _iter_dataset(
        self, h5dataset: HDF5Dataset, start_index: int
    ) -> Iterator[types.DataType]:
        try:
            if start_index == 0:
                yield from h5dataset
            else:
                for i in range(start_index, len(h5dataset)):
                    yield h5dataset[i]
        except ValueError:
            raise RetryWithoutResetError(f"Failed accessing dataset {h5dataset}")

    def _len_item(self, name: str) -> int:
        item = self._get_item(name)
        if getattr(item, "ndim", None) == 0:
            # h5py error does not need to be retried
            raise TypeError("Attempt to take len() of scalar dataset")
        try:
            return len(item)
        except RetryError as e:
            is_complete = self._is_finished(item)
            item.is_complete = is_complete
            if is_complete:
                raise KeyError(name) from e
            raise

    def _getattr_item(self, name: str, attr_name: str) -> Any:
        item = self._get_item(name)
        try:
            return getattr(item, attr_name)
        except RetryError as e:
            is_complete = self._is_finished(item)
            item.is_complete = is_complete
            if is_complete:
                raise AttributeError(attr_name) from e
            raise

    def _is_initialized(self, h5item: HDF5Item) -> bool:
        return True

    def _is_finished(self, h5item: HDF5Item) -> bool:
        return True


def _key_does_not_exist(exc: KeyError) -> bool:
    """
    A real KeyError (meaning that the key does not exist) gives the following error message:

    .. code

        KeyError: "Unable to synchronously open object (object 'end_time' doesn't exist)"

    An example of a KeyError caused by another reason

    .. code

        KeyError: 'Unable to synchronously open object (addr overflow, addr = 64392369, size = 96, eoa = 64365353)'

    """
    return "doesn't exist" in str(exc)
