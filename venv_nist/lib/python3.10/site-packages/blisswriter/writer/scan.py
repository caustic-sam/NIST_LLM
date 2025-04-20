import os
import re
import logging
import datetime
from pprint import pformat
from contextlib import contextmanager
from importlib.metadata import version
from typing import (
    Dict,
    Optional,
    Union,
    Callable,
    Generator,
    Tuple,
    Iterator,
    Mapping,
    List,
    Any,
)

import h5py
from pyicat_plus.metadata.nexus import create_nxtreedict

from . import info
from . import datasets
from . import references
from . import plots
from ..utils.periodic import PeriodicTask
from ..io import nexus
from ..io import os_utils
from ..nexus.mapping import normalize_nexus_name


class ScanWriter:
    """Manage the saving of a Bliss scan in NeXus format"""

    def __init__(
        self,
        identifier: str,
        name: str,
        short_names: bool = True,
        expand_variable_length: bool = True,
        hold_file_open: bool = True,
        locking: bool = True,
        swmr: bool = False,
        flat: bool = True,
        multivalue_positioners: bool = False,
        allow_external_nonhdf5: bool = False,
        allow_external_hdf5: bool = True,
        copy_non_external: bool = False,
        required_disk_space: float = 200,
        recommended_disk_space: float = 1024,
        stack_mcas: bool = False,
        configurable: bool = True,
        writer_name: Optional[str] = None,
        parent_logger: Optional[Union[logging.Logger, logging.LoggerAdapter]] = None,
        query_pool=None,
    ) -> None:
        # Save options
        self._expand_variable_length = expand_variable_length
        self._hold_file_open = hold_file_open
        self._locking = locking
        self._swmr = swmr
        self._allow_external_nonhdf5 = allow_external_nonhdf5
        self._allow_external_hdf5 = allow_external_hdf5
        self._copy_non_external = copy_non_external
        self._required_disk_space = required_disk_space
        self._recommended_disk_space = recommended_disk_space
        self._stack_mcas = stack_mcas
        self._applications: Dict[
            str, Callable[[info.SubScanInfo, str, Mapping], None]
        ] = {"appxrf": self._save_application_xrf}

        # Periodic tasks
        self._h5flush_task_period = 0.5  # seconds
        self._disk_check_period = 3  # seconds
        self._periodic_tasks = [
            PeriodicTask(self._check_required_disk_space, self._disk_check_period)
        ]

        #
        self._query_pool = query_pool

        # Runtime state
        self._nxroot: Dict[str, nexus.nxRoot] = dict()  # scan and master files
        self._nxentry: Optional[h5py.Group] = None  # scan file only
        self._nxentry_created = False
        self._exception_is_fatal = False  # REMOVE
        if not hasattr(self, "_scan_info"):  # REMOVE when dropping the backport
            self._scan_info = info.ScanInfo(
                identifier,
                name,
                flat=flat,
                short_names=short_names,
                multivalue_positioners=multivalue_positioners,
                configurable=configurable,
                writer_name=writer_name,
                parent_logger=parent_logger,
            )

    def get_save_options(self) -> dict:
        return {
            **self._scan_info.get_save_options(),
            "expand_variable_length": self._expand_variable_length,
            "hold_file_open": self._hold_file_open,
            "locking": self._locking,
            "swmr": self._swmr,
            "allow_external_nonhdf5": self._allow_external_nonhdf5,
            "allow_external_hdf5": self._allow_external_hdf5,
            "copy_non_external": self._copy_non_external,
            "required_disk_space": self._required_disk_space,
            "recommended_disk_space": self._recommended_disk_space,
            "stack_mcas": self._stack_mcas,
        }

    @property
    def scan_info(self) -> info.ScanInfo:
        return self._scan_info

    def has_write_permissions(self):
        """This process has permission to write/create file and/or directory"""
        path = self._scan_info.filename
        if path:
            return os_utils.has_write_permissions(path)
        else:
            return True

    def disk_space_error(self) -> str:
        """Returns an error message when the disk space is running dangerously low."""
        return self._required_disk_space_msg(self._required_disk_space)

    def disk_space_warning(self) -> str:
        """Returns a warning message when the disk space is running low."""
        return self._required_disk_space_msg(self._recommended_disk_space)

    def _required_disk_space_msg(self, required_disk_space: int) -> str:
        path = self._scan_info.filename
        if not path:
            return ""
        if os_utils.has_required_disk_space(
            path, required_disk_space, query_pool=self._query_pool
        ):
            return ""
        return "Free disk space below {:.0f} MB".format(required_disk_space)

    def _check_required_disk_space(self) -> None:
        """
        :raises RuntimeError: when not enough space on disk
        """
        err_msg = self.disk_space_error()
        if err_msg:
            raise RuntimeError(err_msg)

    def process_periodic_tasks(self):
        """Execute tasks after succesfully processing a Redis event"""
        for task in self._periodic_tasks:
            task.execute()

    def add_dataset(
        self,
        subscan: info.SubScanInfo,
        identifier: str,
        parent: h5py.Group,
        device: dict,
        data_info: dict,
    ) -> datasets.Hdf5DatasetWriter:
        scan_shape = self._scan_info.scan_shape(subscan)
        scan_save_shape = self._scan_info.scan_save_shape(subscan)
        return subscan.add_dataset(
            identifier,
            self._scan_info.filename,
            self.nxroot,
            parent,
            device,
            scan_shape,
            scan_save_shape,
            self._scan_info._save_order,
            self._scan_info.chunk_options,
            data_info,
        )

    def add_reference(
        self, subscan: info.SubScanInfo, identifier: str
    ) -> references.Hdf5ScanReferenceWriter:
        return subscan.add_reference(identifier, self._scan_info.filename, self.nxroot)

    @contextmanager
    def nxroot(self, filename=None) -> Generator[Optional[nexus.nxRoot], None, None]:
        """
        Yields the NXroot instance (h5py.File) or None
        when information is missing
        """
        if not filename:
            filename = self._scan_info.filename
            if not filename:
                self._scan_info.logger.debug(
                    "HDF5 group not created yet ('filename' missing)"
                )
        nxroot = self._nxroot.get(filename, None)
        if nxroot is None:
            if filename:
                self._check_required_disk_space()
                try:
                    with nexus.nxRoot(filename, **self._nxroot_kwargs) as nxroot:
                        with self._nxroot_flush_task(nxroot):
                            try:
                                self._nxroot[filename] = nxroot
                                yield nxroot
                            finally:
                                self._nxroot.pop(filename, None)
                except OSError as e:
                    if nxroot is None and nexus.isLockedError(e):
                        self._exception_is_fatal = True
                        raise RuntimeError(nexus.lockedErrorMessage(filename)) from None
                    else:
                        raise
            else:
                yield None
        else:
            yield nxroot

    @property
    def _nxroot_kwargs(self) -> dict:
        if nexus.ISSUE_1641_FIXED:
            rootattrs = {
                "creator": "blissdata",
                "create_version": version("blissdata"),
                "publisher": self._scan_info.get("publisher", "bliss"),
                "publisher_version": self._scan_info.get("publisher_version", None),
            }
        else:
            rootattrs = {
                "creator": "blissdata",
                "create_version": version("blissdata"),
                "publisher_version": self._scan_info.get("publisher_version", None),
            }
        return {
            "mode": "a",
            "locking": self._locking,
            "swmr": self._swmr,
            "rootattrs": rootattrs,
        }

    @contextmanager
    def _nxroot_flush_task(self, nxroot):
        task = PeriodicTask(nxroot.flush, self._h5flush_task_period)
        self._periodic_tasks.append(task)
        try:
            yield
        finally:
            self._periodic_tasks.remove(task)

    @contextmanager
    def _modify_nxroot(
        self, filename=None
    ) -> Generator[Optional[nexus.nxRoot], None, None]:
        with self.nxroot(filename=filename) as nxroot:
            if nxroot is None:
                yield nxroot
            else:
                with nxroot.protect():
                    yield nxroot

    @contextmanager
    def nxentry(
        self, subscan: info.SubScanInfo
    ) -> Generator[Optional[h5py.Group], None, None]:
        with self.nxroot() as nxroot:
            if nxroot is None:
                yield None
                return
            if self._nxentry is None:
                nxentry = self._require_nxentry(nxroot, subscan)
                try:
                    self._nxentry = nxentry
                    yield nxentry
                finally:
                    self._nxentry = None
            else:
                yield self._nxentry

    def _require_nxentry(
        self, nxroot: nexus.nxRoot, subscan: info.SubScanInfo
    ) -> Optional[h5py.Group]:
        name = self._scan_info.nxentry_name(subscan)
        if not name:
            return None
        nxentry = nxroot.get(name, None)
        if nxentry is None:
            kwargs = self._scan_info.nxentry_create_args()
            if not kwargs:
                return None
            try:
                nxentry = nexus.nxEntry(nxroot, name, raise_on_exists=True, **kwargs)
            except nexus.NexusInstanceExists:
                self._exception_is_fatal = True
                raise RuntimeError(
                    f"More than one writer is writing to {self._scan_info.filename}"
                )
            else:
                self._nxentry_created = True
            url = repr(self._scan_info.get_subscan_url(subscan))
            name = repr(subscan.name)
            self._scan_info.logger.info("Start writing subscan %s to %s", name, url)
        elif not self._nxentry_created:
            self._exception_is_fatal = True
            raise RuntimeError(
                f"Scan {name} already exists in {self._scan_info.filename}"
            )
        return nxentry

    @contextmanager
    def nxinstrument(
        self, subscan: info.SubScanInfo
    ) -> Generator[Optional[h5py.Group], None, None]:
        with self.nxentry(subscan) as nxentry:
            if nxentry is None:
                yield None
            else:
                yield nexus.nxInstrument(
                    nxentry, "instrument", nxdict=self._scan_info.instrument_info
                )

    @contextmanager
    def nxdetector(
        self, subscan: info.SubScanInfo, name: str, **kwargs
    ) -> Generator[Optional[h5py.Group], None, None]:
        with self.nxinstrument(subscan) as nxinstrument:
            if nxinstrument is None:
                yield None
            else:
                yield nexus.nxDetector(nxinstrument, name, **kwargs)

    @contextmanager
    def nxpositioner(
        self, subscan: info.SubScanInfo, name: str, **kwargs
    ) -> Generator[Optional[h5py.Group], None, None]:
        with self.nxinstrument(subscan) as nxinstrument:
            if nxinstrument is None:
                yield None
            else:
                yield nexus.nxPositioner(nxinstrument, name, **kwargs)

    @contextmanager
    def nxcollection(
        self, subscan: info.SubScanInfo, name: str, **kwargs
    ) -> Generator[Optional[h5py.Group], None, None]:
        with self.nxinstrument(subscan) as nxinstrument:
            if nxinstrument is None:
                yield None
            else:
                yield nexus.nxCollection(nxinstrument, name, **kwargs)

    @contextmanager
    def nxpositioners(
        self, subscan: info.SubScanInfo, suffix: str = ""
    ) -> Generator[Optional[h5py.Group], None, None]:
        with self.nxcollection(subscan, "positioners" + suffix) as nxcollection:
            yield nxcollection

    @contextmanager
    def nxmeasurement(
        self, subscan: info.SubScanInfo
    ) -> Generator[Optional[h5py.Group], None, None]:
        with self.nxentry(subscan) as nxentry:
            if nxentry is None:
                yield None
            else:
                yield nexus.nxCollection(nxentry, "measurement")

    @contextmanager
    def nxnotes(
        self, subscan: info.SubScanInfo
    ) -> Generator[Optional[h5py.Group], None, None]:
        with self.nxentry(subscan) as nxentry:
            if nxentry is None:
                yield None
            else:
                yield nexus.nxCollection(nxentry, "notes")

    @contextmanager
    def device_parent_context(
        self, subscan, device: Dict
    ) -> Generator[Optional[h5py.Group], None, None]:
        parentname = normalize_nexus_name(device["device_name"])
        if device["device_type"] in ("positioner", "positionergroup"):
            # Add as separate positioner group
            parentcontext = self.nxpositioner
            parentcontextargs = subscan, parentname
        elif device["device_name"]:
            # Add as separate detector group
            parentcontext = self.nxdetector
            parentcontextargs = subscan, parentname
        else:
            # Add to generic 'measurement' group
            parentcontext = self.nxmeasurement
            parentcontextargs = (subscan,)
        with parentcontext(*parentcontextargs) as parent:
            yield parent

    @contextmanager
    def instrument_group_context(
        self, subscan: info.SubScanInfo, groupname: str
    ) -> Generator[Optional[h5py.Group], None, None]:
        parentcontext = None
        for device in self._scan_info.devices[subscan.name].values():
            parentname = normalize_nexus_name(device["device_name"])
            if groupname != parentname:
                continue
            if device["device_type"] in ("positioner", "positionergroup"):
                parentcontext = self.nxpositioners
            elif device["device_name"]:
                parentcontext = self.nxdetector
            break
        if parentcontext is None:
            yield None
            return
        with parentcontext(subscan, groupname) as parent:
            yield parent

    def _save_reference_mode(self, file_format):
        """
        Save reference as external link (or string uri) or copy data to hdf5
        """
        if file_format == "hdf5":
            external = self._allow_external_hdf5 and nexus.HASVIRTUAL
        elif file_format == "edf":
            external = self._allow_external_nonhdf5
        else:
            external = True
            file_format = None
        if not external and not self._copy_non_external:
            file_format = None
            external = True
        return external, file_format

    def _save_device_metadata(self, subscan: info.SubScanInfo):
        """Get all device metadata and save them under the appropriate NXdetector or NXpositioner groups."""
        channels = self._scan_info.get("channels")
        devices = self._scan_info.get("devices")
        visited = set()
        for fullname, device in self._scan_info.devices[subscan.name].items():
            if device["device_name"] in visited:
                continue
            channel_info = channels.get(fullname)
            if not channel_info:
                continue
            device_name = channel_info.get("device")
            if not device_name:
                continue
            metadata = devices[device_name].get("metadata")
            if not metadata:
                continue

            metadata_keys = device["metadata_keys"]
            if metadata_keys:
                metadata = {
                    metadata_keys[k]: v
                    for k, v in metadata.items()
                    if k in metadata_keys
                }
            if not metadata:
                continue

            with self.device_parent_context(subscan, device) as parent:
                if parent is None:
                    continue
                if not parent.name.endswith("measurement"):
                    self._device_dicttonx_helper(parent, metadata)
                visited.add(device["device_name"])

    def _update_subscan_status(self, subscan: info.SubScanInfo, status: str) -> None:
        status = status.upper()
        final = status in ("SUCCEEDED", "FAILED")
        with self.nxentry(subscan) as nxentry:
            if nxentry is None:
                return
            nxnote = nexus.nxNote(nxentry, "writer")
            nexus.updateDataset(nxnote, "status", status)
            with self._modify_nxroot():
                nexus.updated(nxentry, final=final, parents=True)
            subscan.logger.info("Scan marked as %s in HDF5", status)

    def positioner_iter(
        self,
        subscan: info.SubScanInfo,
        onlyprincipals: bool = True,
        onlymasters: bool = True,
    ) -> Iterator[Tuple[str, datasets.Hdf5DatasetWriter]]:
        """Yields all positioner dataset handles

        :param subscan:
        :param onlyprincipals: only the principal value of each positioner
        :param onlymasters: only positioners that are master in the acquisition chain
        :returns: fullname and dataset handles
        """
        for fullname, dproxy in list(subscan.dataset_items()):
            if dproxy.device_type in ("positioner", "positionergroup"):
                if onlyprincipals and dproxy.data_type != "principal":
                    continue
                if onlymasters and dproxy.master_index < 0:
                    continue
                yield fullname, dproxy

    def _iter_detector_datasets(
        self, subscan: info.SubScanInfo
    ) -> Iterator[Tuple[str, datasets.Hdf5DatasetWriter]]:
        """Yields all dataset handles except for positioners

        :param subscan:
        :returns: fullname and dataset handle
        """
        for fullname, dproxy in list(subscan.dataset_items()):
            if dproxy.device_type not in ("positioner", "positionergroup"):
                yield fullname, dproxy

    def _save_positioners(self, subscan: info.SubScanInfo) -> None:
        """Save fixed snapshots of motor positions.

        :param Subscan subscan:
        """
        info = self._scan_info.positioner_info
        units = info.get("positioners_units", {})

        # Positions at the beginning of the scan
        positioners = info.get("positioners_start", {})
        subscan.logger.info("Save {} motor positions".format(len(positioners)))
        self._save_positioners_snapshot(
            subscan, positioners, units, "_start", overwrite=False
        )
        self._save_positioners_snapshot(
            subscan, positioners, units, "", overwrite=False
        )
        positioners = info.get("positioners_dial_start", {})
        self._save_positioners_snapshot(
            subscan, positioners, units, "_dial_start", overwrite=False
        )

        # Positions at the end of the scan
        positioners = info.get("positioners_end", {})
        self._save_positioners_snapshot(
            subscan, positioners, units, "_end", overwrite=True
        )
        positioners = info.get("positioners_dial_end", {})
        self._save_positioners_snapshot(
            subscan, positioners, units, "_dial_end", overwrite=True
        )

    def _save_positioners_snapshot(
        self,
        subscan: info.SubScanInfo,
        positions: Mapping,
        units: Mapping,
        suffix: str,
        overwrite: bool = False,
    ) -> None:
        """Save fixed snapshot of motor positions.

        :param subscan:
        :param positions: name:position
        :param units: name:unit
        :param suffix: output suffix
        :param overwrite: goes for values and attributes
        """
        if not positions:
            return
        with self.nxpositioners(subscan, suffix=suffix) as nxpositioners:
            if nxpositioners is None:
                return
            for mot, pos in positions.items():
                unit = units.get(mot, None)
                exists = mot in nxpositioners
                if exists:
                    dset = nxpositioners[mot]
                    if overwrite:
                        dset[()] = pos
                    if unit and ("units" not in dset.attrs or overwrite):
                        dset.attrs["units"] = unit
                else:
                    if unit:
                        attrs = {"units": unit}
                    else:
                        attrs = {}
                    nexus.nxCreateDataSet(nxpositioners, mot, pos, attrs)

    def _add_to_measurement_group(
        self, subscan: info.SubScanInfo, dproxy: datasets.Hdf5DatasetWriter
    ) -> None:
        """Add link in measurement group."""
        with self.nxmeasurement(subscan) as measurement:
            if measurement is None:
                return
            if dproxy.parent == measurement.name:
                return
            linkname = dproxy.linkname
            if not linkname:
                dproxy.logger.warning("cannot be linked too")
                return
            linknames = [linkname]
            for linkname in linknames:
                if linkname in measurement:
                    self._scan_info.logger.warning(
                        "Duplicate name '%s' in the measurement group. Rename this detector or positioner.",
                        linkname,
                    )
                else:
                    nexus.createLink(measurement, linkname, dproxy.path)

    def _add_to_positioners_group(
        self, subscan: info.SubScanInfo, dproxy: datasets.Hdf5DatasetWriter
    ) -> None:
        """Add link in positioners group."""
        with self.nxpositioners(subscan) as parent:
            if parent is None:
                return
            linkname = dproxy.linkname
            try:
                del parent[linkname]
            except KeyError:
                pass
            nexus.createLink(parent, linkname, dproxy.path)

    def _save_subscan_user_metadata(self, subscan: info.SubScanInfo) -> None:
        """Dump metadata under the user scan metadata categories"""
        subscan.logger.info("Save scan user metadata")
        categories = set(self._scan_info["scan_meta_categories"])
        categories -= {"positioners", "nexuswriter"}
        nxtreedict = {cat: self._scan_info.get(cat, None) for cat in categories}
        self._subscan_dicttonx(subscan, nxtreedict)

    def _save_subscan_metadata(self, subscan: info.SubScanInfo) -> None:
        """Dump metadata dataset metadata"""
        subscan.logger.info("Save scan metadata")
        if self._scan_info.get("data_policy", "").upper() != "ESRF":
            return
        metadict = self._scan_info.get("dataset_metadata_snapshot")
        if not metadict:
            return
        # Remove unwanted ICAT field names (see hdf5_cfg.xml)
        skip = ("SamplePositioners_value", "SamplePositioners_name")
        metadict = {
            k: v
            for k, v in metadict.items()
            if k.startswith("Sample") and k not in skip
        }
        if not metadict:
            return
        nxtreedict = create_nxtreedict(metadict)
        self._subscan_dicttonx(subscan, nxtreedict)

    def _save_scan_status(self, subscan: info.SubScanInfo) -> None:
        """Save the reason the scan ended in Bliss"""
        end_reason = self._scan_info.get("end_reason", "NOT STARTED")
        with self.nxentry(subscan) as parent:
            if parent is None:
                return
            parent["end_reason"] = end_reason

    def _subscan_dicttonx(
        self, subscan: info.SubScanInfo, nxtreedict: Optional[Mapping]
    ) -> None:
        if not nxtreedict:
            return
        with self.nxentry(subscan) as parent:
            if parent is None:
                return

            nxtreedict_direct = dict()

            for catname, catcontent in nxtreedict.items():
                if not catcontent:
                    continue
                # Datasets are dumped directly
                if not isinstance(catcontent, Mapping):
                    nxtreedict_direct[catname] = catcontent
                    continue

                # Empty group
                if "NX_class" in catcontent:
                    catcontent["@NX_class"] = catcontent.pop("NX_class")
                if not (set(catcontent.keys()) - {"@NX_class"}):
                    continue

                # Instrument group should not be dumped directly
                if catname == "instrument":
                    directcontent = dict()
                    for name, value in catcontent.items():
                        # Datasets are dumped directly
                        if not isinstance(value, Mapping):
                            directcontent[name] = value
                            continue
                        with self.instrument_group_context(subscan, name) as group:
                            if group is None:
                                # Non-device groups are dumped directly
                                directcontent[name] = value
                                continue
                            # Device group is dumped with a specific parent for
                            # initialization purposes
                            self._device_dicttonx_helper(group, value)
                            continue
                    if not directcontent:
                        continue
                    catcontent = directcontent

                # Group can be dumped directly
                nxtreedict_direct[catname] = catcontent

            self._subscan_dicttonx_helper(subscan, parent, nxtreedict_direct)

    def _subscan_dicttonx_helper(
        self,
        subscan: info.SubScanInfo,
        parent: h5py.Group,
        nxtreedict: Optional[Mapping],
    ) -> None:
        if not nxtreedict or not purge_nxtreedict(nxtreedict):
            return
        try:
            nexus.dicttonx(nxtreedict, parent, update_mode="modify", add_nx_class=True)
        except Exception as e:
            self._set_state(self.STATES.FAULT, e)
            subscan.logger.exception(
                "Scan metadata cannot be saved:\n%s", pformat(nxtreedict)
            )

    def _device_dicttonx_helper(
        self, parent: h5py.Group, nxtreedict: Optional[Mapping]
    ):
        if not nxtreedict or not purge_nxtreedict(nxtreedict):
            return
        nxtreedict["@NX_class"] = parent.attrs["NX_class"]
        nexus.dicttonx(nxtreedict, parent, update_mode="modify", add_nx_class=True)

    def _save_subscan_notes(self, subscan: info.SubScanInfo) -> None:
        """Save notes for this subscan"""
        notes = self._scan_info.get("comments", [])
        if not notes:
            return
        with self.nxnotes(subscan) as parent:
            if parent is None:
                return
            subscan.logger.info("Save scan notes")
            for i, note in enumerate(notes, 1):
                nexus.nxNote(
                    parent,
                    f"note_{i:02d}",
                    data=note["message"],
                    type="text/plain",
                    date=datetime.datetime.fromisoformat(note["date"]),
                )

    def _create_master_links(self, subscan: info.SubScanInfo) -> None:
        """Links to the scan's NXentry"""
        filenames = self._scan_info.get_master_filenames()
        if not filenames:
            return
        with self.nxentry(subscan) as nxentry:
            if nxentry is None:
                return
            self._scan_info.logger.info("Create scan links in masters ...")
            prefix, _ = os.path.splitext(os.path.basename(nxentry.file.filename))
            prefix += "_"
            nxentry_name = nxentry.name[1:]  # remove the leading "/"
            for level, filename in filenames.items():
                if level == "dataset":
                    linkname = nxentry_name
                else:
                    linkname = prefix + nxentry_name
                with self.nxroot(filename=filename) as nxroot:
                    if nxroot is None:
                        continue
                    if linkname in nxroot:
                        continue
                    self._scan_info.logger.info(
                        "Create link '%s' in master '%s'", linkname, filename
                    )
                    nexus.createLink(nxroot, linkname, nxentry)

    def _create_plots(self, subscan: info.SubScanInfo) -> None:
        """Create default plot in Nexus structure"""
        with self.nxentry(subscan) as nxentry:
            if nxentry is None:
                return
            default = None
            plots = self._scan_info.plots
            if plots:
                positioners = [
                    dproxy
                    for _, dproxy in self.positioner_iter(
                        subscan, onlyprincipals=True, onlymasters=True
                    )
                ]
                plotselect = self._scan_info.plotselect
                subscan.logger.info(f"Create {len(plots)} plots")
            else:
                subscan.logger.info("No plots defined for saving")
            for plotname, plotparams in plots.items():
                if plotname in nxentry:
                    subscan.logger.warning(
                        f"Cannot create plot {repr(plotname)} (name already exists)"
                    )
                    continue
                nxproxy = self._create_nxdata_proxy(subscan, plotname, **plotparams)
                if not nxproxy:
                    nxproxy.logger.warning("Not created (no signals)")
                    continue
                nxproxy.add_axes(positioners, self._scan_info._save_order)
                default = nxproxy.save(nxentry, default, plotselect)
            # Default plot
            with self._modify_nxroot():
                if default is None:
                    nexus.markDefault(nxentry)
                else:
                    nexus.markDefault(default)

    def _create_nxdata_proxy(
        self,
        subscan: info.SubScanInfo,
        plotname: str,
        ndim: int = -1,
        grid: bool = False,
        items: List = None,
    ) -> plots.NXdataWriter:
        """Select plot signals based on detector dimensions.

        :param subscan:
        :param plotname:
        :param ndim: detector dimensions
        :param grid: preserve scan shape
        :returns:
        """
        nxproxy = plots.NXdataWriter(plotname, parent_logger=subscan.logger)
        if not items and ndim < 0:
            return nxproxy
        detector_datasets = sorted(
            self._iter_detector_datasets(subscan),
            key=lambda tpl: tpl[1].linkname or tpl[1].name,
        )
        if items:
            for configname in items:
                for fullname, dproxy in detector_datasets:
                    if self._matching_fullname(configname, fullname):
                        nxproxy.add_signal(dproxy, grid)
                        break
        else:
            for _, dproxy in detector_datasets:
                if dproxy.detector_ndim == ndim:
                    nxproxy.add_signal(dproxy, grid)
        return nxproxy

    @contextmanager
    def nxapplication(
        self, subscan: info.SubScanInfo, name: str, definition_name: str
    ) -> Generator[Optional[h5py.Group], None, None]:
        """Yields the NXsubentry instance (h5py.Group) or None when NXentry is missing

        :param subscan:
        :param name: name of the NXsubentry
        :param definition_name: Nexus application definition
        """
        with self.nxentry(subscan) as nxentry:
            if nxentry is None:
                yield None
                return
            # Find nxapplication (when existing)
            for child in nxentry:
                nxsubentry = nxentry[child]
                if (
                    child == name
                    and nexus.isNxClass(nxsubentry, "NXsubentry")
                    and definition_name == nxsubentry.attrs.get("definition", None)
                ):
                    break
            else:
                nxsubentry = None
            if nxsubentry is None:
                # Create nxapplication
                ret = self._nxapplication_create_args(name, definition_name)
                if ret is None:
                    yield None
                    return
                args, kwargs = ret
                nxsubentry = nexus.nxSubEntry(nxentry, *args, **kwargs)
            yield nxsubentry

    def _nxapplication_create_args(
        self, name: str, definition_name: str
    ) -> Tuple[tuple, dict]:
        """Arguments for application nxSubEntry creation"""
        start_time = self._scan_info.get("start_time")
        if not start_time:
            self._scan_info._log_h5creation("'start_time' not specified")
            return None
        start_time = datetime.datetime.fromisoformat(start_time)
        nxdict = {"definition": definition_name}
        args = (name,)
        kwargs = {"start_time": start_time, "nxdict": nxdict}
        return args, kwargs

    def _save_applications(self, subscan: info.SubScanInfo):
        """Save a Nexus application definitions"""
        for name, nxapplidef in self._scan_info.applications.items():
            nxclass = nxapplidef["class"].lower()
            save_app = self._applications.get(nxclass)
            if save_app is None:
                self._scan_info.logger.warning(
                    "NeXus application definitions '%s' is not supported", nxclass
                )
            else:
                save_app(subscan, name, nxapplidef)

    def _save_application_xrf(
        self, subscan: info.SubScanInfo, name: str, xrfapplidef: Mapping
    ):
        """Save XRF Nexus application definition"""
        with self.nxapplication(subscan, name, "APPxrf") as nxsubentry:
            if nxsubentry is None:
                return
            self._scan_info.logger.info("Create 'APPxrf' Nexus application")
            self._save_application_i0(subscan, nxsubentry, xrfapplidef)
            self._save_application_it(subscan, nxsubentry, xrfapplidef)
            self._save_application_mca(subscan, nxsubentry, xrfapplidef)
            nexus.updated(nxsubentry, final=True, parents=False)

    def _mca_iter(
        self, subscan: info.SubScanInfo
    ) -> Iterator[Tuple[str, datasets.Hdf5DatasetWriter]]:
        """Yields all principal mca dataset handle

        :param subscan:
        :returns: fullname and dataset handle
        """
        for fullname, dproxy in list(subscan.dataset_items()):
            if dproxy.device_type == "mca" and dproxy.data_type == "principal":
                yield fullname, dproxy

    def _get_application(
        self, nxapplidef: Mapping, attr: str, default: Any = None
    ) -> Any:
        """Get parameter from application definition"""
        ret = nxapplidef.get(attr, None)
        if ret is None:
            self._scan_info.logger.warning(
                "Application definition incomplete ('%s' is missing)", attr
            )
            ret = default
        return ret

    def _save_application_i0(
        self, subscan: info.SubScanInfo, parent: h5py.Group, nxapplidef: Mapping
    ) -> None:
        """Add beam monitor to the application

        :param parent: application subentry
        :param nxapplidef:
        """
        I0configname = self._get_application(nxapplidef, "I0")
        I0appliname = "i0"
        self._save_application_link(subscan, parent, I0configname, I0appliname)

    def _save_application_it(
        self, subscan: info.SubScanInfo, parent: h5py.Group, nxapplidef: Mapping
    ) -> None:
        """Add sample transmission to the application

        :param h5py.Group parent: application subentry
        :param dict nxapplidef:
        """
        Itconfigname = self._get_application(nxapplidef, "It")
        Itappliname = "it"
        self._save_application_link(subscan, parent, Itconfigname, Itappliname)

    def _save_application_mca(
        self, subscan: info.SubScanInfo, parent: h5py.Group, xrfapplidef: Mapping
    ) -> None:
        """Add MCA's tp the application

        :param Subscan subscan:
        :param h5py.Group parent: application subentry
        :param dict xrfapplidef:
        """
        # Mca names from the beamline configuration
        confignames = self._get_application(xrfapplidef, "mca", [])
        if not confignames:
            # All MCA's when not specified
            for fullname, dproxy in self._mca_iter(subscan):
                confignames.append(fullname)
            confignames = sorted(confignames)
        if not confignames:
            self._scan_info.logger.warning(
                "Application definition incomplete (no mca's found)"
            )
            return

        # Links or concatenate?
        concatenate = self._stack_mcas
        aslinks = len(confignames) == 1 or not concatenate
        if aslinks:
            method = self._save_application_links
        else:
            method = self._save_application_merged

        # Add mca
        withsuffix = len(confignames) > 1 and not concatenate
        if withsuffix:
            mcaapplinamefmt = "mca{:02d}"
        else:
            mcaapplinamefmt = "mca"
        method(
            subscan,
            parent,
            confignames,
            mcaapplinamefmt,
            devicetype="mca",
            datatype="principal",
        )

        if withsuffix:
            mcaapplinamefmt = "elapsed_time_mca{:02d}"
        else:
            mcaapplinamefmt = "elapsed_time"
        method(
            subscan,
            parent,
            confignames,
            mcaapplinamefmt,
            devicetype="mca",
            datatype="realtime",
        )

        if withsuffix:
            mcaapplinamefmt = "live_time_mca{:02d}"
        else:
            mcaapplinamefmt = "live_time"
        method(
            subscan,
            parent,
            confignames,
            mcaapplinamefmt,
            devicetype="mca",
            datatype="livetime",
        )

    def _save_application_merged(
        self,
        subscan: info.SubScanInfo,
        parent: h5py.Group,
        confignames: List[str],
        appliname: str,
        virtual: bool = True,
        **kwargs,
    ) -> None:
        """Add datasets to an application as one single dataset,
        virtual or merged copy.

        :param subscan:
        :param parent: application subentry
        :param confignames: names specified in the beamline
                            static configuration
        :param applifmt: format for application definition name
        :param virtual: virtual dataset or copy
        :param **kwargs: see `_iter_fullnames`
        """
        if not confignames or appliname in parent:
            return
        uris = []
        for configname in confignames:
            notfoundmsg = (
                "Application definition incomplete ({} not found for {})".format(
                    repr(configname), repr(appliname)
                )
            )
            for fullname in self._iter_fullnames(
                subscan, configname, notfoundmsg=notfoundmsg, **kwargs
            ):
                dproxy = subscan.get_dataset_writer(fullname)
                with dproxy.open() as dset:
                    if not uris:
                        value = {
                            "data": uris,
                            "fillvalue": dset.fillvalue,
                            "axis": 0,
                            "newaxis": True,
                            "virtual": virtual,
                        }
                        attrs = dset.attrs
                    uris.append(nexus.getUri(dset))
        if uris:
            nexus.nxCreateDataSet(parent, appliname, value, attrs)

    def _save_application_links(
        self,
        subscan: info.SubScanInfo,
        parent: h5py.Group,
        confignames: List[str],
        applifmt: str,
        **kwargs,
    ) -> None:
        """Add dataset links to an application.

        :param subscan:
        :param parent: application subentry
        :param confignames: names specified in the beamline
                            static configuration
        :param applifmt: format for application definition name
        :param **kwargs: see `_iter_fullnames`
        """
        if not confignames:
            return
        for i, configname in enumerate(confignames):
            appliname = applifmt.format(i)
            self._save_application_link(
                subscan, parent, configname, appliname, **kwargs
            )

    def _save_application_link(
        self,
        subscan: info.SubScanInfo,
        parent: h5py.Group,
        configname: str,
        appliname: str,
        **kwargs,
    ) -> None:
        """Link to a dataset in the application definition

        :param subscan:
        :param parent: application subentry
        :param configname: name specified in the beamline
                               static configuration
        :param appliname: name in the Nexus application definition
        :param **kwargs: see `_iter_fullnames`
        """
        if not configname or appliname in parent:
            return
        notfoundmsg = "Application definition incomplete ({} not found for {})".format(
            repr(configname), repr(appliname)
        )
        for fullname in self._iter_fullnames(
            subscan, configname, notfoundmsg=notfoundmsg, **kwargs
        ):
            dproxy = subscan.get_dataset_writer(fullname)
            nexus.createLink(parent, appliname, dproxy.path)

    def _matching_fullname(self, configname: str, fullname: str) -> bool:
        """Checks whether a Redis node's full name is referred to
        by name from the writer configuration.

        Examples:
            "iodet" refers to "simulation_diode_controller:iodet"
            "xmap1:det0" refers to "xmap1:realtime_det0"
            "xmap1:det0" refers to "simxmap1:spectrum_det0"

        :param configname: from the writer configuration
        :param fullname: node.fullname
        """
        seps = r"[\.:]"
        configparts = re.split(seps, configname)
        fullparts = re.split(seps, fullname)
        return all(
            pfull.endswith(pconfig)
            for pfull, pconfig in zip(fullparts[::-1], configparts[::-1])
        )

    def _iter_fullnames(
        self,
        subscan: info.SubScanInfo,
        configname: str,
        devicetype: Optional[str] = None,
        datatype: Optional[str] = None,
        notfoundmsg: Optional[str] = None,
    ) -> Iterator[str]:
        """Yield all Redis node's full names referred to by a name
        from the writer configuration.

        :param Subscan subscan:
        :param configname: name specified in the beamline
                           static configuration
        :param devicetype: device type
        :param datatype: data type
        :param notfoundmsg:
        :yields: Redis node fullname
        """
        incomplete = True
        for fullname, dproxy in self._iter_detector_datasets(subscan):
            if self._matching_fullname(configname, fullname):
                if (devicetype == dproxy.device_type or not devicetype) and (
                    datatype == dproxy.data_type or not datatype
                ):
                    incomplete = False
                    yield fullname
        if incomplete and notfoundmsg:
            self._scan_info.logger.warning(notfoundmsg)


def purge_nxtreedict(nxtreedict: dict) -> bool:
    """Returns `True` when the tree contains actual content."""
    for key, value in list(nxtreedict.items()):
        if isinstance(value, dict):
            has_content = purge_nxtreedict(value)
            if not has_content:
                nxtreedict.pop(key)
    return bool(set(nxtreedict.keys()) - {"@NX_class"})
