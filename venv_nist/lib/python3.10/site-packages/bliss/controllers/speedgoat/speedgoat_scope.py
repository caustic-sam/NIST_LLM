# -*- coding: utf-8 -*-
#
# This file is part of the mechatronic project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

import enum
import weakref
import tabulate
import gevent
import numpy as np
import time

from bliss.controllers.speedgoat.speedgoat_counter import SpeedgoatHdwCounter
from bliss.common.user_status_info import status_message


class ScopeType(enum.IntEnum):
    Null = 0
    Host = 1
    Target = 2
    File = 3
    Hidden = 4


class ScopeMode(enum.IntEnum):
    Numerical = 0
    Redraw = 1
    Sliding = 2
    Rolling = 3


class TriggerMode(enum.IntEnum):
    FreeRun = 0
    Software = 1
    Signal = 2
    Scope = 3
    ScopeEnd = 4


class TriggerSlope(enum.IntEnum):
    Either = 0
    Rising = 1
    Falling = 2


class ScopeState(enum.IntEnum):
    WaitToStart = 0
    WaitForTrigger = 1
    Acquiring = 2
    Finished = 3
    Interrupted = 4
    PreAcquiring = 5


# =============================================================================
# General Scope Controller
# =============================================================================
class SpeedgoatHdwScopeController:
    def __init__(self, speedgoat):
        self._speedgoat = speedgoat
        self._scopes = None
        self._load()

    def __info__(self):
        if self._scopes is None:
            return "    No Scope in the model"
        lines = [["", "Scope id", "Scope type"]]
        for scope_id, scope in self._load()["scopes"].items():
            lines.append(["", scope_id, scope._type])
        mystr = "\n" + tabulate.tabulate(lines, tablefmt="plain", stralign="right")
        return mystr

    def _load(self, force=False):
        if self._scopes is None or force:
            self._scopes = {}
            for scope_id in self._speedgoat.get_scope_list():
                scope_type = self._speedgoat.sc_get_type(scope_id)
                if scope_type == ScopeType.Target:
                    scope = SpeedgoatHdwTargetScope(self._speedgoat, scope_id)
                else:
                    scope = SpeedgoatHdwScope(self._speedgoat, scope_id)
                self._scopes[scope_id] = scope
        return {"scopes": self._scopes}

    @property
    def scopes(self):
        return self._load()["scopes"]


class SpeedgoatHdwScope(object):
    def __init__(self, speedgoat, scope_id):
        """Scope Class that can be used to interact with Speedgoat Scopes

        :speedgoat: Speedgoat object
        :scope_id: Scope ID (int)
        """
        self._speedgoat = weakref.proxy(speedgoat)
        self.scope_id = scope_id
        self._type = ScopeType(self._speedgoat.sc_get_type(scope_id)).name

    def add_signal(self, signal_id):
        """Add signal to the Scope

        :signal_id: ID of the signal to be added to the scope.
        The signal ID can be obtained using the speedgoat.get_signal_index function.
        """
        self._speedgoat.sc_add_signal(self.scope_id, signal_id)

    def remove_signal(self, signal_id):
        """Remove one signal from the Scope

        :signal_id: ID of the signal to be removed from the scope.
        The signal ID can be obtained using the speedgoat.get_signal_index function.
        """
        self._speedgoat.sc_rem_signal(self.scope_id, signal_id)

    def start(self):
        """Start the Scope"""
        self._speedgoat.sc_start(self.scope_id)

    def stop(self):
        """Stop the scope"""
        self._speedgoat.sc_stop(self.scope_id)

    def software_trigger(self):
        """Triggers the scope with a "software trigger".
        This has to be configured using "scope.trigger_mode = TriggerMode.Software"
        """
        self._speedgoat.sc_software_trigger(self.scope_id)

    def get_data(self, signal_id, first_point=0, num_samples=None, decimation=1):
        # TODO
        if num_samples is None:
            num_samples = self.num_samples
        # sc_get_data is a method defined in xpc.py
        return self._speedgoat.sc_get_data(
            self.scope_id, signal_id, first_point, num_samples, decimation
        )

    @property
    def auto_restart(self):
        return self._speedgoat.sc_get_auto_restart(self.scope_id)

    @auto_restart.setter
    def auto_restart(self, auto_restart):
        self._speedgoat.sc_set_auto_restart(self.scope_id, auto_restart)

    @property
    def decimation(self):
        return self._speedgoat.sc_get_decimation(self.scope_id)

    @decimation.setter
    def decimation(self, decimation):
        self._speedgoat.sc_set_decimation(self.scope_id, decimation)

    @property
    def num_samples(self):
        return self._speedgoat.sc_get_num_samples(self.scope_id)

    @num_samples.setter
    def num_samples(self, num_samples):
        self._speedgoat.sc_set_num_samples(self.scope_id, num_samples)

    @property
    def trigger_level(self):
        return self._speedgoat.sc_get_trigger_level(self.scope_id)

    @trigger_level.setter
    def trigger_level(self, trigger_level):
        self._speedgoat.sc_set_trigger_level(self.scope_id, trigger_level)

    @property
    def trigger_mode(self):
        return TriggerMode(self._speedgoat.sc_get_trigger_mode(self.scope_id))

    @trigger_mode.setter
    def trigger_mode(self, trigger_mode):
        self._speedgoat.sc_set_trigger_mode(self.scope_id, trigger_mode)

    @property
    def trigger_scope(self):
        return self._speedgoat.sc_get_trigger_scope(self.scope_id)

    @trigger_scope.setter
    def trigger_scope(self, trigger_scope):
        self._speedgoat.sc_set_trigger_scope(self.scope_id, trigger_scope)

    @property
    def trigger_scope_sample(self):
        return self._speedgoat.sc_get_trigger_scope_sample(self.scope_id)

    @trigger_scope_sample.setter
    def trigger_scope_sample(self, trigger_scope_sample):
        self._speedgoat.sc_set_trigger_scope_sample(self.scope_id, trigger_scope_sample)

    @property
    def trigger_signal(self):
        return self._speedgoat.sc_get_trigger_signal(self.scope_id)

    @trigger_signal.setter
    def trigger_signal(self, trigger_signal):
        self._speedgoat.sc_set_trigger_signal(self.scope_id, trigger_signal)

    @property
    def trigger_slope(self):
        return TriggerSlope(self._speedgoat.sc_get_trigger_slope(self.scope_id))

    @trigger_slope.setter
    def trigger_slope(self, trigger_slope):
        self._speedgoat.sc_set_trigger_slope(self.scope_id, trigger_slope)

    @property
    def num_pre_post_samples(self):
        return self._speedgoat.sc_get_num_pre_post_samples(self.scope_id)

    @num_pre_post_samples.setter
    def num_pre_post_samples(self, num_pre_post_samples):
        self._speedgoat.sc_set_num_pre_post_samples(self.scope_id, num_pre_post_samples)

    @property
    def state(self):
        return ScopeState(self._speedgoat.sc_get_state(self.scope_id))

    @property
    def type(self):
        return ScopeType(self._speedgoat.sc_get_type(self.scope_id))

    @property
    def signal_list(self):
        return self._speedgoat.sc_get_signals(self.scope_id)

    @property
    def is_finished(self):
        return self._speedgoat.is_sc_finished(self.scope_id)


class SpeedgoatHdwTargetScope(SpeedgoatHdwScope):
    @property
    def grid(self):
        return self._speedgoat.tg_sc_get_grid(self.scope_id)

    @grid.setter
    def grid(self, grid):
        self._speedgoat.tg_sc_set_grid(self.scope_id, grid)

    @property
    def mode(self):
        return ScopeMode(self._speedgoat.tg_sc_get_mode(self.scope_id))

    @mode.setter
    def mode(self, mode):
        self._speedgoat.tg_sc_set_mode(self.scope_id, mode)


# =============================================================================
# Fast DAQ
# =============================================================================
class SpeedgoatHdwFastdaqController:
    def __init__(self, speedgoat):
        self._speedgoat = speedgoat
        self._fastdaqs = None
        self._load()

    def __info__(self):
        if self._fastdaqs is None:
            return "    No Fastdaq in the model"
        lines = [["    ", "Name", "Unique Name"]]
        for fastdaq in self._fastdaqs.values():
            lines.append(["    ", fastdaq.name, fastdaq._unique_name])
        mystr = "\n" + tabulate.tabulate(lines, tablefmt="plain", stralign="right")
        return mystr

    def _load(self, force=False):
        if self._fastdaqs is None or force:
            fastdaqs = self._speedgoat._get_all_objects_from_key("bliss_fastdaq")
            if len(fastdaqs) > 0:
                self._fastdaqs = {}
                for fastdaq in fastdaqs:
                    sp_fastdaq = SpeedgoatHdwFastdaq(self._speedgoat, fastdaq)
                    setattr(self, sp_fastdaq.name, sp_fastdaq)
                    self._fastdaqs[sp_fastdaq.name] = sp_fastdaq
        return self._fastdaqs


class SpeedgoatHdwFastdaq:
    def __init__(self, speedgoat, unique_name):
        self._speedgoat = speedgoat
        self._unique_name = unique_name
        self.name = self._speedgoat.parameter.get(
            f"{self._unique_name}/bliss_fastdaq/String"
        )
        self.scope_id = int(
            self._speedgoat.parameter.get(f"{self._unique_name}/scope_id")
        )
        self.scope = self._speedgoat.scope.scopes[self.scope_id]
        self._max_sample = 2000000
        self._start_time = 0
        self._total_time = 0

    def __info__(self):
        lines = []
        lines.append(["Name", self.name])
        lines.append(["Unique Name", self._unique_name])
        mystr = "\n" + tabulate.tabulate(lines, tablefmt="plain", stralign="right")
        return mystr

    def _tree(self):
        self._speedgoat.parameter._load()["param_tree"].subtree(
            self._unique_name
        ).show()

    def set_counters(self, counter_list):
        nb_signals = len(counter_list)
        if nb_signals > self.counter_num:
            raise ValueError(
                f"FastDAQ: Signals number ({nb_signals}) exceeds maximum ({self.counter_num})"
            )

        self._signal_idx_list = []
        self._signal_name_list = []
        for counter in counter_list:
            if hasattr(counter, "_speedgoat_counter"):
                if isinstance(counter._speedgoat_counter, SpeedgoatHdwCounter):
                    cnt = counter._speedgoat_counter
                    self._signal_name_list.append(cnt.name)
                    self._signal_idx_list.append(cnt._index)
                else:
                    print(
                        f"Warning: Counter ({counter.name}) is not valid for Fastdaq Acquisition"
                    )
            else:
                print(
                    f"Warning: Counter ({counter.name}) is not valid for Fastdaq Acquisition"
                )

        sp_signal_list = np.full((self.counter_num,), 1)
        for idx in range(len(self._signal_idx_list)):
            sp_signal_list[idx] = self._signal_idx_list[idx]
        self._speedgoat.parameter.set(
            f"{self._unique_name}/counters_ids", sp_signal_list
        )

    def prepare(self, frequency, nsample, counter_list):
        self.scope.stop()
        self.scope.trigger_mode = TriggerMode.Software
        self.frequency = frequency
        self.nsample = nsample
        self.set_counters(counter_list)

    def prepare_time(self, time, counter_list):
        self.scope.stop()
        self.scope.trigger_mode = TriggerMode.Software
        self.frequency = self._speedgoat._Fs
        self.nsample = int(time * self.frequency)
        self.set_counters(counter_list)

    def start(self, wait=False, silent=True):
        self.scope.start()
        gevent.sleep(0.1)
        self.scope.software_trigger()

        self._start_time = time.time()
        self._total_time = float(self.nsample) / float(self.frequency)

        if wait:
            self.wait_finished(silent=silent)

    def state(self):
        return self.scope.state

    def get_data(self):
        result = {}
        scope_sig_list = self.scope.signal_list
        for idx in range(len(self._signal_name_list)):
            result[self._signal_name_list[idx]] = self.scope.get_data(
                scope_sig_list[idx]
            )
        return result

    def wait_finished(self, silent=True):
        with status_message() as update:
            while not self.is_finished():
                if not silent:
                    time_left = self._total_time - (time.time() - self._start_time)
                    update(
                        f" Waiting Speedgoat fastdaq to terminate {time_left:.2f}s (/{self._total_time}s)"
                    )
                gevent.sleep(0.2)
        if not silent:
            print("\n")

    def is_finished(self):
        return self.scope.state == ScopeState.Finished

    @property
    def frequency(self):
        return self._speedgoat._Fs / self.scope.decimation

    @frequency.setter
    def frequency(self, freq):
        if freq > 0:
            self.scope.decimation = int(0.5 + (self._speedgoat._Fs / freq))
        else:
            raise RuntimeError(f"FastDAQ: Frequency {freq} must be > 0")

    @property
    def counter_num(self):
        return int(self._speedgoat.parameter.get(f"{self._unique_name}/counter_num"))

    @property
    def nsample(self):
        return self.scope.num_samples

    @nsample.setter
    def nsample(self, nb_samples):
        if nb_samples > 0 and nb_samples <= self._max_sample:
            self.scope.num_samples = nb_samples
        else:
            raise RuntimeError(
                f"FastDAQ: nsample {nb_samples} must be [1:{self._max_sample}]"
            )


# =============================================================================
# Ring Buffer
# =============================================================================
class SpeedgoatHdwRingBufferController:
    def __init__(self, speedgoat):
        self._speedgoat = speedgoat
        self._ringbuffers = None
        self._load()

    def __info__(self):
        if self._ringbuffers is None:
            return "    No ringbuffer in the model"
        lines = [["    ", "Name", "Unique Name"]]
        for ringbuffer in self._ringbuffers.values():
            lines.append(["    ", ringbuffer.name, ringbuffer._unique_name])
        mystr = "\n" + tabulate.tabulate(lines, tablefmt="plain", stralign="right")
        return mystr

    def _load(self, force=False):
        if self._ringbuffers is None or force:
            ringbuffers = self._speedgoat._get_all_objects_from_key("bliss_ringbuffer")
            if len(ringbuffers) > 0:
                self._ringbuffers = {}
                for ringbuffer in ringbuffers:
                    sp_ringbuffer = SpeedgoatHdwRingBuffer(self._speedgoat, ringbuffer)
                    setattr(self, sp_ringbuffer.name, sp_ringbuffer)
                    self._ringbuffers[sp_ringbuffer.name] = sp_ringbuffer
        return self._ringbuffers


class SpeedgoatHdwRingBuffer:
    def __init__(self, speedgoat, unique_name):
        self._speedgoat = speedgoat
        self._unique_name = unique_name
        self.name = self._speedgoat.parameter.get(
            f"{self._unique_name}/bliss_ringbuffer/String"
        )
        self.scope_id = int(
            self._speedgoat.parameter.get(f"{self._unique_name}/scope_id")
        )
        self.scope = self._speedgoat.scope.scopes[self.scope_id]
        self.point_to_acq = 0

    def __info__(self):
        lines = []
        lines.append(["Name", self.name])
        lines.append(["Unique Name", self._unique_name])
        mystr = "\n" + tabulate.tabulate(lines, tablefmt="plain", stralign="right")
        return mystr

    def _tree(self):
        self._speedgoat.parameter._load()["param_tree"].subtree(
            self._unique_name
        ).show()

    def prepare(self, counter_list, nb_point):
        """Function called to prepare the acquisition."""
        self.set_counters(counter_list)
        self.reset_trigger()
        # Store the total number of points to acquire for this particular scan
        self.point_to_acq = nb_point

    def set_counters(self, counter_list):
        """Configure the counters to be saved with the Ring Buffer."""
        nb_signals = len(counter_list)
        max_cnt = int(
            self._speedgoat.parameter.get(f"{self._unique_name}/max_counters")
        )
        if nb_signals > max_cnt:
            raise ValueError(
                f"ringbuffer: Signals number ({nb_signals}) exceeds maximum ({max_cnt})"
            )

        self._signal_idx_list = []
        self._signal_name_list = []
        for counter in counter_list:
            if hasattr(counter, "_speedgoat_counter"):
                if isinstance(counter._speedgoat_counter, SpeedgoatHdwCounter):
                    cnt = counter._speedgoat_counter
                    self._signal_name_list.append(cnt.name)
                    self._signal_idx_list.append(cnt._index)
                else:
                    print(
                        f"Warning: Counter ({counter.name}) is not valid for Fastdaq Acquisition"
                    )
            else:
                print(
                    f"Warning: Counter ({counter.name}) is not valid for Fastdaq Acquisition"
                )

        sp_signal_list = np.full((max_cnt,), 1)
        for idx in range(len(self._signal_idx_list)):
            sp_signal_list[idx] = self._signal_idx_list[idx]
        self._speedgoat.parameter.set(
            f"{self._unique_name}/counters_ids", sp_signal_list
        )

    def reset_trigger(self):
        """Used to reset the buffer.
        It deletes all stored values, reset errors, and reset the stored index"""
        self._speedgoat.parameter.set(f"{self._unique_name}/point_trigger/Bias", 0)
        self._speedgoat.parameter.set(f"{self._unique_name}/reset_trigger/Bias", 0)
        self._speedgoat.parameter.set(f"{self._unique_name}/start_trigger/Bias", 0)

        reset_trigger = int(
            self._speedgoat.parameter.get(f"{self._unique_name}/reset_trigger/Bias")
        )
        self._speedgoat.parameter.set(
            f"{self._unique_name}/reset_trigger/Bias", reset_trigger + 1
        )

    def point_trigger(self):
        """Trigger the saving of one set of values."""
        point_trigger = int(
            self._speedgoat.parameter.get(f"{self._unique_name}/point_trigger/Bias")
        )
        self._speedgoat.parameter.set(
            f"{self._unique_name}/point_trigger/Bias", point_trigger + 1
        )

    def start_trigger(self):
        """Starts the Ring-buffer read-out.
        Part of the data stored in the ring-buffer is transfered to the Scope."""
        start_trigger = int(
            self._speedgoat.parameter.get(f"{self._unique_name}/start_trigger/Bias")
        )
        self._speedgoat.parameter.set(
            f"{self._unique_name}/start_trigger/Bias", start_trigger + 1
        )

    def is_finished(self):
        """Returns True is the number of data *sent* by the ring-buffer is equal to the
        number of points to acquire since the last reset."""
        return (
            int(self._speedgoat.signal.get(f"{self._unique_name}/nb_sent"))
            >= self.point_to_acq
        )

    def is_received_all(self):
        """Returns True is the number of data *received* by the ring-buffer is more than the
        number of points to acquire since the last reset."""
        return self.nb_trigs >= self.point_to_acq

    def is_overwritten(self):
        """Return whether if the Ring Buffer is overwritten"""
        return bool(self._speedgoat.signal.get(f"{self._unique_name}/is_overwritten"))

    @property
    def nb_trigs(self):
        """Number of trigs since last reset."""
        return int(self._speedgoat.signal.get(f"{self._unique_name}/pulse_counter"))

    @property
    def max_counters(self):
        """Maximum number of simultaneous counters that can be saved"""
        return int(self._speedgoat.signal.get(f"{self._unique_name}/max_counters"))

    @property
    def ringbuffer_length(self):
        """Maximum number of stored values in the ring buffer"""
        return int(
            self._speedgoat.parameter.get(f"{self._unique_name}/ring_buffer_length")
        )

    @property
    def nb_stored(self):
        """Number of stored values in the Ring Buffer"""
        return int(self._speedgoat.signal.get(f"{self._unique_name}/nb_stored"))

    @property
    def point_to_read(self):
        """Maximum transfered values each time the ring-buffer 'read' trigger is used"""
        return int(self._speedgoat.parameter.get(f"{self._unique_name}/read_data_num"))

    @point_to_read.setter
    def point_to_read(self, val):
        self._speedgoat.parameter.set(f"{self._unique_name}/read_data_num", int(val))

    def scope_read(self, nbpoint):
        """Triggers the Ring-Buffer and the scope to get 'nbpoint' stored data.
        The data is then transfered from the Scope to Bliss."""
        # Make sure the scope is stopped to be able to change the number of stored values
        self.scope.stop()
        self.scope.num_samples = nbpoint
        self.scope.start()
        # Also configure the Ring-Buffer so that it sends the same number of points to the scope
        self.point_to_read = nbpoint
        # Start the transfer of data from Ring-buffer to the scope.
        # It also starts the scope simultaneously.
        gevent.sleep(0.01)
        self.start_trigger()

        # Can already wait approximately nbpoints * sampling time of Speedgoat
        gevent.sleep(1.1 * nbpoint * self._speedgoat._Ts)

        if self.scope.state == ScopeState.WaitForTrigger:
            print("Ring Buffer Scope has not been triggered")
            return None

        elif self.scope.state == ScopeState.Finished:
            data = self.scope_get_data()

            return_data = np.zeros((len(self._signal_idx_list), nbpoint))
            # TODO - uneccessary for loop
            for i in range(len(self._signal_idx_list)):
                return_data[i] = data[i][1]

            return np.transpose(return_data)

        else:
            return None

    def scope_get_data(
        self, signals=None, first_point=0, num_samples=None, decimation=1
    ):
        """Used to get the data stored in the Scope"""
        if signals is None:
            signals = self.scope.signal_list

        data_arr = []
        for signal in signals:
            data_arr.append(
                (
                    signal,
                    self.scope.get_data(signal, first_point, num_samples, decimation),
                )
            )

        return data_arr


# =============================================================================
# Target Scopes
# =============================================================================
class SpeedgoatHdwDisplScopeController:
    def __init__(self, speedgoat):
        self._speedgoat = speedgoat
        self._tgscopes = None
        self._load()

    def __info__(self):
        if self._tgscopes is None:
            return "    No Display Scope in the model"
        lines = [["    ", "Name", "Unique Name", "ID"]]
        for tgscope in self._tgscopes.values():
            lines.append(["    ", tgscope.name, tgscope._unique_name, tgscope.scope_id])
        mystr = "\n" + tabulate.tabulate(lines, tablefmt="plain", stralign="right")
        return mystr

    def _load(self, force=False):
        if self._tgscopes is None or force:
            tgscopes = self._speedgoat._get_all_objects_from_key("bliss_scope")
            if len(tgscopes) > 0:
                self._tgscopes = {}
                for tgscope in tgscopes:
                    sp_tgscope = SpeedgoatHdwDisplScope(self._speedgoat, tgscope)
                    setattr(self, sp_tgscope.name, sp_tgscope)
                    self._tgscopes[sp_tgscope.name] = sp_tgscope
        return self._tgscopes


class SpeedgoatHdwDisplScope:
    def __init__(self, speedgoat, unique_name):
        self._speedgoat = speedgoat
        self._unique_name = unique_name
        self.name = self._speedgoat.parameter.get(
            f"{self._unique_name}/bliss_scope/String"
        )
        self.scope_id = int(
            self._speedgoat.parameter.get(f"{self._unique_name}/scope_id")
        )
        self.scope = self._speedgoat.scope.scopes[self.scope_id]

    def __info__(self):
        lines = []
        lines.append(["Name", self.name])
        lines.append(["Unique Name", self._unique_name])
        mystr = "\n" + tabulate.tabulate(lines, tablefmt="plain", stralign="right")
        return mystr

    def set_counters(self, counter_list):
        nb_signals = len(counter_list)
        max_cnt = int(self._speedgoat.parameter.get(f"{self._unique_name}/counter_num"))
        if nb_signals > max_cnt:
            raise ValueError(
                f"DisplayScope: Signals number ({nb_signals}) exceeds maximum ({max_cnt})"
            )

        self._signal_idx_list = []
        self._signal_name_list = []
        for counter in counter_list:
            if hasattr(counter, "_speedgoat_counter"):
                if isinstance(counter._speedgoat_counter, SpeedgoatHdwCounter):
                    cnt = counter._speedgoat_counter
                    self._signal_name_list.append(cnt.name)
                    self._signal_idx_list.append(cnt._index)
                else:
                    print(
                        f"Warning: Counter ({counter.name}) is not valid for Display Scope"
                    )
            else:
                print(
                    f"Warning: Counter ({counter.name}) is not valid for Display Scope"
                )

        sp_signal_list = np.full((max_cnt,), 1)
        for idx in range(len(self._signal_idx_list)):
            sp_signal_list[idx] = self._signal_idx_list[idx]
        self._speedgoat.parameter.set(
            f"{self._unique_name}/counters_ids", sp_signal_list
        )

    @property
    def frequency(self):
        return self._speedgoat._Fs / self.scope.decimation

    @frequency.setter
    def frequency(self, freq):
        if freq > 0:
            self.scope.decimation = int(0.5 + (self._speedgoat._Fs / freq))
        else:
            raise RuntimeError(f"DisplayScope: Frequency {freq} must be > 0")

    @property
    def nsample(self):
        return self.scope.num_samples

    @nsample.setter
    def nsample(self, nb_samples):
        self.scope.num_samples = nb_samples


# =============================================================================
# PROTOTYPE OF RING BUFFER without need of external signal
# =============================================================================
class SpeedgoatHdwdaqBufferController:
    def __init__(self, speedgoat):
        self._speedgoat = speedgoat
        self._daqbuffers = None
        self._load()

    def __info__(self):
        if self._daqbuffers is None:
            return "    No daqbuffer in the model"
        lines = [["    ", "Name", "Unique Name"]]
        for daqbuffer in self._daqbuffers.values():
            lines.append(["    ", daqbuffer.name, daqbuffer._unique_name])
        mystr = "\n" + tabulate.tabulate(lines, tablefmt="plain", stralign="right")
        return mystr

    def _load(self, force=False):
        if self._daqbuffers is None or force:
            daqbuffers = self._speedgoat._get_all_objects_from_key("bliss_daqbuffer")
            if len(daqbuffers) > 0:
                self._daqbuffers = {}
                for daqbuffer in daqbuffers:
                    sp_daqbuffer = SpeedgoatHdwdaqbuffer(self._speedgoat, daqbuffer)
                    setattr(self, sp_daqbuffer.name, sp_daqbuffer)
                    self._daqbuffers[sp_daqbuffer.name] = sp_daqbuffer
        return self._daqbuffers


class SpeedgoatHdwdaqbuffer:
    def __init__(self, speedgoat, unique_name):
        self._speedgoat = speedgoat
        self._unique_name = unique_name
        self.name = self._speedgoat.parameter.get(
            f"{self._unique_name}/bliss_daqbuffer/String"
        )
        self.scope_id = int(
            self._speedgoat.parameter.get(f"{self._unique_name}/scope_id")
        )
        self.scope = self._speedgoat.scope.scopes[self.scope_id]
        self.point_to_acq = 0
        self.__stop_flag = False
        self.read_points = 0

    def __info__(self):
        lines = []
        lines.append(["Name", self.name])
        lines.append(["Unique Name", self._unique_name])
        mystr = "\n" + tabulate.tabulate(lines, tablefmt="plain", stralign="right")
        return mystr

    def _tree(self):
        self._speedgoat.parameter._load()["param_tree"].subtree(
            self._unique_name
        ).show()

    def prepare(self, counter_list, nb_point):
        """Function called to prepare the acquisition."""
        # Prepare the triggers
        self.deactivate_triggers()
        self.reset_trigger()

        # Store the total number of points to acquire for this particular scan
        self.point_to_acq = nb_point
        self.__stop_flag = False
        self.read_points = 0

        # Set the counters to be saved
        self.set_counters(counter_list)

    def set_counters(self, counter_list):
        """Configure the counters to be saved with the Ring Buffer."""
        nb_signals = len(counter_list)
        max_cnt = int(
            self._speedgoat.parameter.get(f"{self._unique_name}/max_counters")
        )
        if nb_signals > max_cnt:
            raise ValueError(
                f"daqbuffer: Signals number ({nb_signals}) exceeds maximum ({max_cnt})"
            )

        self._signal_idx_list = []
        self._signal_name_list = []
        for counter in counter_list:
            if hasattr(counter, "_speedgoat_counter"):
                if isinstance(counter._speedgoat_counter, SpeedgoatHdwCounter):
                    cnt = counter._speedgoat_counter
                    self._signal_name_list.append(cnt.name)
                    self._signal_idx_list.append(cnt._index)
                else:
                    print(
                        f"Warning: Counter ({counter.name}) is not valid for Fastdaq Acquisition"
                    )
            else:
                print(
                    f"Warning: Counter ({counter.name}) is not valid for Fastdaq Acquisition"
                )

        sp_signal_list = np.full((max_cnt,), 1)
        for idx in range(len(self._signal_idx_list)):
            sp_signal_list[idx] = self._signal_idx_list[idx]
        self._speedgoat.parameter.set(
            f"{self._unique_name}/counters_ids", sp_signal_list
        )

    def activate_triggers(self):
        """Activate automatic triggering"""
        self._speedgoat.parameter.set(f"{self._unique_name}/trigger/Amplitude", 1)

    def deactivate_triggers(self):
        """Deactivate automatic triggering"""
        self._speedgoat.parameter.set(f"{self._unique_name}/trigger/Amplitude", 0)

    def reset_trigger(self):
        """Used to reset the buffer.
        It deletes all stored values, reset errors, and reset the stored index"""
        self._speedgoat.parameter.set(f"{self._unique_name}/reset_trigger/Bias", 0)
        self._speedgoat.parameter.set(f"{self._unique_name}/start_trigger/Bias", 0)

        reset_trigger = int(
            self._speedgoat.parameter.get(f"{self._unique_name}/reset_trigger/Bias")
        )
        self._speedgoat.parameter.set(
            f"{self._unique_name}/reset_trigger/Bias", reset_trigger + 1
        )

    def start_trigger(self):
        """Starts the Ring-buffer read-out.
        Part of the data stored in the ring-buffer is transfered to the Scope."""
        start_trigger = int(
            self._speedgoat.parameter.get(f"{self._unique_name}/start_trigger/Bias")
        )
        self._speedgoat.parameter.set(
            f"{self._unique_name}/start_trigger/Bias", start_trigger + 1
        )

    def is_finished(self):
        """Returns True is the number of data *sent* by the ring-buffer is equal to the
        number of points to acquire since the last reset."""
        return (
            int(self._speedgoat.signal.get(f"{self._unique_name}/nb_sent"))
            >= self.point_to_acq
        )

    def is_received_all(self):
        """Returns True is the number of data *received* by the ring-buffer is more than the
        number of points to acquire since the last reset."""
        return self.nb_trigs >= self.point_to_acq

    def is_overwritten(self):
        """Return whether if the Ring Buffer is overwritten"""
        return bool(self._speedgoat.signal.get(f"{self._unique_name}/is_overwritten"))

    @property
    def nb_trigs(self):
        """Number of trigs since last reset."""
        return int(self._speedgoat.signal.get(f"{self._unique_name}/pulse_counter"))

    @property
    def max_counters(self):
        """Maximum number of simultaneous counters that can be saved"""
        return int(self._speedgoat.signal.get(f"{self._unique_name}/max_counters"))

    @property
    def daqbuffer_length(self):
        """Maximum number of stored values in the ring buffer"""
        return int(
            self._speedgoat.parameter.get(f"{self._unique_name}/ring_buffer_length")
        )

    @property
    def nb_stored(self):
        """Number of stored values in the Ring Buffer"""
        return int(self._speedgoat.signal.get(f"{self._unique_name}/nb_stored"))

    @property
    def decimation(self):
        """"""
        return int(self._speedgoat.parameter.get(f"{self._unique_name}/trigger/Period"))

    @decimation.setter
    def decimation(self, val):
        self._speedgoat.parameter.set(f"{self._unique_name}/trigger/Period", int(val))

    @property
    def point_to_read(self):
        """Maximum transfered values each time the ring-buffer 'read' trigger is used"""
        return int(self._speedgoat.parameter.get(f"{self._unique_name}/read_data_num"))

    @point_to_read.setter
    def point_to_read(self, val):
        self._speedgoat.parameter.set(f"{self._unique_name}/read_data_num", int(val))

    def scope_read(self, nbpoint):
        """Triggers the Ring-Buffer and the scope to get 'nbpoint' stored data.
        The data is then transfered from the Scope to Bliss."""
        # Make sure the scope is stopped to be able to change the number of stored values
        self.scope.stop()
        self.scope.num_samples = nbpoint
        self.scope.start()
        # Also configure the Ring-Buffer so that it sends the same number of points to the scope
        self.point_to_read = nbpoint
        # Start the transfer of data from Ring-buffer to the scope.
        # It also starts the scope simultaneously.
        gevent.sleep(0.01)
        self.start_trigger()

        # Can already wait approximately nbpoints * sampling time of Speedgoat
        gevent.sleep(1.1 * nbpoint * 0.0001)

        if self.scope.state == ScopeState.WaitForTrigger:
            print("Ring Buffer Scope has not been triggered")
            return None

        elif self.scope.state == ScopeState.Finished:
            data = self.scope_get_data()

            result = {}
            for idx in range(len(self._signal_name_list)):
                result[self._signal_name_list[idx]] = data[idx][1]

            return result

            # self._signal_idx_list = []
            # self._signal_name_list = []

            # return_data = np.zeros((len(self._signal_idx_list), nbpoint))
            # for i in range(len(self._signal_idx_list)):
            #     return_data[i] = data[i][1]

            # return np.transpose(return_data)

        else:
            return None

    def scope_get_data(
        self, signals=None, first_point=0, num_samples=None, decimation=1
    ):
        """Used to get the data stored in the Scope"""
        if signals is None:
            signals = self.scope.signal_list

        data_arr = []
        for signal in signals:
            data_arr.append(
                (
                    signal,
                    self.scope.get_data(signal, first_point, num_samples, decimation),
                )
            )

        return data_arr

    def reading(self):
        """Function used to start the measurement and get all the data"""
        # Start triggering data
        self.activate_triggers()

        # Initialize results
        result = {}
        for idx in range(len(self._signal_name_list)):
            result[self._signal_name_list[idx]] = np.empty((0,))

        try:
            # Get data until Ring Buffer as register all the data
            while (not self.__stop_flag) and (not self.is_received_all()):
                point_acquired = self.nb_stored
                if point_acquired > 100:
                    data = self.scope_read(point_acquired)

                    if data is not None:
                        for idx in range(len(self._signal_name_list)):
                            result[self._signal_name_list[idx]] = np.hstack(
                                (
                                    result[self._signal_name_list[idx]],
                                    data[self._signal_name_list[idx]],
                                )
                            )

                gevent.sleep(10e-3)

            # Get data until Ring Buffer as sent all the data
            while (not self.__stop_flag) and (not self.is_finished()):
                data = self.scope_read(self.nb_stored)

                if data is not None:
                    for idx in range(len(self._signal_name_list)):
                        result[self._signal_name_list[idx]] = np.hstack(
                            (
                                result[self._signal_name_list[idx]],
                                data[self._signal_name_list[idx]],
                            )
                        )

                gevent.sleep(10e-3)

            # Check if too much data has been acquired
            for idx in range(len(self._signal_name_list)):
                if len(result[self._signal_name_list[idx]]) > self.point_to_acq:
                    result[self._signal_name_list[idx]] = np.delete(
                        result[self._signal_name_list[idx]],
                        np.arange(
                            self.point_to_acq
                            - len(result[self._signal_name_list[idx]]),
                            0,
                        ),
                    )

        except KeyboardInterrupt:
            return result

        return result
