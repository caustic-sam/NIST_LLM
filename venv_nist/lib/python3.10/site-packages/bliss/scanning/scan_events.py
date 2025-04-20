# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

from __future__ import annotations

import time
import numpy
import gevent
import gevent.event
import typing
import logging

from blissdata.beacon.data import BeaconData
from blissdata.streams.base import BaseView, CursorGroup
from blissdata.streams.lima import LimaStream
from blissdata.streams.lima2 import Lima2Stream
from blissdata.redis_engine.store import DataStore
from blissdata.redis_engine.scan import ScanState
from blissdata.redis_engine.exceptions import (
    NoScanAvailable,
    EndOfStream,
    ScanLoadError,
    ScanNotFoundError,
)

_logger = logging.getLogger(__name__)


class ScansObserver:
    """
    Observer for the `ScansWatcher`.

    Provides methods which can be inherited to follow the life cycle of the
    scans of a session.
    """

    def on_scan_created(self, scan_key: str, scan_info: dict):
        """
        Called upon scan created (devices are not yet prepared).

        Arguments:
            scan_key: Identifier of the scan
            scan_info: Dictionary containing scan metadata
        """
        pass

    def on_scan_started(self, scan_key: str, scan_info: dict):
        """
        Called upon scan started (the devices was prepared).

        Arguments:
            scan_key: Identifier of the scan
            scan_info: Dictionary containing scan metadata updated with metadata
                       prepared metadata from controllers
        """
        pass

    def on_scalar_data_received(
        self,
        scan_key: str,
        channel_name: str,
        index: int,
        data_bunch: typing.Union[list, numpy.ndarray],
    ):
        """
        Called upon a bunch of scalar data (0dim) from a `top_master` was
        received.

        Arguments:
            scan_key: Identifier of the parent scan
            channel_name: Name of the updated channel
            index: Start index of the data bunch in the real data stream.
                   There could be wholes between 2 bunches of data.
            data_bunch: The list of data received, as a bunch of data.
        """
        pass

    def on_ndim_data_received(
        self,
        scan_key: str,
        channel_name: str,
        dim: int,
        index: int,
        data_bunch: typing.Union[list, numpy.ndarray],
    ):
        """Called upon a ndim data (except 0dim, except data ref) data was
        received.

        - For 0dim data, see `on_scalar_data_received`.

        Arguments:
            scan_key: Identifier of the parent scan
            channel_name: Name of the channel emitting the data
            dim: Dimension of this data (MCA is 1, image is 2)
            index: Start index of the data bunch in the real data stream.
                   There could be wholes between 2 bunches of data.
            data_bunch: The list of data received, as a bunch of data.
        """
        pass

    def on_lima_event_received(self, scan_key: str, channel_name: str, view: BaseView):
        """Called upon a ndim (except 0dim) data was received.

        For 0dim data, see `on_scalar_data_received`.

        Arguments:
            scan_key: Identifier of the parent scan
            channel_name: Name of the channel emitting the data
            view: View to grab lima images (or just to know they're available)
        """
        pass

    def on_scan_finished(self, scan_key: str, scan_info: dict):
        """
        Called upon scan end.

        Arguments:
            scan_key: Identifier of the parent scan
            scan_info: Dictionary containing scan metadata updated with
                       prepared and finished metadata from controllers
                       Other fields like positioners and datetime are also
                       updated.
        """
        pass


class ScansWatcher:
    """
    Watch scans from a specific session.

    Arguments:
        session_name: Name of the BLISS session
    """

    def __init__(self, session_name: str, data_store: DataStore | None = None):
        self.ready_event = gevent.event.Event()

        self._session_name = session_name
        self._data_store = data_store
        self._watch_scan_group = False
        self._observer: ScansObserver | None = None
        self._running: bool = False
        self._blocked: bool = False
        self._greenlet: gevent.Greenlet | None = None
        self._scan_watchers: list[ScanWatcher] = []

        # When an event is received from Redis (new scan, state change, ...),
        # the greenlet in charge of this event may not call observer's callback
        # immediately. Ex: when receiving end of a scan, state greenlet needs
        # to join on the data receiving one. In that case, some other event
        # may be executed before. The following semaphore prevent the next
        # callback to be overtaken by someone else.
        # Because gevent semaphore is fair, multiple waiters are resumed in
        # FIFO order.
        self._pending_callback_lock = gevent.lock.BoundedSemaphore()

    def set_watch_scan_group(self, watch: bool):
        """
        Set to True to include scan groups like any other scans. Default is False.

        It have to be set before start.
        """
        assert not self._running
        self._watch_scan_group = watch

    def set_observer(self, observer: ScansObserver):
        """
        Set the observer to use with this watcher process.

        If not set, the `run` method will raise an exception.
        """
        assert not self._running
        self._observer = observer

    def stop(self, wait_running_scans=True):
        self._running = False
        if self._greenlet is not None:
            if self._blocked:
                self._greenlet.kill()
        for watcher in self._scan_watchers:
            if wait_running_scans:
                watcher.join()
            else:
                watcher.stop()
            self._scan_watchers = []
        if self._greenlet is not None:
            self._greenlet.join()

    def run(self):  # ignore_running_scans=True):
        """
        Run watching scan events.

        This method is blocking. But can be terminated by calling `stop`.

        Any scan node that is created before the `ready_event` will not be watched
        when `exclude_existing_scans` is True.
        """
        if self._running:
            raise RuntimeError("ScansWatcher is already running")

        if self._observer is None:
            raise RuntimeError("No observer was set")

        self._running = True
        self._greenlet = gevent.getcurrent()

        # TODO use data_store.search_existing_scans() to collect already running scans
        if self._data_store is None:
            redis_url = BeaconData().get_redis_data_db()
            self._data_store = DataStore(redis_url)
        since = self._data_store.get_last_scan_timetag()
        self.ready_event.set()

        while self._running:
            try:
                self._blocked = True
                since, scan_key = self._data_store.get_next_scan(since=since)
            except NoScanAvailable:
                continue
            finally:
                self._blocked = False

            with self._pending_callback_lock:
                try:
                    scan = self._data_store.load_scan(scan_key)
                except ScanNotFoundError:
                    # scan already deleted from Redis by user, skip it
                    continue
                except ScanLoadError:
                    _logger.warning("Cannot load scan %r", scan_key, exc_info=True)
                    continue
                # TODO need helper to check session_name without opening Scan or use filter args on get_next_scan
                if scan.session != self._session_name:
                    continue
                if not self._watch_scan_group and scan.info.get(
                    "is_scan_sequence", False
                ):
                    continue

                self._observer.on_scan_created(scan.key, scan.info)

            watcher = ScanWatcher(self._observer, scan, self._pending_callback_lock)
            watcher.start()
            self._scan_watchers.append(watcher)


class ScanWatcher:
    """Watcher of a single scan"""

    def __init__(self, observer, scan, pending_callback_lock=None):
        if pending_callback_lock is None:
            self._pending_callback_lock = gevent.lock.DummySemaphore()
        else:
            self._pending_callback_lock = pending_callback_lock
        self._observer = observer
        self._scan = scan

        self._running = False
        self._blocked_on_state = False
        self._blocked_on_data = False

        self._state_greenlet: gevent.Greenlet | None = None
        self._data_greenlet: gevent.Greenlet | None = None

    def start(self):
        assert not self._running
        self._running = True
        self._state_greenlet = gevent.spawn(self._listen_state)
        self._state_greenlet.link_exception(self._log_greenlet_exception)

    def _listen_state(self):
        # Assume scan was CREATED recently.
        # IMPORTANT: This won't be true anymore if we search for already running scans when starting.
        prev_state = ScanState.CREATED

        while self._running:
            if prev_state < ScanState.STARTED <= self._scan.state:
                with self._pending_callback_lock:
                    self._observer.on_scan_started(self._scan.key, self._scan.info)
                self._data_greenlet = gevent.spawn(self._listen_streams)
                self._data_greenlet.link_exception(self._log_greenlet_exception)

            if prev_state < ScanState.CLOSED <= self._scan.state:
                # make sure all data is read before advertising then end of the scan
                with self._pending_callback_lock:
                    if self._data_greenlet is not None:
                        self._data_greenlet.join()
                    self._observer.on_scan_finished(self._scan.key, self._scan.info)
                    break

            prev_state = self._scan.state
            try:
                self._blocked_on_state = True
                self._scan.update()
            finally:
                self._blocked_on_state = False

    def _listen_streams(self):
        array_streams = {
            stream for stream in self._scan.streams.values() if stream.kind == "array"
        }

        lima_streams = {
            stream
            for stream in array_streams
            if isinstance(stream, (LimaStream, Lima2Stream))
        }

        cursor_group = CursorGroup(array_streams)
        update_period = 0.1
        while self._running:
            try:
                self._blocked_on_data = True
                views = cursor_group.read()
            except EndOfStream:
                break
            finally:
                self._blocked_on_data = False

            for stream, view in views.items():
                # pick up lima streams before they fall into n-dim case
                if stream in lima_streams:
                    self._observer.on_lima_event_received(
                        self._scan.key,
                        stream.name,
                        view,
                    )
                    continue

                # streams are all "array", thus info contains dtype and shape
                ndim = len(stream.info["shape"])
                if ndim == 0:
                    self._observer.on_scalar_data_received(
                        self._scan.key,
                        stream.name,
                        view.index,
                        view.get_data(),
                    )
                else:
                    self._observer.on_ndim_data_received(
                        self._scan.key,
                        stream.name,
                        ndim,
                        view.index,
                        view.get_data(),
                    )

            # Limiting update rate.
            # IMPORTANT: This is not a polling loop. Calls to cursor_group.read() only return
            # when new data is available, but new data can be available very frequently.
            time.sleep(update_period)

    def stop(self):
        self._running = False
        if self._state_greenlet is not None:
            if self._blocked_on_state:
                self._state_greenlet.kill()
        if self._data_greenlet is not None:
            if self._blocked_on_data:
                self._data_greenlet.kill()
        self.join()

    def join(self):
        gevent.joinall(list(filter(None, (self._state_greenlet, self._data_greenlet))))

    def _log_greenlet_exception(self, greenlet):
        try:
            greenlet.get()
        except Exception:
            _logger.exception(f"ScanWatcher greenlet {greenlet} failed.")
