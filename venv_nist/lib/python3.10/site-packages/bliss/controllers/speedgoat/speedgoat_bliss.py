# -*- coding: utf-8 -*-
#
# This file is part of the mechatronic project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

"""
Speedgoat BLISS support.
"""

import gevent

from bliss import global_map
from bliss.common.axis.state import AxisState
from bliss.common.utils import autocomplete_property
from bliss.common.counter import SamplingCounter, SamplingMode
from bliss.scanning.acquisition.counter import SamplingCounterAcquisitionSlave
from bliss.scanning.chain import AcquisitionMaster, AcquisitionSlave
from bliss.controllers.counter import SamplingCounterController
from bliss.controllers.bliss_controller import BlissController
from bliss.controllers.motor import Controller

from bliss.controllers.speedgoat.speedgoat_counter import SpeedgoatHdwCounter


class SpeedgoatController(BlissController):
    """Bliss controller for Speedgoat machine"""

    def __init__(self, config):
        BlissController.__init__(self, config)

        global_map.register(self, parents_list=["counters"])

        # Speedgoat Hardware controller
        self._hwc = self._config.get("speedgoat_hardware_controller")

        # Counter controller
        self._scc = None

    def _load_config(self):
        """
        Read and apply the YML configuration of this container
        """
        # Each Speedgoat has a ring-buffer
        speedgoat_ring_buffer = self.config.get("speedgoat_ring_buffer", None)

        # Counter controller + SpeedgoatHwCounters
        self._scc = SpeedgoatCountersController(
            self, ring_buffer_name=speedgoat_ring_buffer
        )

        # Parameter and/or Signal counters
        extra_counters = self.config.get("counters", None)
        if extra_counters is not None:
            for counter in extra_counters:
                counter_name = counter.get("counter_name", None)
                if counter_name is None:
                    raise RuntimeError(
                        "SpeedgoatController->param/signal counter: No counter_name specified"
                    )
                sp_name = counter.get("speedgoat_name", None)
                if sp_name is None:
                    raise RuntimeError(
                        "SpeedgoatController->param/signal counter: No speedgoat_name specified"
                    )
                SpeedgoatParamSignalCounter(counter_name, counter, self._scc)

    def _init(self):
        """
        Place holder for any action to perform after the configuration has been loaded.
        """
        # called in esrf_dcm, kohzu_dcm
        pass

    @autocomplete_property
    def counters(self):
        return self._scc.counters

    def __info__(self):
        """Command line info string"""
        txt = self.get_info()
        return txt

    def get_info(self):
        """Return controller info as a string"""
        return f"Speedgoat Controller {self.name}"

    def add_signal_counter(self, speedgoat_name):
        """called ???"""
        pass


class SpeedgoatMotorController(Controller):
    """Bliss Motor controller for Speedgoat machine"""

    def _load_config(self):
        super()._load_config()
        self.speedgoat = self.config.get("speedgoat_hardware_controller", None)

        self.speedgoat_names = {}
        self.speedgoat_motors = {}
        for name, config in self._axes_config.items():
            sp_name = config.get("speedgoat_name")
            if sp_name not in self.speedgoat.motor._motors.keys():
                raise RuntimeError(f"Motor {sp_name} not in Speedgoat Model")
            self.speedgoat_names[name] = sp_name
            self.speedgoat_motors[name] = self.speedgoat.motor._motors[sp_name]

        self._axis_init_done = {}

    def initialize_axis(self, axis):
        if (
            axis.name not in self._axis_init_done.keys()
            or self._axis_init_done[axis.name] is False
        ):
            self._axis_init_done[axis.name] = True
            try:
                axis.low_limit = self.speedgoat_motors[axis.name].limit_neg
                axis.high_limit = self.speedgoat_motors[axis.name].limit_pos
            except Exception:
                self._axis_init_done[axis.name] = False

    def read_position(self, axis):
        return self.speedgoat_motors[axis.name].position

    def read_velocity(self, axis):
        return self.speedgoat_motors[axis.name].velocity

    def set_velocity(self, axis, new_velocity):
        self.speedgoat_motors[axis.name].velocity = int(new_velocity)

    def read_acceleration(self, axis):
        acc_time = self.speedgoat_motors[axis.name].acc_time
        velocity = self.speedgoat_motors[axis.name].velocity
        return velocity / acc_time

    def set_acceleration(self, axis, new_acc):
        acc_time = self.speedgoat_motors[axis.name].velocity / new_acc
        self.speedgoat_motors[axis.name].acc_time = acc_time

    def state(self, axis):
        # speedgoat motor states: 0: ready 1:moving 2:lim_neg 3:lim_pos 4:stopped 5:error
        if not self.speedgoat._is_app_running:
            return AxisState("OFF")
        state = self.speedgoat_motors[axis.name].state
        if state == 1:
            return AxisState("MOVING")
        if state == 2:
            return AxisState("LIMNEG")
        if state == 3:
            return AxisState("LIMPOS")
        if state == 5:
            return AxisState("FAULT")
        return AxisState("READY")

    def prepare_move(self, motion):
        self.speedgoat_motors[motion.axis.name].setpoint = motion.target_pos

    def start_one(self, motion):
        self.speedgoat_motors[motion.axis.name].start()

    def start_all(self, *motions):
        for m in motions:
            self.start_one(m)

    def stop_one(self, axis):
        self.speedgoat_motors[axis.name].stop()

    def stop_all(self, *motions):
        for m in motions:
            self.stop_one(m.axis)

    def set_limits(self, axis, limits):
        self.speedgoat_motors[axis.name].limit_neg = limits[0]
        self.speedgoat_motors[axis.name].limit_pos = limits[1]
        axis.limits = limits


class SpeedgoatCountersController(SamplingCounterController):
    """Bliss Counter controller for Speedgoat machine"""

    def __init__(self, speedgoat, ring_buffer_name=None):
        super().__init__(f"{speedgoat._name}_scc", register_counters=False)
        self._ring_buffer_name = ring_buffer_name
        self.speedgoat = speedgoat
        for cnt_name in speedgoat._hwc.counter._counters:
            SpeedgoatCounter(cnt_name, {}, self)
        global_map.register(speedgoat, parents_list=["counters"])

    def get_acquisition_object(self, acq_params, ctrl_params, parent_acq_params):
        trigger_type = acq_params.pop("trigger_type")
        if trigger_type == "HARDWARE":
            # remove the count_time used in the other case for
            # the SamplingCounterAcquisitionSlave
            # (i.e. if trigger_type != "HARDWARE" )
            acq_params.pop("count_time")
            return SpeedgoatRingBufferAcquisitionSlave(
                self,
                ring_buffer_name=self._ring_buffer_name,
                ctrl_params=ctrl_params,
                **acq_params,
            )
        else:
            return SamplingCounterAcquisitionSlave(
                self, ctrl_params=ctrl_params, **acq_params
            )

    def get_default_chain_parameters(self, scan_params, acq_params):
        if "count_time" in acq_params.keys():
            count_time = acq_params["count_time"]
        else:
            count_time = scan_params["count_time"]
        if "npoints" in acq_params.keys():
            npoints = acq_params["npoints"]
        else:
            npoints = scan_params["npoints"]
        trigger_type = acq_params.get("trigger_type", "SOFTWARE")
        params = {
            "count_time": count_time,
            "npoints": npoints,
            "trigger_type": trigger_type,
        }
        return params

    def read_all(self, *counters):
        values = []
        for cnt in counters:
            values.append(cnt.read())
        return values


class SpeedgoatCounter(SamplingCounter):
    """Bliss SamplingCounter for Speedgoat machine"""

    def __init__(self, name, config, controller):
        super().__init__(name, controller, mode=SamplingMode.LAST)
        self._speedgoat_counter = controller.speedgoat._hwc.counter._counters[name]
        self._unit = self._speedgoat_counter.unit

    def read(self):
        return self._speedgoat_counter.value


class SpeedgoatParamSignalCounter(SamplingCounter):
    """Additional counters for Speedgoat (can be parameter of signal)"""

    def __init__(self, name, config, controller):
        super().__init__(name, controller, mode=SamplingMode.LAST)
        self._speedgoat_hw = controller.speedgoat._hwc
        self._type = config.get("counter_type")
        self._speedgoat_name = config.get("speedgoat_name")

    def read(self):
        if self._type == "parameter":
            return self._speedgoat_hw.parameter.get(self._speedgoat_name)
        if self._type == "signal":
            return self._speedgoat_hw.signal.get(self._speedgoat_name)


class SpeedgoatRingBufferAcquisitionSlave(AcquisitionSlave):
    """Acquisition slave corresponding to Speedgoat Ring Buffer"""

    def __init__(
        self, acq_controller, ring_buffer_name=None, npoints=1, ctrl_params=None
    ):
        AcquisitionSlave.__init__(
            self,
            acq_controller,
            npoints=npoints,
            trigger_type=AcquisitionMaster.HARDWARE,
            ctrl_params=ctrl_params,
        )

        self.__stop_flag = False
        self._speedgoat = acq_controller.speedgoat
        if ring_buffer_name is None:
            raise ValueError(
                "SpeedgoatRingBufferAcquisitionSlave: No Speedgoat RingBuffer specified"
            )
        self.ring_buffer = self._speedgoat._hwc.ringbuffer._load()[ring_buffer_name]
        self.nb_points = npoints

    def add_counter(self, counter):
        if hasattr(counter, "_speedgoat_counter"):
            if isinstance(counter._speedgoat_counter, SpeedgoatHdwCounter):
                super().add_counter(counter)
            else:
                print(
                    f"Warning: Counter ({counter.name}) is not valid for RingBuffer Acquisition"
                )
        else:
            print(
                f"Warning: Counter ({counter.name}) is not valid for RingBuffer Acquisition"
            )

    def wait_ready(self):
        # return only when ready
        return True

    def prepare(self):
        self.ring_buffer.prepare(list(self._counters.keys()), self.nb_points)
        self.__stop_flag = False

    def start(self):
        # Start speedgoat DAQ device
        pass

    def stop(self):
        # Set the stop flag to stop the reading process
        self.__stop_flag = True

    def reading(self):
        """Function used by BLISS during zap scans or time scans"""
        point_acquired_total = 0
        # Get data until Ring Buffer as register all the data
        while (not self.__stop_flag) and (not self.ring_buffer.is_received_all()):
            point_acquired = self.ring_buffer.nb_stored
            # Take only 10 points minimum to limit access to the Speedgoat
            if point_acquired > 9:
                point_acquired_total += point_acquired
                data = self.ring_buffer.scope_read(point_acquired)

                if data is not None:
                    self.emit_progress_signal({"nb_points": point_acquired_total})
                    self.channels.update_from_array(data)

            gevent.sleep(10e-3)

        # Get data until Ring Buffer as sent all the data
        while (not self.__stop_flag) and (not self.ring_buffer.is_finished()):
            point_acquired = self.ring_buffer.nb_stored
            data = self.ring_buffer.scope_read(point_acquired)
            point_acquired_total += point_acquired

            if data is not None:
                self.emit_progress_signal({"nb_points": point_acquired_total})
                self.channels.update_from_array(data)

            gevent.sleep(10e-3)

        if self.ring_buffer.is_overwritten():
            raise RuntimeError("Speedgoat Ring Buffer is overwritten")
