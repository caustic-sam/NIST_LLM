import enum
import logging
import datetime
from collections import OrderedDict
from contextlib import contextmanager
from typing import Generator, Dict, Optional, Tuple, List, Any

from blissdata.beacon.data import BeaconData
from blissdata.redis_engine.store import DataStore
from blissdata.redis_engine.scan import Scan as RedisScan
from blissdata.redis_engine.exceptions import NoScanAvailable

from ..parameters.base import PROFILE_PARAMETERS
from .base_subscriber import BaseSubscriber
from .scan_subscriber import NexusScanSubscriber
from ..utils.periodic import PeriodicTask
from ..utils import process_utils


_logger = logging.getLogger(__name__)


class NexusSessionSubscriber(BaseSubscriber):
    @enum.unique
    class _STATE(enum.IntEnum):
        """
        * INIT: initializing (not accepting scans yet)
        * ON: accepting scans (without active scan subscribers)
        * RUNNING: accepting scans (with active scan subscribers)
        * OFF: not accepting scans
        * FAULT: not accepting scans due to exception
        """

        INIT = enum.auto()
        ON = enum.auto()
        RUNNING = enum.auto()
        FAULT = enum.auto()
        OFF = enum.auto()

    _ALLOWED_STATE_TRANSITIONS = {
        _STATE.INIT: [_STATE.INIT, _STATE.ON, _STATE.RUNNING, _STATE.OFF, _STATE.FAULT],
        _STATE.ON: [_STATE.RUNNING, _STATE.OFF, _STATE.FAULT],
        _STATE.RUNNING: [_STATE.ON, _STATE.OFF, _STATE.FAULT],
        _STATE.OFF: [_STATE.OFF, _STATE.FAULT],
        _STATE.FAULT: [],
    }

    def __init__(
        self,
        session_name: str,
        parent_logger=None,
        purge_delay: float = 300,
        resource_profiling: Optional[PROFILE_PARAMETERS] = None,
        start_semaphore_file: Optional[str] = None,
        **save_options,
    ) -> None:
        self._save_options = save_options
        self._purge_delay: float = purge_delay
        self._minimal_purge_delay: float = 5
        self._scan_subscribers: Dict[str, NexusScanSubscriber] = dict()
        self._periodic_tasks = [
            PeriodicTask(self.purge_scan_writers, 0),
            PeriodicTask(self._log_resources, 10),
        ]
        self._yield_period: float = 1

        if parent_logger is None:
            parent_logger = _logger
        super().__init__(
            session_name,
            parent_logger=parent_logger,
            resource_profiling=resource_profiling,
            start_semaphore_file=start_semaphore_file,
        )

    @property
    def _state(self):
        if self.__state == self._STATE.ON and any(
            scan_subscriber.is_alive()
            for scan_subscriber in list(self._scan_subscribers.values())
        ):
            return self._STATE.RUNNING
        return self.__state

    @_state.setter
    def _state(self, value):
        self.__state = value

    def stop(self, block: bool = True, timeout: Optional[float] = None) -> None:
        if self.state == self._STATE.RUNNING:
            raise RuntimeError("Cannot stop session subscriber when scans are running")
        super().stop(block=block, timeout=timeout)

    def _run(self):
        redis_url = BeaconData().get_redis_data_db()
        self._data_store = DataStore(redis_url)
        since = self._data_store.get_last_scan_timetag()
        super()._run()
        for scan in self._iter_scans(since):
            scan_subscriber = NexusScanSubscriber(
                scan, parent_logger=self._logger, **self._save_options
            )
            scan_subscriber.start(block=False)
            self._scan_subscribers[scan_subscriber.name] = scan_subscriber

    def _iter_scans(self, since: Optional[str]) -> Generator[RedisScan, None, None]:
        """Iterator stops only when the `stop` method is called."""
        while not self._stop_requested:
            try:
                since, key = self._data_store.get_next_scan(
                    since=since, timeout=self._yield_period
                )
                scan = self._data_store.load_scan(key, scan_cls=RedisScan)
                if scan.session == self.name:
                    yield scan
            except NoScanAvailable:
                pass
            self._execute_periodic_tasks()

    def _execute_periodic_tasks(self):
        for task in self._periodic_tasks:
            task.execute()

    def purge_scan_writers(self, delay=True):
        if delay:
            delay = max(self._minimal_purge_delay, self._purge_delay)
        else:
            delay = self._minimal_purge_delay
        scan_subscribers = dict()
        for name, scan_subscriber in list(self._scan_subscribers.items()):
            if scan_subscriber.done(delay):
                self._logger.info("Purge scan subscriber %s", scan_subscriber)
            else:
                scan_subscribers[name] = scan_subscriber
        self._scan_subscribers = scan_subscribers

    def update_saveoptions(self, **kwargs):
        self._save_options.update(kwargs)

    @property
    def resource_profiling(self) -> PROFILE_PARAMETERS:
        return self._save_options["resource_profiling"]

    @resource_profiling.setter
    def resource_profiling(self, value):
        if value is None:
            self._save_options["resource_profiling"] = PROFILE_PARAMETERS.OFF
        else:
            self._save_options["resource_profiling"] = PROFILE_PARAMETERS(value)
        self._log_resources()

    def _log_resources(self):
        if self.resource_profiling == PROFILE_PARAMETERS.OFF:
            return
        n = len(self._scan_subscribers)
        nactive = sum(w.is_alive() for w in list(self._scan_subscribers.values()))
        self._logger.info("%d scan subscribers (%d active)", n, nactive)
        self._logger.info("%s", _GLOBAL_RESOURCES)

    def get_resources(self) -> str:
        return str(_GLOBAL_RESOURCES)

    @contextmanager
    def _possible_profiling(self) -> Generator[None, None, None]:
        yield

    def _get_scan_subscriber(
        self, name: str, raise_non_existing: bool = True
    ) -> Optional[NexusScanSubscriber]:
        scan_subscriber = self._scan_subscribers.get(name, None)
        if scan_subscriber is None:
            if raise_non_existing:
                raise ValueError(f"No subscriber for scan {repr(name)} exists")
        return scan_subscriber

    def _sorted_subscriber_items(self) -> List[Tuple[str, NexusScanSubscriber]]:
        return sorted(list(self._scan_subscribers.items()), key=lambda item: item[1])

    def _scan_properties(self, getter, name=None) -> Dict[str, Any]:
        ret = OrderedDict()
        if name:
            subscriber = self._get_scan_subscriber(name)
            ret[name] = getter(subscriber)
        else:
            for name, subscriber in self._sorted_subscriber_items():
                ret[name] = getter(subscriber)
        return ret

    def scan_exists(self, name: str) -> bool:
        return name in self._scan_subscribers

    def scan_names(self) -> List[str]:
        return [name for name, _ in self._sorted_subscriber_items()]

    def stop_scan_writer(self, name: str) -> None:
        subscriber = self._get_scan_subscriber(name, raise_non_existing=False)
        if subscriber is not None:
            subscriber.stop()

    def scan_has_write_permissions(self, name: str) -> bool:
        subscriber = self._get_scan_subscriber(name)
        return subscriber.scan_writer.has_write_permissions()

    def scan_has_required_disk_space(self, name: str) -> bool:
        return not self.scan_disk_space_error(name)

    def scan_disk_space_error(self, name: str) -> str:
        subscriber = self._get_scan_subscriber(name)
        return subscriber.scan_writer.disk_space_error()

    def scan_disk_space_warning(self, name: str) -> str:
        subscriber = self._get_scan_subscriber(name)
        return subscriber.scan_writer.disk_space_warning()

    def scan_state(
        self, name: Optional[str] = None
    ) -> Dict[str, NexusScanSubscriber._STATE]:
        return self._scan_properties(lambda s: s.state, name=name)

    def scan_state_info(
        self, name: Optional[str] = None
    ) -> Dict[str, Tuple[NexusScanSubscriber._STATE, Optional[str]]]:
        return self._scan_properties(lambda s: (s.state, s.state_reason), name=name)

    def scan_urls(self, name: Optional[str] = None) -> Dict[str, List[str]]:
        return self._scan_properties(
            lambda s: s.scan_writer.scan_info.get_urls(), name=name
        )

    def scan_start(
        self, name: Optional[str] = None
    ) -> Dict[str, List[Optional[datetime.datetime]]]:
        return self._scan_properties(lambda s: s.start_time, name=name)

    def scan_end(
        self, name: Optional[str] = None
    ) -> Dict[str, List[Optional[datetime.datetime]]]:
        return self._scan_properties(lambda s: s.end_time, name=name)

    def scan_duration(
        self, name: Optional[str] = None
    ) -> Dict[str, List[datetime.timedelta]]:
        return self._scan_properties(lambda s: s.get_duration(), name=name)

    def scan_progress(self, name: Optional[str] = None) -> Dict[str, Dict[str, str]]:
        return self._scan_properties(lambda s: s.get_progress(), name=name)

    def scan_progress_info(
        self, name: Optional[str] = None
    ) -> Dict[str, Dict[str, str]]:
        return self._scan_properties(lambda s: s.get_progress_info(), name=name)


class _GlobalResources:
    def __str__(self) -> str:
        nfds = len(process_utils.file_descriptors())
        nsockets = len(process_utils.sockets())
        ngreenlets = len(process_utils.greenlets())
        nthreads = len(process_utils.threads())
        mb = int(process_utils.memory() / 1024**2)
        return f"{nthreads} threads, {ngreenlets} greenlets, {nsockets} sockets, {nfds} fds, {mb}MB MEM"


_GLOBAL_RESOURCES = _GlobalResources()
