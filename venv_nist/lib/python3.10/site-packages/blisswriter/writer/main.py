from copy import deepcopy
from contextlib import ExitStack
from typing import Dict, Union, Optional, Tuple

import numpy

from blissdata.redis_engine.scan import Scan as RedisScan
from blissdata.lima.client import lima_client_factory

from . import types
from . import typing
from .scan import ScanWriter
from .info import SubScanInfo
from .capture import capture_exceptions
from blisswriter.writer import scan_url_info
from blisswriter.writer.datasets import Hdf5DatasetWriter
from blisswriter.writer.references import Hdf5ScanReferenceWriter


class ScanWriterWithState:
    """Usage

    .. code:: python

        writer = ScanWriterWithState(identifier, name, ...)
        writer.initialize()
        # Optional channel initialization:
        writer.add_channel(channel1)
        writer.add_channel(channel2)
        ...
        writer.add_channel_data(channel1, data)
        writer.add_channel_data(channel2, data)
        ...
        writer.finalize()
    """

    def __init__(self, *args, **kw) -> None:
        self._scan_writer = ScanWriter(*args, **kw)
        self._scan_info = self._scan_writer.scan_info
        self._exit_stack = ExitStack()

        self._channel_to_subscan: Dict[str, SubScanInfo] = dict()
        self._subscan_to_pending_data: Dict[Dict[str, typing.ChannelDataType]] = dict()
        self._lima_clients: Dict[str, tuple(object, bool)] = dict()
        self._lima_proxy = None

    @property
    def scan_writer(self) -> ScanWriter:
        return self._scan_writer

    def initialize(self, scan_info: dict, data_store) -> bool:
        """Call when the scan is prepared"""
        self._data_store = data_store
        self._exit_stack = ExitStack().__enter__()

        self._scan_info.update(deepcopy(scan_info))
        if not self._scan_info.saving_enabled():
            return False

        # Initialize scan writing
        self._scan_info.logger.info(
            "Start writing to '%s' with options %s",
            self._scan_info.filename,
            self._scan_writer.get_save_options(),
        )

        if self._scan_writer._hold_file_open:
            ctx = self._scan_writer.nxroot()
            _ = self._exit_stack.enter_context(ctx)

        # Initialize subscan writing
        with capture_exceptions() as capture:
            for name in self._scan_info._expected_subscans:
                subscan = self._scan_info.add_subscan(name, name)

                adict = self._scan_info["acquisition_chain"][name]
                for device in adict["devices"]:
                    for channel in self._scan_info["devices"][device]["channels"]:
                        self._channel_to_subscan[channel] = subscan

                self._subscan_to_pending_data[subscan.identifier] = dict()

                with capture():
                    self._initialize_subscan(scan_info, subscan)

        self._scan_writer.process_periodic_tasks()

        return True

    def finalize(self, scan_info: dict, failed: bool) -> None:
        """Call at the end of the scan"""
        try:
            self._scan_writer.process_periodic_tasks()

            self._scan_info = self._scan_writer.scan_info
            if not self._scan_info.saving_enabled():
                return

            self._scan_info.logger.info("Finalize scan")

            self._scan_info.update(deepcopy(scan_info))

            with capture_exceptions() as capture:
                for _, subscan in self._scan_info.iter_subscans():
                    with capture():
                        self._flush_pending_data(subscan)
                    with capture():
                        self._finalize_subscan(scan_info, failed, subscan)
        finally:
            self._exit_stack.__exit__(None, None, None)
            self._exit_stack = None

    def _initialize_subscan(self, scan_info: dict, subscan: SubScanInfo):
        """One subscan is saved in one NXentry"""
        subscan.logger.info("Initialize subscan")
        with capture_exceptions() as capture:
            with capture():
                subscan.update(deepcopy(scan_info))
            with capture():
                self._scan_writer._update_subscan_status(subscan, "STARTING")
            with capture():
                self._scan_writer._save_positioners(subscan)
            with capture():
                self._scan_writer._create_master_links(subscan)
            with capture():
                self._scan_writer._save_device_metadata(subscan)
            with capture():
                self._scan_writer._save_subscan_user_metadata(subscan)
            with capture():
                self._scan_writer._save_subscan_metadata(subscan)
            with capture():
                if not capture.failed:
                    self._scan_writer._update_subscan_status(subscan, "RUNNING")

    def _finalize_subscan(
        self,
        scan_info: dict,
        failed: bool,
        subscan: SubScanInfo,
    ):
        subscan.logger.info("Finalize subscan")
        with capture_exceptions() as capture:
            with capture():
                subscan.flush()
            with capture():
                subscan.update(deepcopy(scan_info))
            with capture():
                self._scan_writer._save_positioners(subscan)
            with capture():
                self._scan_writer._create_plots(subscan)
            with capture():
                self._scan_writer._save_device_metadata(subscan)
            with capture():
                self._scan_writer._save_subscan_user_metadata(subscan)
            with capture():
                self._scan_writer._save_subscan_notes(subscan)
            with capture():
                self._scan_writer._save_subscan_metadata(subscan)
            with capture():
                self._scan_writer._save_applications(subscan)
            with capture():
                self._scan_writer._save_scan_status(subscan)
            with capture():
                if failed or capture.failed:
                    self._scan_writer._update_subscan_status(subscan, "FAILED")
                else:
                    self._scan_writer._update_subscan_status(subscan, "SUCCEEDED")

    def add_channel(self, channel: types.Channel) -> None:
        """Call when the channel is ready (optional)"""
        subscan = self._channel_to_subscan[channel.name]
        _ = self._get_writer(channel, subscan)

    def _get_writer(
        self,
        channel: types.Channel,
        subscan: SubScanInfo,
        requires_full_channel_info: bool = True,
    ) -> Union[Hdf5DatasetWriter, Hdf5ScanReferenceWriter]:
        self._scan_writer.process_periodic_tasks()
        if channel.data_type == types.ChannelDataType.SCAN_REFERENCE:
            return self._get_reference_writer(channel, subscan)
        return self._get_dataset_writer(
            channel, subscan, requires_full_channel_info=requires_full_channel_info
        )

    def _get_reference_writer(
        self,
        channel: types.Channel,
        subscan: SubScanInfo,
    ) -> Optional[Hdf5ScanReferenceWriter]:
        writer = subscan.get_reference_writer(channel.name)
        if writer is not None:
            return writer
        return self._scan_writer.add_reference(subscan, channel.name)

    def _get_dataset_writer(
        self,
        channel: types.Channel,
        subscan: SubScanInfo,
        requires_full_channel_info: bool = True,
    ) -> Optional[Hdf5DatasetWriter]:
        # Already initialized?
        writer = subscan.get_dataset_writer(channel.name)
        if writer is not None:
            return writer

        # Can be initialized?
        data_info, complete = self._detector_data_info(channel)
        if requires_full_channel_info and not complete:
            return None

        # Create parent: NXdetector, NXpositioner or measurement
        device = self._scan_writer.scan_info.get_device_info(subscan, channel.name)
        if device is None:
            return None

        with self._scan_writer.device_parent_context(subscan, device) as parent:
            if parent is None:
                return None
            # Save info associated to the device (not this specific dataset)
            if not parent.name.endswith("measurement"):
                self._scan_writer._device_dicttonx_helper(parent, device["device_info"])
            parent = parent.name

        # Everything is ready to create the dataset
        return self._scan_writer.add_dataset(
            subscan, channel.name, parent, device, data_info
        )

    def _detector_data_info(self, channel: types.Channel) -> Tuple[dict, bool]:
        info = dict()
        is_lima = channel.data_type == types.ChannelDataType.LIMA_STATUS
        if is_lima:
            images_per_file = channel.info["lima_info"].get("frame_per_file")
            if images_per_file:
                info["external_images_per_file"] = images_per_file
        else:
            info["external_images_per_file"] = None

        dtype = channel.info["dtype"]
        if dtype is not None:
            info["dtype"] = dtype

        detector_shape = channel.info["shape"]
        if detector_shape is not None:
            # Cannot handle variable dimensions in the detector for lima
            # because we are not allowed to check the actual lima data,
            # we only have references. We can handle variable dimensions
            # for other detectors because we have the data in memory.
            if not is_lima or all(n > 0 for n in detector_shape):
                info["detector_shape"] = detector_shape

        # Fill missing info
        allkeys = {"dtype", "detector_shape", "external_images_per_file"}
        complete = set(info.keys()) == allkeys
        if not complete:
            info.setdefault("detector_shape", tuple())  # 0D detector
            info.setdefault("dtype", float)
            info.setdefault("external_images_per_file", None)
        return info, complete

    def add_channel_data(
        self, channel: types.Channel, data: typing.ChannelDataType
    ) -> bool:
        """Call when the channel has data. Returns `False` when the data is buffered because
        the channel does not have enough information yet."""
        subscan = self._channel_to_subscan[channel.name]
        writer = self._get_writer(channel, subscan, requires_full_channel_info=True)

        pending_data = self._subscan_to_pending_data[subscan.identifier]
        pending = pending_data.get(channel.name)

        # To test pending data:
        # writer = None

        if writer is not None:
            if pending is not None:
                # First flush the pending data
                del pending_data[channel.name]
                self._add_data_to_writer(subscan, channel, writer, pending.get_data())
            self._add_data_to_writer(subscan, channel, writer, data)
            return True

        # The writer could not be instantiated yet. Buffer the data locally.
        if pending is None:
            cls = _CHANNEL_TYPE_TO_PENDING_CLASSES[channel.data_type]
            pending_data[channel.name] = cls(channel, data)
            return False

        pending.add_data(data)
        return False

    def _force_add_channel_data(
        self,
        channel: types.Channel,
        data: typing.ChannelDataType,
    ) -> None:
        """Add data to a writer (force initialization)."""
        subscan = self._channel_to_subscan[channel.name]
        writer = self._get_writer(channel, subscan, requires_full_channel_info=False)
        self._add_data_to_writer(subscan, channel, writer, data)

    def _add_data_to_writer(
        self,
        subscan: SubScanInfo,
        channel: types.Channel,
        writer: Union[Hdf5DatasetWriter, Hdf5ScanReferenceWriter],
        data: typing.ChannelDataType,
    ) -> None:
        """The data types are

        * `numpy.ndarray`: normal data
        * `list`: buffered normal data or ragged data (e.g. diode samples)
        * `dict`: lima status or reference to another scan
        """
        npoints_before = writer.npoints
        try:
            if channel.data_type == types.ChannelDataType.SCAN_REFERENCE:
                for scan_reference in data:
                    scan = self._data_store.load_scan(
                        scan_reference["key"], scan_cls=RedisScan
                    )
                    if scan.info.get("save"):
                        urls = scan_url_info.scan_urls(scan.info)
                        writer.add_references(urls)
            elif channel.data_type == types.ChannelDataType.LIMA_STATUS:
                try:
                    lima_client, use_references = self._lima_clients[channel.name]
                except KeyError:
                    lima_client = lima_client_factory(self._data_store, channel.info)
                    if not lima_client._saved:
                        return

                    if not writer.is_internal:
                        use_references, _ = self._scan_writer._save_reference_mode(
                            lima_client.file_format
                        )
                    else:
                        use_references = False

                    if use_references:
                        lima_client = lima_client_factory(
                            self._data_store, channel.info, ref_mode=True
                        )

                    self._lima_clients[channel.name] = (lima_client, use_references)

                last_status = data[-1]
                lima_client.update(**last_status)  # Only the last status is relevant
                self._add_lima_data(writer, lima_client, use_references)
            elif channel.data_type == types.ChannelDataType.NUMERIC_DATA:
                writer.add_internal(data)
            else:
                raise NotImplementedError(
                    f"Channel type {channel.data_type} not supported"
                )
        finally:
            if not npoints_before and writer.npoints:
                self._add_dataset_links(subscan, channel, writer)

    def _add_lima_data(
        self, writer: Hdf5DatasetWriter, lima_client, use_references: bool
    ) -> None:
        from_index = writer.npoints
        if use_references:
            _, file_format = self._scan_writer._save_reference_mode(
                lima_client.file_format
            )
            refs = lima_client[from_index:]
            if file_format == "hdf5":
                urls = [
                    (ref.file_path + "::" + ref.data_path, ref.index) for ref in refs
                ]
            else:
                urls = [(ref.file_path, ref.index) for ref in refs]
            writer.add_external(urls, file_format)
        else:
            # Read images from lima or file and add them to the writer
            images = lima_client[from_index:]
            if len(images) > 0:
                writer.add_internal(images)

    def _flush_pending_data(self, subscan: SubScanInfo):
        subscan.logger.info("Flush pending data")
        pending_data = self._subscan_to_pending_data[subscan.identifier]
        for pending in pending_data.values():
            self._force_add_channel_data(pending.channel, pending.get_data())
        pending_data.clear()

    def _add_dataset_links(
        self,
        subscan: SubScanInfo,
        channel: types.Channel,
        writer: Union[Hdf5DatasetWriter, Hdf5ScanReferenceWriter],
    ):
        if channel.data_type == types.ChannelDataType.SCAN_REFERENCE:
            return
        self._scan_writer._add_to_measurement_group(subscan, writer)
        if writer.device_type in ("positioner", "positionergroup"):
            self._scan_writer._add_to_positioners_group(subscan, writer)


class _PendingNumericData:
    def __init__(self, channel: types.Channel, data: typing.NumericDataType):
        self._buffer = list(data)
        self._channel = channel
        self._as_numpy = isinstance(data, numpy.ndarray)

    @property
    def channel(self) -> types.Channel:
        return self._channel

    def add_data(self, data: typing.NumericDataType) -> None:
        self._buffer.extend(data)

    def get_data(self) -> typing.NumericDataType:
        if self._as_numpy:
            detector_shapes = {arr.shape[1:] for arr in self._buffer}
            if len(detector_shapes) == 1:
                return numpy.array(self._buffer)
            # This is a ragged array (e.g. diode samples)
        return self._buffer


class _PendingLimaData:
    def __init__(self, channel: types.Channel, data: typing.LimaStatusType):
        self._data = data
        self._channel = channel

    @property
    def channel(self) -> types.Channel:
        return self._channel

    def add_data(self, data: typing.LimaStatusType) -> None:
        self._data = data

    def get_data(self) -> typing.LimaStatusType:
        return self._data


class _PendingScanReferenceData:
    def __init__(self, channel: types.Channel, data: typing.ScanReferenceType):
        self._data = data
        self._channel = channel

    @property
    def channel(self) -> types.Channel:
        return self._channel

    def add_data(self, data: typing.ScanReferenceType) -> None:
        self._data.extend(data)

    def get_data(self) -> typing.ScanReferenceType:
        return self._data


_CHANNEL_TYPE_TO_PENDING_CLASSES = {
    types.ChannelDataType.NUMERIC_DATA: _PendingNumericData,
    types.ChannelDataType.LIMA_STATUS: _PendingLimaData,
    types.ChannelDataType.SCAN_REFERENCE: _PendingScanReferenceData,
}
