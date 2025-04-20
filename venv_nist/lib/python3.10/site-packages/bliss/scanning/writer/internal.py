import logging
import gevent
from functools import wraps
from typing import Optional
from collections.abc import Iterator

from querypool.pools import NonCooperativeQueryPool
from blisswriter.writer.main import ScanWriterWithState
from blisswriter.writer import types
from blisswriter.parameters import default_saveoptions
from blisswriter.utils import logging_utils

from bliss.scanning.channel import (
    AcquisitionChannel,
    LimaAcquisitionChannel,
    Lima2AcquisitionChannel,
    SubscanAcquisitionChannel,
)

from bliss.common.data_store import get_default_data_store
from .nexus import NexusWriter
from .base import _ScanEventInfo
from .base import _ScanSignal
from .base import _ScanEventSender


logger = logging.getLogger(__name__)


def _mark_failed_on_exception(method):
    @wraps(method)
    def wrapper(self, *args, **kw):
        try:
            return method(self, *args, **kw)
        except (gevent.GreenletExit, KeyboardInterrupt):
            # Scan was interrupted
            raise
        except BaseException:
            self._writing_failed = True
            raise

    return wrapper


class InternalNexusWriter(NexusWriter, name="hdf5"):
    """NeXus writer runs in this process. Scan events directly causes the writing."""

    _WRITER_PARAMETERS = {"configurable": True}

    @classmethod
    def update_parameters(cls, *args, **kw) -> None:
        cls._WRITER_PARAMETERS.update(*args, **kw)

    @classmethod
    def clear_parameters(cls) -> None:
        cls._WRITER_PARAMETERS.clear()
        cls._WRITER_PARAMETERS["configurable"] = True

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._scan_writer = None
        self._master_event_callback = self._on_event
        self._slave_event_callback = self._on_event
        self._writing_failed = False
        self._channels: dict[str, types.Channel] = dict()
        self._query_pool = NonCooperativeQueryPool(timeout=0.1)

    def get_last_scan_number(self) -> int:
        return super().get_last_scan_number(mode="a")

    @property
    def configurable(self) -> bool:
        return bool(self._WRITER_PARAMETERS.get("configurable"))

    @_mark_failed_on_exception
    def _prepare_scan(self, scan) -> None:
        options = {
            **default_saveoptions(configurable=self.configurable),
            **self._WRITER_PARAMETERS,
        }
        options.pop("resource_profiling")
        parent_logger = logging_utils.CustomLogger(logger, scan.identifier)
        scan_writer = ScanWriterWithState(
            scan._scan_data.key,
            scan.scan_info["name"],
            parent_logger=parent_logger,
            query_pool=self._query_pool,
            **options,
        )
        if not scan_writer.initialize(scan.scan_info, get_default_data_store()):
            return
        self._scan_writer = scan_writer

    @_mark_failed_on_exception
    def finalize(self, scan) -> None:
        """Called at the end of the scan"""
        if self._scan_writer is None:
            return
        self._scan_writer.finalize(scan.scan_info, self._writing_failed)

    @_mark_failed_on_exception
    def _on_event(
        self,
        data: _ScanEventInfo,
        signal: Optional[_ScanSignal] = None,
        sender: Optional[_ScanEventSender] = None,
    ) -> None:
        if self._scan_writer is None:
            return
        if signal == "start":
            channels: Iterator[AcquisitionChannel] = sender.channels
            for acq_channel in channels:
                channel = self._compile_channel(acq_channel, cache=False)
                self._scan_writer.add_channel(channel)
        elif signal == "new_data":
            if data is None:
                return
            channel = self._compile_channel(sender)
            self._scan_writer.add_channel_data(channel, data)
        else:
            raise ValueError(f"{type(self)}: unkown event '{signal}'")

    def _compile_channel(
        self, acq_channel: AcquisitionChannel, cache: bool = True
    ) -> types.Channel:
        if cache:
            channel = self._channels.get(acq_channel.fullname)
            if channel is not None:
                return channel
        if isinstance(acq_channel, SubscanAcquisitionChannel):
            data_type = types.ChannelDataType.SCAN_REFERENCE
        elif isinstance(acq_channel, (LimaAcquisitionChannel, Lima2AcquisitionChannel)):
            data_type = types.ChannelDataType.LIMA_STATUS
        else:
            data_type = types.ChannelDataType.NUMERIC_DATA
        channel_info = acq_channel.stream.info
        channel = types.Channel(
            name=acq_channel.fullname, data_type=data_type, info=channel_info
        )
        if cache:
            self._channels[acq_channel.fullname] = channel
        return channel
