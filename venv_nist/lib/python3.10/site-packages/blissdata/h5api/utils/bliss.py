import os
import re
from collections.abc import Sequence
from warnings import warn

import h5py

from . import lima
from . import hdf5
from .hdf5_retry import RetryError


class BlissDynamicHDF5Handler(hdf5.DynamicHDF5Handler):
    """Each NXentry has a writer status which can be
    STARTING, RUNNING, SUCCEEDED or FAILED. This status
    is used as stop criterium for iteration.
    """

    def __init__(
        self,
        *args,
        lima_names: Sequence[str] = tuple(),
        instrument_name: str | None = None,
        lima_url_template: str | None = None,
        lima_url_template_args: dict | None = None,
        prioritize_non_native_h5items: bool = False,
        **kwargs,
    ):
        r"""
        :param \*args: For `DynamicHDF5Handler`.
        :param lima_names: Lima `NXdetector` group name in order to read the data non-natively.
        :param instrument_name: To be used in `lima_url_template`.
        :param lima_url_template: HDF5 URL of the Lima dataset template in the external Lima HDF5 files.
        :param lima_url_template_args: To be used in `lima_url_template`.
        :param prioritize_non_native_h5items: Try the non-native HDF5 interface when applicable (Lima only so far)
            for reading before the native `h5py` interface.
        :param \**kwargs: For `DynamicHDF5Handler`.
        """
        self._match_lima_group = [
            re.compile(
                f"{hdf5.SEP}[0-9]+\\.[0-9]+{hdf5.SEP}instrument{hdf5.SEP}{name}{hdf5.SEP}data"
            )
            for name in lima_names
        ]
        self._match_lima_dataset = [
            re.compile(
                f"{hdf5.SEP}[0-9]+\\.[0-9]+{hdf5.SEP}instrument{hdf5.SEP}{name}{hdf5.SEP}data"
            )
            for name in lima_names
        ]

        self._lima_url_template_args = lima_url_template_args or {}
        if instrument_name:
            warn(
                "`instrument_name` argument is deprecated. Please specify it as an entry of `lima_url_template_args` instead",
                DeprecationWarning,
            )
            self._lima_url_template_args["instrument_name"] = instrument_name
        self._lima_url_template = lima_url_template

        self._non_native_items = dict()
        self._prioritize_non_native_h5items = prioritize_non_native_h5items

        super().__init__(*args, **kwargs)

    def reset(self) -> None:
        super().reset()
        for item in self._non_native_items.values():
            item.reset()

    def close(self) -> None:
        for item in self._non_native_items.values():
            item.close()
        self._non_native_items = dict()
        super().close()

    def _check_dataset_before_read(self, item: hdf5.HDF5Dataset):
        """Check that the last source of a virtual dataset is released by the writer."""
        if not hasattr(item, "is_virtual"):
            return
        if not item.is_virtual:
            return
        sources = item.virtual_sources()
        if not sources:
            return
        _, filename, _, _ = sources[-1]
        if not os.path.isabs(filename):
            filename = os.path.abspath(
                os.path.join(os.path.dirname(item.file.filename), filename)
            )
        try:
            with h5py.File(filename, "r"):
                pass
        except Exception:
            raise RetryError(f"cannot open '{filename}'")

    def _get_item(self, name: str) -> hdf5.HDF5Item:
        h5item = self._non_native_items.get(name)
        if h5item is not None:
            return h5item

        if self._prioritize_non_native_h5items:
            # Try getting a Native item when Non-Native item is not applicable.

            h5item = self._get_item_non_native(name)

            if h5item is not None:
                return h5item

            return self._get_item_native(name)

        # Try getting a Non-Native item when the Native item does not exist.

        try:
            return self._get_item_native(name)
        except KeyError as e:
            exception = e

        h5item = self._get_item_non_native(name)

        if h5item is not None:
            return h5item

        raise exception

    def _get_item_native(self, name: str) -> hdf5.NativeHDF5Item:
        return super()._get_item(name)

    def _get_item_non_native(self, name: str) -> hdf5.NonNativeHDF5Item | None:
        if any(m.match(name) for m in self._match_lima_dataset):
            dirname = os.path.dirname(self.file_obj.filename)
            h5item = lima.LimaDataset(
                name,
                dirname,
                url_template=self._lima_url_template,
                url_template_args=self._lima_url_template_args,
            )
            self._non_native_items[name] = h5item
            return h5item

        if any(m.match(name) for m in self._match_lima_group):
            dirname = os.path.dirname(self.file_obj.filename)
            h5item = lima.LimaGroup(
                name,
                dirname,
                url_template=self._lima_url_template,
                url_template_args=self._lima_url_template_args,
            )
            self._non_native_items[name] = h5item
            return h5item

    @staticmethod
    def is_group(h5item: hdf5.HDF5Item):
        return isinstance(h5item, (h5py.Group, h5py.File, lima.LimaGroup))

    def _is_initialized(self, h5item: hdf5.HDF5Item) -> bool:
        try:
            if h5item.name == hdf5.SEP:
                return False
            scan = [s for s in h5item.name.split(hdf5.SEP) if s][0]
            nxentry = self.file_obj[scan]
        except AttributeError:
            raise RetryError("file is closed")
        if "end_time" in nxentry:
            # Last dataset written to the file
            return True
        return self._get_writer_status(nxentry) in ("RUNNING", "SUCCEEDED", "FAILED")

    def _is_finished(self, h5item: hdf5.HDF5Item) -> bool:
        try:
            if h5item.name == hdf5.SEP:
                # Assume there could be always more scans coming
                return False
            scan = [s for s in h5item.name.split(hdf5.SEP) if s][0]
            nxentry = self.file_obj[scan]
        except AttributeError:
            raise RetryError("file is closed")
        return "end_time" in nxentry  # Last dataset written to the file

    def _get_writer_status(self, nxentry: h5py.Dataset) -> str | None:
        nxnote = nxentry.get("writer", None)
        if nxnote is None:
            # writer notes not created yet
            return None
        status = nxnote.get("status", None)
        if status is None:
            # writer status not set yet
            return None
        status = status[()]
        try:
            status = status.decode()
        except AttributeError:
            pass
        return status
