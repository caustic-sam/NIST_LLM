import logging
from collections import abc
import datetime
from typing import (
    Dict,
    Optional,
    Any,
    Union,
    MutableMapping,
    Iterator,
    Tuple,
    List,
    Set,
    Generator,
)

import h5py

from ..nexus import devices as nexus_devices
from ..nexus import mapping as nexus_mapping
from ..io import nexus
from .datasets import Hdf5DatasetWriter
from .references import Hdf5ScanReferenceWriter
from . import scan_url_info
from ..utils.logging_utils import CustomLogger
from ..utils.array_order import Order


_logger = logging.getLogger(__name__)


class _NotProvided:
    pass


_NOT_PROVIDED = _NotProvided()


class _BaseInfo(abc.MutableMapping):
    def __init__(
        self,
        identifier: str,
        name: str,
        info: Optional[MutableMapping] = None,
        parent_info: Optional["_BaseInfo"] = None,
        parent_logger: Optional[Union[logging.Logger, logging.LoggerAdapter]] = None,
    ) -> None:
        self._name = name
        self._identifier = identifier
        if info is None:
            info = dict()
        self._info = info
        self._parent_info = parent_info

        if parent_logger is None:
            parent_logger = _logger
        self._logger = parent_logger

    @property
    def identifier(self) -> str:
        return self._identifier

    @property
    def name(self) -> str:
        return self._name

    @property
    def logger(self) -> CustomLogger:
        return self._logger

    def __repr__(self) -> str:
        return self._identifier

    def __str__(self) -> str:
        return self._name

    def __getitem__(self, key: str) -> Any:
        return self._info[key]

    def __setitem__(self, key: str, value: Any):
        self._info[key] = value

    def __delitem__(self, key: str) -> None:
        del self._info[key]

    def __iter__(self) -> Iterator:
        return iter(self._info)

    def __len__(self) -> int:
        return len(self._info)

    def get(self, key: str, default: Any = _NOT_PROVIDED) -> Any:
        """When a key is missing it first tries from the parent. If that
        fails well it returns the default. Default value (when explicitly provided)
        and parent value are cached in this object and in the parent.
        """
        try:
            value = self[key]
        except KeyError:
            if self._parent_info is not None:
                value = self._parent_info.get(key, default=default)
                self[key] = value
            elif default is _NOT_PROVIDED:
                value = None
            else:
                value = default
                self[key] = value
        return value


class SubScanInfo(_BaseInfo):
    def __init__(
        self,
        identifier: str,
        name: str,
        scan_info: "ScanInfo",
        info: Optional[MutableMapping] = None,
        parent_logger: Optional[Union[logging.Logger, logging.LoggerAdapter]] = None,
    ) -> None:
        if parent_logger is None:
            parent_logger = _logger
        parent_logger = CustomLogger(parent_logger, name)
        super().__init__(
            identifier,
            name,
            info=info,
            parent_info=scan_info,
            parent_logger=parent_logger,
        )
        self._dataset_writers: Dict[str, Hdf5DatasetWriter] = dict()
        self._scanref_writers: Dict[str, Hdf5ScanReferenceWriter] = dict()

    def get_reference_writer(
        self, identifier: str
    ) -> Optional[Hdf5ScanReferenceWriter]:
        return self._scanref_writers.get(identifier)

    def get_dataset_writer(self, identifier: str) -> Optional[Hdf5DatasetWriter]:
        return self._dataset_writers.get(identifier)

    def reference_items(self) -> Iterator[Tuple[str, Hdf5ScanReferenceWriter]]:
        return self._scanref_writers.items()

    def dataset_items(self) -> Iterator[Tuple[str, Hdf5DatasetWriter]]:
        return self._dataset_writers.items()

    def add_reference(
        self, identifier: str, filename: str, nxroot
    ) -> Hdf5ScanReferenceWriter:
        ref = self._scanref_writers.get(identifier)
        if ref is not None:
            raise RuntimeError(f"Reference '{identifier}' already added")
        ref = Hdf5ScanReferenceWriter(
            filename=filename,
            parent="/",
            filecontext=nxroot,
            nreferences=0,
            parent_logger=self.logger,
        )
        self._scanref_writers[identifier] = ref
        self.logger.debug("Created reference writer %s", ref)
        return ref

    def add_dataset(
        self,
        identifier: str,
        filename: str,
        nxroot: Generator[Optional[nexus.nxRoot], None, None],
        parent: h5py.Group,
        device: dict,
        scan_shape: Tuple[int],
        scan_save_shape: Tuple[int],
        saveorder: Order,
        chunk_options: dict,
        data_info: dict,
    ) -> Hdf5DatasetWriter:

        dset = self._dataset_writers.get(identifier)
        if dset is not None:
            raise RuntimeError(f"Dataset '{identifier}' already added")
        dset = Hdf5DatasetWriter(
            filename=filename,
            parent=parent,
            filecontext=nxroot,
            device=device,
            scan_shape=scan_shape,
            scan_save_shape=scan_save_shape,
            saveorder=saveorder,
            publishorder=saveorder,
            parent_logger=self.logger,
            chunk_options=chunk_options,
            **data_info,
        )
        self._dataset_writers[identifier] = dset
        self.logger.debug("Created dataset writer %s", identifier)
        return dset

    def flush(self) -> None:
        for writer in self._dataset_writers.values():
            if writer.npoints:
                writer.flush()
        for writer in self._scanref_writers.values():
            if writer.npoints:
                writer.flush()


class ScanInfo(_BaseInfo):
    def __init__(
        self,
        identifier: str,
        name: str,
        info: Optional[MutableMapping] = None,
        configurable: bool = True,
        flat: bool = True,
        short_names: bool = True,
        multivalue_positioners: bool = False,
        writer_name: Optional[str] = None,
        parent_logger: Optional[Union[logging.Logger, logging.LoggerAdapter]] = None,
    ) -> None:
        super().__init__(identifier, name, info=info, parent_logger=parent_logger)

        # Save options
        self._configurable = configurable
        self._short_names = short_names
        self._multivalue_positioners = multivalue_positioners
        self._flat = flat
        self._save_order = Order("C")
        self._writer_name = writer_name

        # Runtime state
        self._filename: Optional[str] = None
        self._devices: Dict[
            str, Dict[str, str]
        ] = dict()  # keys: subscan names (from acquisition chain)
        self._subscans: Dict[
            str, SubScanInfo
        ] = dict()  # keys: subscan identifiers (added)

    def get_save_options(self) -> dict:
        return {
            "configurable": self._configurable,
            "short_names": self._short_names,
            "multivalue_positioners": self._multivalue_positioners,
            "flat": self._flat,
        }

    def get(self, key, default=None, subscan: Optional[SubScanInfo] = None):
        if subscan is None:
            return super().get(key, default=default)
        if subscan is None:
            return super().get(key, default=default)
        return subscan.get(key, default=default)

    def get_subscan(self, identifier: str) -> "SubScanInfo":
        return self._subscans[identifier]

    def add_subscan(
        self, identifier: str, name: str, info: Optional[MutableMapping] = None
    ) -> "SubScanInfo":
        subscan = self._subscans.get(identifier)
        if subscan is not None:
            raise RuntimeError(f"Subscan '{identifier}' already added")
        subscan = SubScanInfo(
            identifier, name, self, info=info, parent_logger=self._logger
        )
        self._subscans[identifier] = subscan
        return subscan

    def iter_subscans(self) -> Iterator[Tuple[str, SubScanInfo]]:
        yield from self._subscans.items()

    @property
    def _expected_subscans(self):
        """
        Subscan names for which there are devices defined.
        Ordered according to position in acquisition chain.
        """
        return list(sorted(self.devices.keys()))

    def nxentry_name(self, subscan: SubScanInfo) -> Optional[str]:
        """Name of the NXentry associated with a subscan"""
        if self.scan_number is None:
            self._log_h5creation("scan number not specified")
            return None
        try:
            chain_index = self._expected_subscans.index(subscan.name)
        except ValueError:
            self._log_h5creation(
                f"subscan '{subscan.name}' not in the acquisition chain"
            )
            return None
        return f"{self.scan_number}.{chain_index + 1}"

    def nxentry_create_args(self):
        """Arguments for NXentry creation"""
        start_time = self.get("start_time")
        if not start_time:
            self._log_h5creation("'start_time' not specified")
            return None
        title = self.get("title")
        if not title:
            self._log_h5creation("'title' not specified")
            return None
        start_time = datetime.datetime.fromisoformat(start_time)
        nxdict = {"title": title}
        kwargs = {"start_time": start_time, "nxdict": nxdict}
        return kwargs

    def _log_h5creation(self, reason: str) -> None:
        self._logger.debug("HDF5 group not created ({%s})", reason)

    @property
    def devices(self):
        """Maps subscan name to a dictionary of devices,
        which maps fullname to device info. Ordered
        according to position in acquisition chain.
        """
        if not self._devices:
            config_devices = self.config_writer.get("devices", dict())
            self._devices = nexus_devices.device_info(
                config_devices,
                self,
                scan_ndim=self.scan_ndim(),
                short_names=self._short_names,
                multivalue_positioners=self._multivalue_positioners,
            )
        return self._devices

    def get_device_info(
        self, subscan: SubScanInfo, channel_name: Optional[str]
    ) -> Optional[Dict]:
        if not channel_name:
            return None
        subdevices = self.devices[subscan.name]
        return nexus_mapping.get_device_info(channel_name, subdevices)

    @property
    def filename(self) -> Optional[str]:
        if self._filename is None:
            self._filename = scan_url_info.scan_filename(self, raw=True)
        return self._filename

    def get_urls(self) -> List[str]:
        filename = self.filename
        if not filename:
            return list()
        ret = dict()
        for _, subscan in self.iter_subscans():
            name = self.nxentry_name(subscan)
            ret[name.split(".")[-1]] = filename + "::/" + name
        return [v for _, v in sorted(ret.items())]

    def get_subscan_url(self, subscan: SubScanInfo) -> Optional[str]:
        filename = self.filename
        if not filename:
            return None
        return f"{self.filename}::/{self.nxentry_name(subscan)}"

    def get_master_filenames(self) -> Dict[str, str]:
        info = self.config_writer
        if info:
            return info.get("masterfiles", dict())
        filename = self.filename
        master_filename = self.get("filename")
        if filename == master_filename:
            return dict()
        return {"dataset": master_filename}

    def saving_enabled(self) -> bool:
        """Saving intended for this scan?"""
        if not self.get("save", False):
            return False
        if not self._writer_name:
            return True
        return self.get("data_writer", None) == self._writer_name

    @property
    def scan_number(self) -> Optional[int]:
        return self.get("scan_nb")

    @property
    def scan_start_time(self) -> Optional[str]:
        return self.get("start_time")

    @property
    def scan_end_time(self) -> Optional[str]:
        return self.get("end_time")

    @property
    def writer_options(self) -> dict:
        return self.get("writer_options", dict())

    @property
    def chunk_options(self) -> dict:
        return self.writer_options.get("chunk_options", dict())

    @property
    def config_writer(self) -> dict:
        if self._configurable:
            return self.get("nexuswriter", dict())
        return dict()

    @property
    def instrument_info(self) -> dict:
        return self.config_writer.get("instrument_info", dict())

    @property
    def config_technique(self) -> dict:
        return self.config_writer.get("technique", dict())

    @property
    def technique_name(self) -> str:
        return self.config_technique.get("name", "")

    @property
    def applications(self) -> dict:
        return self.config_technique.get("applications", dict())

    @property
    def positioner_info(self) -> dict:
        return self.get("positioners", dict())

    @property
    def motors(self) -> List[str]:
        return list(self.positioner_info.get("positioners_start", dict()).keys())

    def scan_ndim(self, subscan: Optional[str] = None) -> int:
        """Number of dimensions of the scan or subscan (0 means a scalar scan but currently
        no scan has this)
        """
        default = 1  # 1D scan by default
        # TODO: currently "data_dim" is not published in the subscan but in the scan.
        ndim = self.get("data_dim", default=default, subscan=subscan)
        if ndim <= 1:
            return ndim
        if all(
            self.get(f"npoints{i}", default=0, subscan=subscan)
            for i in range(1, ndim + 1)
        ):
            return ndim
        return 1

    def scan_size(self, subscan: Optional[str] = None) -> int:
        """Number of points in the subscan (0 means variable-length)"""
        # TODO: currently "npoints" is not published in the subscan but in the scan.
        return self.get("npoints", default=0, subscan=subscan)

    def scan_shape(self, subscan: Optional[str] = None) -> Tuple[int]:
        """Shape of the subscan (empty means a scalar scan like a ct).
        Dimensions with size 0 are variable (can only occur for 1D scans).
        """
        ndim = self.scan_ndim(subscan)
        if ndim == 0:
            # A scalar scan like a ct
            return tuple()
        if ndim == 1:
            # A 1D scan like a loopscan (fixed) or timescan (variable)
            return (self.scan_size(subscan),)
        # An nD scan like a mesh
        # Fast axis first
        s = tuple(
            self.get(f"npoints{i}", default=0, subscan=subscan)
            for i in range(1, ndim + 1)
        )
        if self._save_order.order == "C":
            # Fast axis last
            s = s[::-1]
        return s

    def scan_save_shape(self, subscan: Optional[str] = None) -> Tuple[int]:
        """Potentially flattened `scan_shape`"""
        if self._flat:
            if self.scan_ndim(subscan) == 0:
                return tuple()
            else:
                return (self.scan_size(subscan),)
        else:
            return self.scan_shape(subscan)

    def scan_save_ndim(self, subscan: Optional[str] = None) -> int:
        """Potentially flattened scan_ndim"""
        return len(self.scan_save_shape(subscan))

    @property
    def detector_ndims(self) -> Set[int]:
        """All detector dimensions"""
        ret = set()
        for _, subscan in self.iter_subscans():
            for _, dproxy in list(subscan.dataset_items()):
                if dproxy is not None:
                    ret.add(dproxy.detector_ndim)
        return ret

    @property
    def plots(self) -> Dict[str, dict]:
        """NXdata signals"""
        adict = self._flintplots
        if self._configurable:
            adict.update(self.config_technique.get("plots", {}))
        else:
            for ndim in self.detector_ndims:
                adict["plot{}D".format(ndim)] = {"ndim": ndim}
        return adict

    @property
    def _flintplots(self) -> dict:
        display_extra = self.get("display_extra", default={})
        items = display_extra.get("displayed_channels", None)
        if items is None:
            items = display_extra.get("plotselect", None)
        if items:
            return {"plotselect": {"items": items, "grid": True}}
        return dict()

    @property
    def plotselect(self) -> str:
        if not self._configurable or self._flintplots:
            return ""
        return self.config_technique.get("plotselect", "")
