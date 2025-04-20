import enum
import time
import logging
import datetime
from contextlib import contextmanager
from typing import Optional, Dict, Union, Tuple, Generator

import numpy

from blissdata.redis_engine.scan import Scan
from blissdata.redis_engine.scan import ScanState
from blissdata.redis_engine.stream import Stream
from blissdata.redis_engine.stream import StreamingClient
from blissdata.redis_engine.exceptions import EndOfStream

from ..parameters import default_saveoptions
from ..writer import types
from ..utils.periodic import PeriodicTask
from ..writer.datasets import format_bytes
from ..writer.main import ScanWriterWithState
from ..writer.capture import capture_exceptions
from ..writer.typing import ChannelDataType
from .base_subscriber import BaseSubscriber


_logger = logging.getLogger(__name__)


class NexusScanSubscriber(BaseSubscriber):
    @enum.unique
    class _STATE(enum.IntEnum):
        """
        * INIT: initializing (not listening to events yet)
        * ON: listening to events
        * OFF: not listening to events and resources released
        * FAULT: not listening to events due to exception
        """

        INIT = enum.auto()
        ON = enum.auto()
        FAULT = enum.auto()
        OFF = enum.auto()

    _ALLOWED_STATE_TRANSITIONS = {
        _STATE.INIT: [_STATE.INIT, _STATE.ON, _STATE.OFF, _STATE.FAULT],
        _STATE.ON: [_STATE.OFF, _STATE.FAULT],
        _STATE.OFF: [_STATE.OFF, _STATE.FAULT],
        _STATE.FAULT: [],
    }

    def __init__(
        self,
        scan: Scan,
        parent_logger=None,
        start_semaphore_file: Optional[str] = None,
        **kw,
    ) -> None:
        if parent_logger is None:
            parent_logger = _logger
        kw = {**default_saveoptions(), **kw}
        resource_profiling = kw.pop("resource_profiling")
        super().__init__(
            name=scan.info["name"],
            parent_logger=parent_logger,
            resource_profiling=resource_profiling,
            start_semaphore_file=start_semaphore_file,
        )

        self._scan = scan
        self._writer = ScanWriterWithState(
            scan.key, self._name, parent_logger=self._logger, **kw
        )
        self._scan_writer = self._writer.scan_writer
        self._channels: Dict[str, types.Channel] = dict()
        self._yield_period: float = 1
        self._minimum_read_period: float = 0.1
        self._periodic_tasks = [
            PeriodicTask(self._log_progress, 5),
        ]

    @property
    def scan_writer(self):
        return self._scan_writer

    def _run(self) -> None:
        super()._run()
        with self._scan_saving_context() as saving:
            if not saving:
                return
            for output in self._iter_data():
                for stream, (first_index, data) in output.items():
                    channel = self._compile_channel(stream)
                    channel_fully_defined = self._writer.add_channel_data(channel, data)
                    if not channel_fully_defined:
                        self._channels.pop(stream.name)
                self._execute_periodic_tasks()

    @contextmanager
    def _scan_saving_context(self) -> Generator[bool, None, None]:
        """Returns `True` when saving is required"""
        self._wait_scan_state(ScanState.PREPARED)
        if not self._writer.initialize(self._scan.info, self._scan._data_store):
            self._logger.info("saving is disabled")
            yield False
            return

        failed = False
        try:
            yield True
        except BaseException:
            failed = True
            raise
        finally:
            with capture_exceptions() as capture:
                with capture():
                    self._wait_scan_state(ScanState.CLOSED)
                with capture():
                    self._writer.finalize(self._scan.info, failed)
                with capture():
                    self._log_progress(
                        f"Finished writing to '{self._scan_writer.scan_info.filename}'"
                    )

    def _iter_data(
        self,
    ) -> Generator[Dict[Stream, Tuple[int, ChannelDataType]], None, None]:
        """Yields stream data until end of scan or `stop` method called."""
        client = StreamingClient(self._scan.streams)
        while not self._stop_requested:
            try:
                output = client.read(timeout=self._yield_period)
            except EndOfStream:
                break

            t0 = time.time()
            yield output
            # Make sure we don't call `client.read` too fast as
            # this stresses the Redis server too much.
            dt = max(time.time() - t0 - self._minimum_read_period, 0)
            time.sleep(dt)

        if self._stop_requested:
            raise RuntimeError("Stop requested")

    def _wait_scan_state(self, state: ScanState) -> None:
        """Wait until the scan state is reached or a stop is requested, in which case `RuntimeError` is raised."""
        while not self._stop_requested and self._scan.state < state:
            self._scan.update(timeout=self._yield_period)
        if self._stop_requested:
            raise RuntimeError("Stop requested")

    def _execute_periodic_tasks(self):
        for task in self._periodic_tasks:
            task.execute()

    def _compile_channel(self, stream: Stream) -> types.Channel:
        channel = self._channels.get(stream.name)
        if channel is not None:
            return channel
        if stream.encoding["type"] == "numeric":
            channel_type = types.ChannelDataType.NUMERIC_DATA
            channel_info = {
                "shape": stream.encoding["shape"],
                "dtype": numpy.dtype(stream.encoding["dtype_str"]),
                **stream.info,
            }
        elif stream.encoding["type"] == "json" and "lima" in stream.info["format"]:
            channel_type = types.ChannelDataType.LIMA_STATUS
            channel_info = dict(stream.info)
        elif stream.encoding["type"] == "json" and "subscan" in stream.info["format"]:
            channel_type = types.ChannelDataType.SCAN_REFERENCE
            channel_info = dict(stream.info)
        else:
            raise NotImplementedError(
                f'Stream not supported: encoding={stream.encoding["type"]}, format={stream.info["format"]}'
            )
        shape = channel_info.get("shape", None)
        if shape is not None:
            channel_info["shape"] = tuple(shape)
        channel = types.Channel(
            name=stream.name, data_type=channel_type, info=channel_info
        )
        self._channels[stream.name] = channel
        return channel

    def done(self, seconds: float = 0) -> bool:
        """Returns `True` when the subscriber finished x seconds ago."""
        if self.is_alive():
            return False
        if self._end_time is None:
            return False
        timediff = datetime.datetime.now().astimezone() - self._end_time
        return timediff >= datetime.timedelta(seconds=seconds)

    def get_progress(self) -> Dict[str, str]:
        """Progress of each subscan"""
        pdict = dict()

        def getprogress(tpl):
            return tpl[1]

        for _, subscan in self._scan_writer.scan_info.iter_subscans():
            lst = list()
            for _, dproxy in list(subscan.dataset_items()):
                if dproxy is not None:
                    lst.append(dproxy.progress_string)
            if lst:
                pstr = f"{min(lst, key=getprogress)[0]}-{max(lst, key=getprogress)[0]}"
            else:
                pstr = "0pts-0pts"
            pdict[subscan.name] = pstr
        return pdict

    def get_progress_info(self) -> Dict[str, str]:
        """Progress info of each subscan"""
        data = format_bytes(self._get_current_bytes())
        state = self.state.name
        pdict = self.get_progress()

        start = self.start_time
        end = self.end_time
        init = self.init_time

        # Delay between scan start and writer instantiation
        startdelay1 = _timediff(init, self._scan_writer.scan_info.scan_start_time)

        # Delay between writer instantiation and start of writing
        startdelay2 = _timediff(start, init)

        # Delay between end of scan and end of writing (or now)
        if end is not None:
            enddelay = _timediff(end, self._scan_writer.scan_info.scan_end_time)
        else:
            enddelay = _timediff(self._now, self._scan_writer.scan_info.scan_end_time)

        # Time between start of writing and end of writing (or now)
        duration = self.get_duration()

        # Format output
        if start is not None:
            start = start.strftime("%Y-%m-%d %H:%M:%S")
        else:
            start = "not started"
        if end is not None:
            end = end.strftime("%Y-%m-%d %H:%M:%S")
        else:
            end = "not finished"
        template = f"{state},{{}},{data},duration: {duration},start: {start} (delay: {startdelay1} + {startdelay2}),end: {end} (delay: {enddelay})"
        if pdict:
            return {name: template.format(s) for name, s in pdict.items()}
        return {"": template.format("0pts-0pts")}

    def _get_overall_progress(self) -> str:
        """Progress of the scan"""
        pdict = self.get_progress()
        if not pdict:
            return "0pts-0pts"
        if len(pdict) == 1:
            return next(iter(pdict.values()))
        plist = [f"{name}:{s}" for name, s in pdict.items()]
        return " ".join(plist)

    def _log_progress(self, msg=None):
        data = format_bytes(self._get_current_bytes())
        progress = self._get_overall_progress()
        duration = self.get_duration()
        if msg:
            self._logger.info("%s (%s %s %s)", msg, progress, data, duration)
        else:
            self._logger.info(" %s %s %s", progress, data, duration)

    def _get_current_bytes(self) -> int:
        """Total bytes (data-only) of all subscans"""
        nbytes = 0
        for _, subscan in self._scan_writer.scan_info.iter_subscans():
            for _, dproxy in list(subscan.dataset_items()):
                if dproxy is not None:
                    nbytes += dproxy.current_bytes
        return nbytes


def _timediff(
    tend: Union[str, datetime.datetime, None],
    tstart: Union[str, datetime.datetime, None],
) -> str:
    if isinstance(tend, str):
        tend = datetime.datetime.fromisoformat(tend)
    if isinstance(tstart, str):
        tstart = datetime.datetime.fromisoformat(tstart)
    if tend is None or tstart is None:
        return "NaN"
    if tend >= tstart:
        return str(tend - tstart)
    return f"-{tstart-tend}"
