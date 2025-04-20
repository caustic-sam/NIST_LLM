# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

from bliss.config import settings

from bliss.common.scans import ct
from bliss.common.cleanup import cleanup
from bliss.controllers.counter import CalcCounterController
from bliss.common.counter import IntegratingCounter
from bliss.scanning.acquisition.calc import CalcCounterAcquisitionSlave


class BackgroundCalcCounterController(CalcCounterController):
    def __init__(self, name, config):

        self.background_object = config.get("open_close", None)

        self.background_object_initial_state = "UNKNOWN"

        CalcCounterController.__init__(self, name, config)

        self._integration_time = None
        self._integration_time_index = {}
        self._time_counter_name = None
        background_setting_name = f"background_{self.name}"
        background_setting_default = {}
        for cnt in self.inputs:
            tag = self.tags[cnt.name]
            if tag == "count_time":
                self._time_counter_name = cnt.name
            background_setting_default[tag] = 0.0
        self.background_setting = settings.HashSetting(
            background_setting_name, default_values=background_setting_default
        )

    def __info__(self):
        mystr = ""
        for cnt in self.outputs:
            tag = self.tags[cnt.name]
            background = self.background_setting[tag]
            mystr += f"{cnt.name} - {background}\n"
        bck_time = self.background_setting["background_time"]
        mystr += f"\nBackground Integration Time: {bck_time} [s]\n"

        if self._time_counter_name:
            mystr += f"count_time counter is: {self._time_counter_name} ({self.inputs[self._time_counter_name].fullname})\n"

        return mystr

    def get_acquisition_object(
        self, acq_params, ctrl_params, parent_acq_params, acq_devices
    ):
        return BackgroundCalcAcquisitionSlave(
            self, acq_devices, acq_params, ctrl_params=ctrl_params
        )

    def get_default_chain_parameters(self, scan_params, acq_params):
        acq_params = super().get_default_chain_parameters(scan_params, acq_params)
        if acq_params.get("count_time") is None:
            acq_params["count_time"] = scan_params["count_time"]
        return acq_params

    def get_input_counter_from_tag(self, tag):
        for cnt in self.inputs:
            if self.tags[cnt.name] == tag:
                return cnt

        return None

    def take_background(self, time=1.0, set_value=None):
        if set_value is not None:
            for cnt in self.inputs:
                tag = self.tags[cnt.name]
                self.background_setting[tag] = set_value
                self.background_setting["background_time"] = time
        else:
            if self.background_object is None:
                self.take_background_data(time)
            else:
                # Store initial state.
                self.background_object_initial_state = self.background_object.state

                # Close beam.
                self.background_object.close()

                # Take background.
                if self.background_object.state == "CLOSED":
                    with cleanup(self._close):
                        self.take_background_data(time)
                else:
                    print(
                        "Close functions did not succeed, Backgrounds have not been changed !!!"
                    )

    def _close(self):
        """Re-open if initial state was OPEN"""
        if self.background_object_initial_state == "OPEN":
            self.background_object.open()

    def take_background_data(self, time):
        scan_ct = ct(time, self.inputs, run=False)
        scan_ct.run()
        for cnt in self.inputs:
            tag = self.tags[cnt.name]
            background = scan_ct.streams[cnt.name][0]
            self.background_setting[tag] = background
            self.background_setting["background_time"] = time
            print(f"{cnt.name} - {background}")

    def calc_function(self, input_dict):
        value = {}
        for tag in input_dict.keys():
            cnt = self.get_input_counter_from_tag(tag)
            background = self.background_setting[tag]
            if isinstance(cnt, IntegratingCounter):
                background /= self.background_setting["background_time"]
                if self._time_counter_name:
                    background *= input_dict["count_time"]
                else:
                    if isinstance(self._integration_time, list):
                        background *= self._integration_time[
                            self._integration_time_index[tag]
                        ]
                        self._integration_time_index[tag] = (
                            self._integration_time_index[tag] + 1
                        )
                    else:
                        background *= self._integration_time

            value[tag] = input_dict[tag] - background

        return value


class BackgroundCalcAcquisitionSlave(CalcCounterAcquisitionSlave):
    """
    Helper to do some extra Calculation on counters.
    i.e: compute encoder position to user position
    Args:
        controller -- CalcCounterController Object
        src_acq_devices_list -- list or tuple of acq(device/master) you want to listen to.
    """

    def __init__(self, controller, src_acq_devices_list, acq_params, ctrl_params=None):
        super().__init__(
            controller, src_acq_devices_list, acq_params, ctrl_params=ctrl_params
        )
        self._integration_time = None
        if "count_time" in acq_params.keys():
            self._integration_time = acq_params["count_time"]

    def prepare(self):
        super().prepare()
        if self._integration_time is not None:
            self.device._integration_time = self._integration_time
            for o_cnt in self.device._output_counters:
                self.device._integration_time_index[self.device.tags[o_cnt.name]] = 0
