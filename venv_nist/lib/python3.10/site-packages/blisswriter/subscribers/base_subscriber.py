import os
import enum
import logging
import datetime
import threading
from typing import Optional, Generator
from contextlib import contextmanager

from ..parameters.base import PROFILE_PARAMETERS
from ..utils import profiling
from ..utils import logging_utils

_logger = logging.getLogger(__name__)


class BaseSubscriber:
    """Provides state, logging, timing, sorting and concurrency model"""

    @enum.unique
    class _STATE(enum.IntEnum):
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
        name: str,
        parent_logger=None,
        resource_profiling: Optional[PROFILE_PARAMETERS] = None,
        start_semaphore_file: Optional[str] = None,
    ) -> None:
        self._name = name
        self._state = self._STATE.INIT
        self._state_reason = "instantiation"
        self._init_time = datetime.datetime.now().astimezone()

        if parent_logger is None:
            parent_logger = _logger
        self._logger = logging_utils.CustomLogger(parent_logger, self)

        if resource_profiling is None:
            resource_profiling = PROFILE_PARAMETERS.OFF
        self.resource_profiling = resource_profiling

        self._thread: Optional[threading.Thread] = None
        self._started_event = threading.Event()
        self._start_time = None
        self._start_semaphore_file = start_semaphore_file
        self._stop_requested: bool = False
        self._end_time = None

    def __str__(self) -> str:
        return f"{self.name} ({self.state.name})"

    @property
    def name(self):
        return self._name

    @property
    def state(self) -> enum.IntEnum:
        return self._state

    @property
    def state_reason(self) -> str:
        return self._state_reason

    def _set_state(self, state: enum.IntEnum, reason: str, force: bool = False) -> None:
        if force or state in self._ALLOWED_STATE_TRANSITIONS[self._state]:
            self._state = self._STATE(state)
            self._state_reason = reason
            if state == self._STATE.FAULT:
                self._logger.error(reason)
            else:
                self._logger.info(reason)

    def _clear_state(self) -> None:
        self._stop_requested = False
        self._started_event.clear()
        self._start_time = None
        self._end_time = None
        self._set_state(self._STATE.INIT, "Starting", force=True)

    def start(self, block: bool = True, timeout: Optional[float] = None) -> None:
        if self.is_alive():
            return
        self._thread = threading.Thread(target=self._main)
        self._clear_state()
        self._thread.start()
        if block:
            self._started_event.wait(timeout=timeout)

    def stop(self, block: bool = True, timeout: Optional[float] = None) -> None:
        self._stop_requested = True
        if block:
            self.join(timeout=timeout)

    def join(self, timeout: Optional[float] = None) -> None:
        if self._thread:
            self._thread.join(timeout=timeout)

    def is_alive(self) -> bool:
        return self._thread and self._thread.is_alive()

    def _main(self) -> None:
        try:
            self._on_started()
            with self._possible_profiling():
                self._run()
        except KeyboardInterrupt:
            self._set_state(self._STATE.FAULT, "KeyboardInterrupt")
            self._logger.warning("Stop listening to Redis events (KeyboardInterrupt)")
            raise
        except BaseException as e:
            self._set_state(self._STATE.FAULT, str(e))
            self._logger.exception("Stop listening to Redis events due to an exception")
            raise
        finally:
            self.on_finished()

    def _on_started(self) -> None:
        self._started_event.set()
        self._start_time = self._now
        if self._start_semaphore_file:
            dirname = os.path.dirname(self._start_semaphore_file)
            if dirname:
                os.makedirs(dirname, exist_ok=True)
            with open(self._start_semaphore_file, "a"):
                pass

    def on_finished(self) -> None:
        try:
            if self._start_semaphore_file:
                os.unlink(self._start_semaphore_file)
        finally:
            self._end_time = self._now
            self._set_state(self._STATE.OFF, "Finished succesfully")

    def _run(self) -> None:
        self._set_state(self._STATE.ON, "Subscribed to Redis")

    @property
    def resource_profiling(self) -> PROFILE_PARAMETERS:
        return self._resource_profiling

    @resource_profiling.setter
    def resource_profiling(self, value: Optional[PROFILE_PARAMETERS]):
        if value is None:
            self._resource_profiling = PROFILE_PARAMETERS.OFF
        else:
            self._resource_profiling = PROFILE_PARAMETERS(value)

    @contextmanager
    def _possible_profiling(self) -> Generator[None, None, None]:
        kwargs = self.resource_profiling.arguments
        if kwargs:
            with profiling.profile(**kwargs):
                yield
        else:
            yield

    @property
    def init_time(self) -> Optional[datetime.datetime]:
        """Time of instatiation"""
        return self._init_time

    @property
    def start_time(self) -> Optional[datetime.datetime]:
        """Start of writing"""
        return self._start_time

    @property
    def end_time(self) -> Optional[datetime.datetime]:
        """End of writing"""
        return self._end_time

    @property
    def _now(self) -> datetime.datetime:
        return datetime.datetime.now().astimezone()

    def get_duration(self) -> datetime.timedelta:
        """Time between start and end of writing"""
        t0 = self._start_time
        t1 = self._end_time
        if t0 is None:
            t0 = self._init_time
        if t1 is None:
            t1 = self._now
        if t1 < t0:
            return t0 - t0
        else:
            return t1 - t0

    def _sort_key(self):
        if self._start_time is None:
            return self._init_time
        else:
            return self._start_time

    def __lt__(self, other):
        if isinstance(other, type(self)):
            return self._sort_key() < other._sort_key()
