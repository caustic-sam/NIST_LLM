# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

import numexpr
import numpy
from collections import deque
from bliss import global_map
from bliss.common.protocols import counter_namespace
from bliss.common.counter import CalcCounter
from bliss.controllers.counter import CalcCounterController
from bliss.scanning.acquisition.calc import CalcCounterAcquisitionSlave
from bliss.controllers.mosca.counters import ROICounter, StatCounter


class CalcROICounter(CalcCounter):
    pass


class SumROICounter(CalcCounter):
    pass


class CalcROICounterController(CalcCounterController):
    def __init__(self, name, mca, config):
        self._mca = mca
        self._calc_formula = self._mca._settings.get(
            "roi_correction_formula", config.get("roi_correction_formula")
        )
        self._external_counters = config.get("external_counters", {})
        self._inputs_dict_stat = {}
        self._inputs_dict_external = {}
        self._inputs_dict_roi = {}

        super().__init__(name, config)

    @property
    def tags(self):
        return self._tags

    @property
    def inputs(self):
        return counter_namespace(
            list(self._inputs_dict_stat.values())
            + list(self._inputs_dict_roi.values())
            + list(self._inputs_dict_external.values())
        )

    def _build_output_name(self, roi_name):
        parts = roi_name.split("_")
        return f"{'_'.join(parts[:-1])}_corr_{parts[-1]}"

    def get_acquisition_object(
        self, acq_params, ctrl_params, parent_acq_params, acq_devices
    ):
        return CalcROIAcquisitionSlave(
            self, acq_devices, acq_params, ctrl_params=ctrl_params
        )

    def build_counters(self, config):
        # superseded by update_counters
        pass

    def update_stat_inputs(self):
        self._inputs_dict_stat = {}
        for cnt in self._mca._masterCC.counters:
            if isinstance(cnt, StatCounter):
                self._tags[cnt.name] = cnt.name
                self._inputs_dict_stat[cnt.name] = cnt

    def update_external_inputs(self):
        # remove current keys from tags
        for cnt in self._inputs_dict_external.values():
            self._tags.pop(cnt.name, None)

        self._inputs_dict_external = {}
        for tag, cnt in self._external_counters.items():
            self._tags[cnt.name] = tag
            if tag in self.calc_formula:
                self._inputs_dict_external[tag] = cnt

    def update_rois_inputs_and_output_counters(self):
        self._inputs_dict_roi = {}
        self._output_counters = []
        for cnt in self._mca._masterCC.counters:
            if isinstance(cnt, ROICounter):
                # only consider single channel rois (i.e. not summed by mosca)
                if not isinstance(cnt.roi.channel, tuple) and cnt.roi.channel >= 0:
                    self._inputs_dict_roi[cnt.name] = cnt
                    self._tags[cnt.name] = cnt.name

                    cntout_name = self._build_output_name(cnt.name)
                    cntout = CalcROICounter(cntout_name, self)
                    self._tags[cntout_name] = cntout_name
                    self._output_counters.append(cntout)

                    if self.calc_formula:
                        # WARNING: a CalcCounterController registers its COUNTERS (not itself, unlike the CounterController class)
                        global_map.register(cntout, parents_list=["counters"])

    def update_counters(self):
        """update inputs and outputs depending on RoiCounters declared in associated self._mca._masterCC"""

        # WARNING: a CalcCounterController registers its COUNTERS (not itself, unlike the CounterController class)
        for cntout in self._output_counters:
            global_map.unregister(cntout)

        self._counters = {}
        self._tags = {}

        # add mca stat counters as inputs
        self.update_stat_inputs()

        # add external counters (declared in config) as inputs
        self.update_external_inputs()

        # add rois inputs and build outputs
        self.update_rois_inputs_and_output_counters()

    @property
    def calc_formula(self):
        return self._calc_formula

    @calc_formula.setter
    def calc_formula(self, formula):
        if formula:
            if not isinstance(formula, str):
                raise ValueError(
                    "formula must be a string, ex: 'roi / (1-deadtime) / iodet' "
                )

            # test formula validity
            local_dict = {"roi": 1}
            for stat in self._inputs_dict_stat:
                local_dict[stat] = 1
            for tag in self._external_counters:
                local_dict[tag] = 1

            try:
                numexpr.evaluate(formula, global_dict={}, local_dict=local_dict)
            except Exception as e:
                raise ValueError(
                    f"formula not valid ({e}), ensure variables are in {list(local_dict.keys())}"
                )
        else:
            formula = (
                ""  # Do not use None for empty formula (because of settings behavior)
            )

        self._calc_formula = formula
        self._mca._settings["roi_correction_formula"] = formula
        self.update_external_inputs()

    def calc_function(self, input_dict):
        output_dict = {}
        if self.calc_formula:
            for cnt_name, cnt in self._inputs_dict_roi.items():
                # apply calc formula to each input roi considering its detector channel
                # ex: formula 'roi * icr / ocr' = > calc_roi_det01 = roi_det01 * icr_det01 / ocr_det01
                detchan = cnt.roi.channel
                cntout_name = self._build_output_name(cnt_name)
                local_dict = {"roi": input_dict[cnt_name]}
                for stat in self._inputs_dict_stat:
                    local_dict[stat] = input_dict[f"{stat}_det{detchan:02d}"]
                for tag in self._inputs_dict_external:
                    local_dict[tag] = input_dict[tag]

                output_dict[cntout_name] = numexpr.evaluate(
                    self.calc_formula, global_dict={}, local_dict=local_dict
                ).astype(float)

        return output_dict


class SumCalcROICounterController(CalcCounterController):
    def __init__(self, name, calcroicc):
        self._calcroicc = calcroicc
        self._out2in = {}
        super().__init__(name, {})

    def _build_output_name(self, roi_name):
        parts = roi_name.split("_")
        return f"{'_'.join(parts[:-2])}_sum"

    @property
    def tags(self):
        return self._tags

    def build_counters(self, config=None):
        # superseded by update_counters
        pass

    def update_counters(self):
        # clear previous calc counters

        # WARNING: a CalcCounterController registers its COUNTERS (not itself, unlike the CounterController class)
        for cntout in self._output_counters:
            global_map.unregister(cntout)

        self._input_counters = []
        self._output_counters = []
        self._counters = {}
        self._tags = {}
        self._out2in = {}

        # rebuild counters
        # list rois that should be summed
        tmp_out2in = {}
        for cntin in self._calcroicc.outputs:
            cntout_name = self._build_output_name(cntin.name)
            tmp_out2in.setdefault(cntout_name, []).append(cntin)

        # create sum counters and register corresponding inputs
        # only if there are at least 2 roi_corr to sum
        for cntout_name in tmp_out2in:
            if len(tmp_out2in[cntout_name]) > 1:
                cntout = SumROICounter(cntout_name, self)
                self._tags[cntout_name] = cntout_name
                self._output_counters.append(cntout)
                self._out2in[cntout_name] = []
                for cntin in tmp_out2in[cntout_name]:
                    self._tags[cntin.name] = cntin.name
                    self._input_counters.append(cntin)
                    self._out2in[cntout_name].append(cntin.name)

                if self._calcroicc.calc_formula:
                    # WARNING: a CalcCounterController registers its COUNTERS (not itself, unlike the CounterController class)
                    global_map.register(cntout, parents_list=["counters"])

    def calc_function(self, input_dict):
        output_dict = {}
        for cntout_name, inputs in self._out2in.items():
            output_dict[cntout_name] = 0
            for cnt_name in inputs:
                output_dict[cntout_name] += input_dict[cnt_name]
        return output_dict


class CalcROIAcquisitionSlave(CalcCounterAcquisitionSlave):
    def build_input_channel_list(self, src_acq_devices_list):
        for acq_device in src_acq_devices_list:
            for cnt, channels in acq_device._counters.items():
                if cnt in self.device.inputs:
                    # handle all channels per counter (unlike CalcCounterAcquisitionSlave)
                    for chan in channels:
                        self._inputs_channels[chan] = cnt

        self._inputs_data_buffer = {chan: deque() for chan in self._inputs_channels}

    def compute(self, sender, sender_data) -> dict:
        # buffering: tmp storage of received newdata
        self._inputs_data_buffer[sender].extend(sender_data)

        # Find the amount of aligned data (i.e the smallest newdata len among all inputs)
        # Build the input_data_dict (indexed by tags and containing aligned data for all inputs)
        # Pop data from _inputs_data_buffer while building input_data_dict

        aligned_data_index = min(
            [len(data) for data in self._inputs_data_buffer.values()]
        )
        if aligned_data_index > 0:
            input_data_dict = dict()
            for chan, cnt in self._inputs_channels.items():
                aligned_data = [
                    self._inputs_data_buffer[chan].popleft()
                    for i in range(aligned_data_index)
                ]
                input_key = (
                    chan.short_name
                    if isinstance(cnt, StatCounter)
                    else self.device.tags[cnt.name]
                )
                input_data_dict[input_key] = numpy.array(aligned_data)

            output_data_dict = self.device.calc_function(input_data_dict)

            return output_data_dict
