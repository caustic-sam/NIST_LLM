# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

from gevent.queue import Queue
import gevent
import gevent.event
from contextlib import contextmanager
from bliss.scanning.chain import (
    AcquisitionMaster,
    AcquisitionSlave,
    AcquisitionChain,
)
from bliss.scanning.channel import AcquisitionChannel, SubscanAcquisitionChannel
from bliss.scanning.scan_state import ScanState
from bliss.scanning.scan import Scan, ScanPreset, ScanAbort
from bliss.scanning.scan_info import ScanInfo


class ScanGroup(Scan):
    def is_flint_recommended(self):
        """Return true if flint is recommended for this scan

        A scan group is usually not displayed, except there is an explicit plot
        """
        scan_info = self._scan_info
        plots = scan_info.get("plots", [])
        return len(plots) >= 1


class ScanSequenceError(RuntimeError):
    pass


class SequenceContext:
    def __init__(self, sequence):
        self.sequence = sequence

    def _wait_before_adding_scan(self, scan):
        scan.wait_state(ScanState.STARTING)
        self.sequence.group_acq_master.new_subscan(scan)

    def add(self, scan: Scan):
        """Add a scan into the group.

        If the scan was not started, this method also flag the scan
        `scan_info` with the group `scan_key`.

        Argument:
            scan: A scan
        """
        assert isinstance(scan, Scan)
        self.sequence._scans.append(scan)

        if scan.state >= ScanState.STARTING:
            # scan is running / has been running already
            self.sequence.group_acq_master.new_subscan(scan)
        else:
            scan.scan_info["group"] = self.sequence._scan._scan_data.key
            self.sequence._waiting_scans.append(
                gevent.spawn(self._wait_before_adding_scan, scan)
            )

    def add_and_run(self, scan: Scan):
        """Add a scan into the group, run it, and wait for
        termination.

        This method also flag the scan `scan_info` with
        the group `scan_key`.

        Argument:
            scan: A scan

        Raise:
            ScanSequenceError: If the scan was already started.
        """
        assert isinstance(scan, Scan)
        if scan.state != ScanState.IDLE:
            raise ScanSequenceError(
                f'Error in  add_and_run: scan "{scan._scan_data.key}" has already been started before!'
            )
        scan.scan_info["group"] = self.sequence._scan._scan_data.key

        self.add(scan)
        scan.run()

    def wait_all_subscans(self, timeout=None):
        self.sequence.wait_all_subscans(timeout=timeout)


class StatePreset(ScanPreset):
    def __init__(self, sequence):
        super().__init__()
        self._sequence = sequence

    def stop(self, scan):
        max_state = ScanState.DONE
        for s in self._sequence._scans:
            if s.state > max_state:
                max_state = s.state
        if max_state == ScanState.KILLED:
            raise RuntimeError("At least one of the scans in the sequence was KILLED")
        elif max_state == ScanState.USER_ABORTED:
            raise ScanAbort(
                "At least one of the scans in the sequence was USER_ABORTED"
            )


class Sequence:
    """
    Should have a scan as internal property that runs
    in a spawned mode in the background. Each new scan
    should publish itself (trigger a master inside the scan)

    There should be a possibility of calc channels.

    TODO: How to handle progress bar for sequence?
    """

    def __init__(self, scan_info=None, title="sequence_of_scans"):
        self.title = title
        self._scan = None
        self._scan_info = ScanInfo.normalize(scan_info)
        self._scan_info["is_scan_sequence"] = True
        self.custom_channels = dict()

        self._scans = list()  # scan objects or scan nodes
        self._waiting_scans = list()

    def add_custom_channel(self, acq_channel):
        assert isinstance(acq_channel, AcquisitionChannel)
        if acq_channel.name == "SUBSCANS":
            raise ValueError(
                "The name 'SUBSCANS' is reserved, can't add custom channel"
            )
        self.custom_channels[acq_channel.name] = acq_channel

    def wait_all_subscans(self, timeout=0):
        if timeout is not None:
            with gevent.timeout.Timeout(timeout):
                gevent.joinall(self._waiting_scans)
        else:
            gevent.joinall(self._waiting_scans)

    @contextmanager
    def sequence_context(self):
        sequence_glt = gevent.spawn(self.scan.run)

        self.scan.wait_state(ScanState.STARTING)

        if self.scan.state >= ScanState.USER_ABORTED:
            # error
            try:
                sequence_glt.get()
            except BaseException as exc:
                raise RuntimeError("Failed to prepare scan sequence") from exc

        if self.group_custom_slave is not None:
            self.group_custom_slave.start_event.wait()

        try:
            yield SequenceContext(self)
        finally:
            # Stop the iteration over group_acq_master
            self.group_acq_master.scan_queue.put(StopIteration)

            # The subscans should have finished before exiting the context
            try:
                self.wait_all_subscans(timeout=0)
                scans_finished = True
            except gevent.Timeout:
                gevent.killall(self._waiting_scans)
                scans_finished = False

            # Wait until all sequence events are published in Redis
            # Note: publishing is done by iterating over group_acq_master
            events_published = True
            if len(self._scans) > 0:
                try:
                    # Timeout not specified because we have no way of
                    # estimating how long it will take.
                    events_published = self.group_acq_master.wait_all_published()
                except ScanSequenceError:
                    events_published = False

            # Wait until the sequence itself finishes
            sequence_glt.get()

            # Raise exception when incomplete
            if not scans_finished:
                raise ScanSequenceError(
                    f'Some scans of the sequence "{self.title}" have not finished before exiting the sequence context'
                )
            elif not events_published:
                raise ScanSequenceError(
                    f'Some events of the sequence "{self.title}" were not published in Redis'
                )

    def _build_scan(self):
        self.group_acq_master = GroupingMaster()
        chain = AcquisitionChain()
        chain.add(self.group_acq_master)

        if len(self.custom_channels) > 0:
            self.group_custom_slave = GroupingSlave(
                "custom_channels", self.custom_channels.values()
            )
            chain.add(self.group_acq_master, self.group_custom_slave)
        else:
            self.group_custom_slave = None

        self._scan = ScanGroup(chain, self.title, save=True, scan_info=self._scan_info)
        self._scan.add_preset(StatePreset(self))

    @property
    def scan(self):
        if self._scan is None:
            self._build_scan()
        return self._scan

    @property
    def scan_info(self):
        """Return the scan info of this sequence.

        Which is the initial one, or the one published by the scan which publish
        this sequence.
        """
        if self._scan is None:
            return self._scan_info
        else:
            return self._scan.scan_info

    @property
    def state(self):
        if self._scan is None:
            return ScanState.IDLE
        else:
            return self.scan.state


class GroupingMaster(AcquisitionMaster):
    def __init__(self):
        super().__init__(
            None, name="GroupingMaster", npoints=0, prepare_once=True, start_once=True
        )

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

    def __iter__(self):
        self._publishing = True
        try:
            yield self
            for scan in self.scan_queue:
                self._publish_new_subscan(scan)
                yield self
        finally:
            self._publishing = False

    def _publish_new_subscan(self, scan):
        """Publish group scan events in Redis related to one scan"""
        self._publish_event.clear()
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

    def start(self):
        pass

    def stop(self):
        pass


class GroupingSlave(AcquisitionSlave):
    """For custom sequence channels"""

    def __init__(self, name, channels):
        super().__init__(None, name=name)
        self.start_event = gevent.event.Event()
        for channel in channels:
            self.channels.append(channel)

    def prepare(self):
        pass

    def start(self):
        self.start_event.set()

    def trigger(self):
        pass

    def stop(self):
        pass
