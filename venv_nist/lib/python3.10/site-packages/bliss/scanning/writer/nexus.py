import os
from numbers import Number, Integral
from typing import Optional

from silx.io.h5py_utils import top_level_names
from silx.utils.retry import RetryTimeoutError
from blisswriter.nexus.scan_meta import register_all_metadata_generators

from .base import FileWriter


class NexusWriter(FileWriter):
    """Write scan data in NeXus compliant HDF5 files."""

    _FILE_EXTENSION = "h5"
    _COMPRESSION_SCHEMES = (
        None,
        "none",
        "gzip",
        "bitshuffle",
        "byteshuffle" "gzip-byteshuffle",
        "lz4-bitshuffle",
    )

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        register_all_metadata_generators()

    @property
    def configurable(self) -> bool:
        raise NotImplementedError

    def get_last_scan_number(self, **fileoptions) -> int:
        filename = self.get_filename()
        if not filename or not os.path.isfile(filename):
            return 0
        try:
            scans = top_level_names(filename, retry_timeout=3, **fileoptions)
        except RetryTimeoutError:
            raise RuntimeError(f"cannot get the scan names from '{filename}'")
        if not scans:
            return 0
        return int(sorted(scans, key=float)[-1].split(".")[0])

    @property
    def compression_scheme(self) -> Optional[str]:
        return self._options.get("compression_scheme")

    @compression_scheme.setter
    def compression_scheme(self, value: Optional[str]):
        if value not in self._COMPRESSION_SCHEMES:
            raise AttributeError(
                f"must be only of these value: {self._COMPRESSION_SCHEMES}"
            )
        self._options["compression_scheme"] = value

    @property
    def chunk_size(self) -> Optional[int]:
        chunk_nbytes = self._options.get("chunk_nbytes")
        if chunk_nbytes is None:
            return None
        return chunk_nbytes << 20

    @chunk_size.setter
    def chunk_size(self, value):
        if value is None or isinstance(value, Number):
            assert value is None or value > 0, value
            self._options["chunk_nbytes"] = int(value * 1024**2)
        else:
            raise TypeError(value)

    @property
    def compression_limit(self) -> Optional[int]:
        compression_limit_nbytes = self._options.get("compression_limit_nbytes")
        if compression_limit_nbytes is None:
            return None
        return compression_limit_nbytes << 20

    @compression_limit.setter
    def compression_limit(self, value):
        if value is None or isinstance(value, Number):
            self._options["compression_limit_nbytes"] = int(value * 1024**2)
        else:
            raise TypeError(value)

    @property
    def chunk_split(self) -> Optional[Integral]:
        return self._options.get("chunk_split")

    @chunk_split.setter
    def chunk_split(self, value):
        if value is None or isinstance(value, Integral):
            assert value is None or value > 0, value
            self._options["chunk_split"] = value
        else:
            raise TypeError(value)

    @property
    def separate_scan_files(self) -> Optional[bool]:
        return self._options.get("separate_scan_files")

    @separate_scan_files.setter
    def separate_scan_files(self, value):
        if value is None or isinstance(value, bool):
            self._options["separate_scan_files"] = value
        else:
            raise TypeError(value)

    def get_writer_options(self) -> dict:
        # See nexus_writer_service.io.h5_config.guess_dataset_config
        writer_options = super().get_writer_options()
        separate_scan_files = writer_options.pop("separate_scan_files", None)
        return {
            "chunk_options": writer_options,
            "separate_scan_files": separate_scan_files,
        }
