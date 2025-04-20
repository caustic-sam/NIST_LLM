# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

from __future__ import annotations
from collections.abc import Callable

from gevent.queue import Queue
import gevent
import gevent.event
from bliss.scanning.chain import (
    AcquisitionMaster,
    AcquisitionSlave,
    AcquisitionChain,
)
from bliss.scanning.channel import AcquisitionChannel, SubscanAcquisitionChannel
from bliss.scanning.scan_state import ScanState
from bliss.scanning.scan import Scan
from bliss.scanning.scan_info import ScanInfo
import logging

_logger = logging.getLogger(__name__)


class ScanSequenceError(RuntimeError):
    """Exception related to a ScanSequence."""

    pass


class _WrapUserException(RuntimeError):
    """Exception to capture and restore user exection.

    Without that BLISS monkey patch the arguments of the exceptions in
    the AcquisitionObjectIterator.
    """

    @property
    def wrapped_exception(self) -> BaseException:
        # The exception have to be the 1st argument
        # Then it is monkey patched by AcquisitionObjectIterator and became the second arguemnt
        return self.args[-1]


class ScanSequence(Scan):
    """A scan grouping together a set of scans.

    It is based on a dedicated user function which handle
    the creation and run of the sub scans.

    Extra channels can be defined and emitted during this
    execution.

    .. code-block::

        def runner(context: SequenceContext):
            context.emit("my_channel", [1])
            s = loopscan(10, 0.1, run=False)
            context.add_and_run(s)
            context.emit("my_channel", [2, 3])

        ch = AcquisitionChannel("my_channel", numpy.float32, ())
        scan = ScanSequence(runner=runner, channels=[ch])
        scan.run()
    """

    def __init__(
        self,
        runner: Callable[[SequenceContext], None],
        name: str = "sequence",
        title: str = "sequence_of_scans",
        save: bool = True,
        scan_info: dict | None = None,
        channels: list[AcquisitionChannel] | None = None,
    ):
        self._scans: list[Scan] = []
        self.__channels: dict[str, AcquisitionChannel] = {}

        scan_info = ScanInfo.normalize(scan_info)
        self._title = title
        if title is not None:
            scan_info["title"] = self._title
        scan_info["is_scan_sequence"] = True
        scan_info.set_channel_meta("SUBSCANS", group="subscans")

        self.group_acq_master = GroupingMaster(self, runner)

        self.group_custom_slave = GroupingSlave("channels")
        if channels is not None:
            for channel in channels:
                if not isinstance(channel, AcquisitionChannel):
                    raise ScanSequenceError(
                        f"An AcquisitionChannel is mandatory. Found {channel}"
                    )
                if channel.name == "SUBSCANS":
                    raise ValueError(
                        "The name 'SUBSCANS' is reserved, can't add custom channel"
                    )
                if channel.name in self.__channels:
                    raise ScanSequenceError(f"{channel.name} is already a channel name")
                self.group_custom_slave.channels.append(channel)
                self.__channels[channel.name] = channel

        chain = AcquisitionChain()
        chain.add(self.group_acq_master)
        chain.add(self.group_acq_master, self.group_custom_slave)

        super().__init__(
            chain=chain,
            name=name,
            save=save,
            scan_info=scan_info,
        )

    @property
    def scans(self) -> list[Scan]:
        """Returns the list of known child scans, in registration order.

        During the sequence this list is updated, and contains potentially
        running scans.
        """
        return list(self._scans)

    def run(self):
        try:
            super().run()
        except _WrapUserException as e:
            raise e.wrapped_exception from None

    def get_custom_channel(self, channel_name: str) -> AcquisitionChannel | None:
        return self.__channels.get(channel_name)

    def register_child_scan(self, scan: Scan):
        self._scans.append(scan)

    def is_flint_recommended(self):
        """Return true if flint is recommended for this scan

        A scan group is usually not displayed, except there is an explicit plot
        """
        scan_info = self._scan_info
        plots = scan_info.get("plots", [])
        return len(plots) >= 1


class SequenceContext:
    def __init__(self, scan_sequence: ScanSequence):
        self._scan_sequence: ScanSequence = scan_sequence
        self._waiting_scans: list[gevent.Greenlet] = []

    def _wait_before_adding_scan(self, scan):
        scan.wait_state(ScanState.STARTING)
        self._scan_sequence.group_acq_master.new_subscan(scan)

    def add(self, scan: Scan):
        """Add a scan into the group.

        If the scan was not started, this method also flag the scan
        `scan_info` with the group `scan_key`.

        Argument:
            scan: A scan
        """
        assert isinstance(scan, Scan)
        self._scan_sequence.register_child_scan(scan)

        if scan.state != ScanState.IDLE:
            raise ScanSequenceError(
                f'Error in  add_and_run: scan "{scan._scan_data.key}" has already been started before!'
            )

        scan.scan_info["group"] = self._scan_sequence._scan_data.key
        self._waiting_scans.append(gevent.spawn(self._wait_before_adding_scan, scan))

    def add_and_run(self, scan: Scan):
        """Add a scan into the group, run it, and wait for termination.

        This method also flag the scan `scan_info` with
        the group `scan_key`.

        Argument:
            scan: A scan

        Raise:
            ScanSequenceError: If the scan was already started.
        """
        self.add(scan)
        scan._add_to_scans_queue = False
        scan.run()

    def emit(self, channel_name: str, values):
        channel = self._scan_sequence.get_custom_channel(channel_name)
        if channel is None:
            raise IndexError(f"Channel name '{channel_name}' not found")
        channel.emit(values)

    def wait_all_subscans(self, timeout=None):
        """
        Wait for all subscans to properly terminate.

        Raises:
            TimeoutError: If some subscans was not properly terminated
        """
        result = gevent.joinall(self._waiting_scans, timeout=timeout)
        if len(result) != len(self._waiting_scans):
            nb = len(self._waiting_scans) - len(result)
            raise TimeoutError(
                f"Timeout of {timeout} expired while remaining {nb} for subscans"
            )

    def close(self) -> bool:
        """Close the running scans and return True if everything was properly done"""
        try:
            self.wait_all_subscans(timeout=0.1)
        except TimeoutError:
            gevent.killall(self._waiting_scans)
            return False
        except BaseException:
            gevent.killall(self._waiting_scans, block=False)
            raise
        return True


class GroupingMaster(AcquisitionMaster):
    """Master of the scan sequence.

    It is responsible of running the runner of the sequence.
    """

    def __init__(
        self, scan_sequence: ScanSequence, runner: Callable[[SequenceContext], None]
    ):
        super().__init__(
            None, name="GroupingMaster", npoints=0, prepare_once=True, start_once=True
        )
        self._scan_sequence: ScanSequence = scan_sequence
        self._task: gevent.Greenlet | None = None
        self._runner: Callable[[SequenceContext], None] = runner
        self.scan_queue = Queue()

        self._subscan_channel = SubscanAcquisitionChannel(name="SUBSCANS")
        self.channels.append(self._subscan_channel)

        # Synchronize GroupingMaster iteration and wait_all_published
        self._publishing = False
        self._publish_success = True
        self._publish_event = gevent.event.Event()
        self._publish_event.set()

    def prepare(self):
        pass

    def start(self):
        if self._task is None:
            # Only executed at the first iteration
            self._task = gevent.spawn(self._run_sequence)

    def stop(self):
        if self._task is not None:
            self._task.kill()
            self._task = None

    def __iter__(self):
        self._publishing = True
        try:
            yield self
            for scan in self.scan_queue:
                self._publish_new_subscan(scan)
                yield self
        finally:
            self._publishing = False

        try:
            if self._task is not None:
                self._task.get()
        except Exception as e:
            raise _WrapUserException(e) from None
        finally:
            self._task = None

    def _run_sequence(self):
        """Called by the master to execute the user sequence"""
        scan_sequence = self._scan_sequence
        scan_sequence.wait_state(ScanState.STARTING)

        if scan_sequence.state >= ScanState.USER_ABORTED:
            raise ScanSequenceError("Failed to prepare scan sequence")

        if scan_sequence.group_custom_slave is not None:
            scan_sequence.group_custom_slave.start_event.wait()

        context = SequenceContext(scan_sequence)
        try:
            self._runner(context)
        finally:
            # Stop the iteration over group_acq_master
            self.scan_queue.put(StopIteration)

            # The subscans should have finished before exiting the context
            scans_finished = context.close()

        # Wait until all sequence events are published in Redis
        # Note: publishing is done by iterating over group_acq_master
        events_published = True
        if len(scan_sequence._scans) > 0:
            try:
                # Timeout not specified because we have no way of
                # estimating how long it will take.
                events_published = self.wait_all_published()
            except ScanSequenceError:
                events_published = False

        # Raise exception when incomplete
        if not scans_finished:
            raise ScanSequenceError(
                f'Some scans of the sequence "{scan_sequence._title}" have not finished before exiting the sequence context'
            )
        elif not events_published:
            raise ScanSequenceError(
                f'Some events of the sequence "{scan_sequence._title}" were not published in Redis'
            )

    def _publish_new_subscan(self, scan):
        """Publish group scan events in Redis related to one scan"""
        self._publish_event.clear()
        scan.wait_state(ScanState.PREPARING)

        if scan.state in [ScanState.KILLED, ScanState.USER_ABORTED]:
            # Make sure scan._scan_data was initialized
            # Else it means the scan was aborted before
            if scan._scan_data is None:
                # Something was not yet initialized
                self._publish_success &= True
                self._publish_event.set()
                return
        try:
            # Emit sequence events
            self._subscan_channel.emit(
                {
                    "key": scan._scan_data.key,
                    "scan_number": int(scan._scan_data.info["scan_nb"]),
                }
            )

            # self._reset_expiration_time(scan)

        except BaseException:
            self._publish_success &= False
            raise
        else:
            self._publish_success &= True
        finally:
            self._publish_event.set()

    def wait_all_published(self, timeout=None):
        """Wait until `_publish_new_subscan` is called for all subscans
        that are queued. Publishing is done by iterating over this
        `GroupingMaster`.

        Raises ScanSequenceError upon timeout or when there are scans
        in the queue while nobody is iterating to publish their
        associated sequence events.
        """
        with gevent.Timeout(timeout, ScanSequenceError):
            success = True
            while self.scan_queue.qsize() > 0 and self._publishing:
                self._publish_event.wait()
                success &= self._publish_success
                gevent.sleep()
            if self.scan_queue.qsize() > 0:
                raise ScanSequenceError
            self._publish_event.wait()
            success &= self._publish_success
            return success

    def new_subscan(self, scan):
        self.scan_queue.put(scan)


class GroupingSlave(AcquisitionSlave):
    """Slave exposing the channels of the sequence"""

    def __init__(self, name):
        super().__init__(None, name=name)
        self.start_event = gevent.event.Event()

    def prepare(self):
        pass

    def start(self):
        self.start_event.set()

    def trigger(self):
        pass

    def stop(self):
        pass
