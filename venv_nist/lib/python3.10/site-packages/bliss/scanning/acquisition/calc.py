# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

import numpy
from collections import deque

from bliss.scanning.chain import AcquisitionSlave, ChainNode
from bliss.scanning.channel import (
    AcquisitionChannel,
    LimaAcquisitionChannel,
    Lima2AcquisitionChannel,
)
from bliss.common.event import dispatcher
from blissdata.streams.base import EventRange
from blissdata.redis_engine.exceptions import EmptyViewException


class CalcHook(object):
    def compute(self, sender, data_dict):
        raise NotImplementedError

    def prepare(self):
        pass

    def start(self):
        pass

    def stop(self):
        pass


class CalcAcquisitionSlaveBase(AcquisitionSlave):
    """Emits data based on data emitted by other acquisition objects."""

    def __init__(self, *args, **kwargs):
        self._connected = False
        super().__init__(*args, **kwargs)

    def prepare(self):
        self.connect()

    def start(self):
        pass

    def stop(self):
        self.disconnect()

    def connect(self):
        if self._connected:
            return
        for channel in self._iter_input_channels():
            dispatcher.connect(self.new_data_received, "new_data", channel)
        self._connected = True

    def disconnect(self):
        if not self._connected:
            return
        for channel in self._iter_input_channels():
            dispatcher.disconnect(self.new_data_received, "new_data", channel)
        self._connected = False

    def _iter_input_channels(self):
        raise NotImplementedError

    def _iter_output_channels(self):
        yield from self.channels

    def new_data_received(self, data, signal, sender):
        input_data = self._prepare_calc_input(data, signal, sender)
        if input_data is not None:
            calc_data = self.compute(sender, input_data)
            self._emit_calc_data(calc_data)

    def _prepare_calc_input(self, data, signal, sender):
        return data

    def compute(self, sender, input_data: dict) -> dict:
        raise NotImplementedError

    def _emit_calc_data(self, calc_data: dict):
        if not calc_data:
            return
        for channel in self._iter_output_channels():
            # Get channel data from calculation
            if self.device is None:
                # note: use channel **name** (see issue #3026)
                output_data = calc_data.get(channel.name)
            else:
                output_data = calc_data.get(self.device.tags[channel.short_name])
            if output_data is None:
                continue
            output_data = numpy.asarray(output_data)

            channel.emit(output_data)


class CalcChannelAcquisitionSlave(CalcAcquisitionSlaveBase):
    """
    Helper to do some extra Calculation on channels.
    i.e: compute encoder position to user position
    Args:
        src_acq_devices_list -- list or tuple of acq(device/master) you want to listen to.
        func -- the transformation function. This will have has input a  dictionary
        with the name of counter as the key and the value has the data of source data channel.
        This function should return a dictionary with the name of the destination channel as key,
        and the value as its data.
        Can also be an inherited class of **CalcHook**:
         - the transformation function is the **compute** method.
         - optionally you can redefine prepare,start,stop.
    """

    def __init__(
        self,
        name,
        src_acq_devices_list,
        func,
        output_channels_list,
        prepare_once=False,
        start_once=False,
    ):
        super().__init__(
            None,
            name=name,
            trigger_type=AcquisitionSlave.HARDWARE,
            prepare_once=prepare_once,
            start_once=start_once,
        )

        self.src_acq_devices_list = src_acq_devices_list

        if isinstance(func, CalcHook):
            self.cbk = func
        else:

            class CBK(CalcHook):
                def compute(self, sender, data_dict):
                    return func(sender, data_dict)

            self.cbk = CBK()

        for chan_out in output_channels_list:
            if isinstance(chan_out, AcquisitionChannel):
                self.channels.append(chan_out)
            elif isinstance(chan_out, str):
                self.channels.append(AcquisitionChannel(chan_out, float, ()))
            else:
                raise TypeError(f"Object '{chan_out}'' is not an AcquisitionChannel")

    def _iter_input_channels(self):
        for acq_device in self.src_acq_devices_list:
            for channel in acq_device.channels:
                yield channel

    def prepare(self):
        self.cbk.prepare()
        super().prepare()

    def start(self):
        self.cbk.start()
        super().start()

    def stop(self):
        super().stop()
        self.cbk.stop()

    def _prepare_calc_input(self, data, signal, sender):
        input_data = super()._prepare_calc_input(data, signal, sender)
        return {sender.short_name: input_data}

    def compute(self, sender, input_data: dict) -> dict:
        return self.cbk.compute(sender, input_data)


class CalcCounterAcquisitionSlave(CalcAcquisitionSlaveBase):
    """
    Helper to do some extra Calculation on counters.
    i.e: compute encoder position to user position
    Args:
        controller -- CalcCounterController Object
        src_acq_devices_list -- list or tuple of acq(device/master) you want to listen to.
    """

    def __init__(self, controller, src_acq_devices_list, acq_params, ctrl_params=None):
        super().__init__(
            controller,
            name=controller.name,
            npoints=acq_params.get("npoints", 1),
            trigger_type=AcquisitionSlave.HARDWARE,
            ctrl_params=ctrl_params,
        )

        self._inputs_channels = dict()
        self.build_input_channel_list(src_acq_devices_list)

        self._lima_cursors = {}

    def build_input_channel_list(self, src_acq_devices_list):
        for acq_device in src_acq_devices_list:
            for cnt, channels in acq_device._counters.items():
                # filter unwanted counters and extra channels
                if cnt in self.device._input_counters:
                    # ignore multi channels per counter (see sampling)
                    self._inputs_channels[channels[0]] = cnt

        self._inputs_data_buffer = {chan: deque() for chan in self._inputs_channels}

    def _iter_input_channels(self):
        yield from self._inputs_channels

    def _prepare_calc_input(self, data, signal, sender):
        if not isinstance(sender, (LimaAcquisitionChannel, Lima2AcquisitionChannel)):
            return super()._prepare_calc_input(data, signal, sender)

        channel = sender
        json_status = data
        try:
            cursor = self._lima_cursors[channel]
        except KeyError:
            cursor = channel.stream.cursor()
            self._lima_cursors[channel] = cursor

        try:
            view = channel.stream._build_view_from_events(
                cursor._hl_index,
                EventRange(cursor._ll_index, 0, [json_status], False),
                last_only=False,
            )
            cursor._hl_index = view.index + len(view)
        except EmptyViewException:
            return None
        finally:
            cursor._ll_index += 1

        return view.get_data()

    def compute(self, sender, sender_data) -> dict:
        """
        This method works only if all input_counters will generate the same number of points !!!
        It registers all data comming from the input counters.
        It calls calc_function with input counters data which have reach the same index
        This function is called once per counter (input and output).

        * <sender> = AcquisitionChannel
        * <data_dict> = {'em1ch1': array([0.00256367])}
        """

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
                input_data_dict[self.device.tags[cnt.name]] = numpy.array(aligned_data)

            output_data_dict = self.device.calc_function(input_data_dict)

            return output_data_dict


class CalcCounterChainNode(ChainNode):
    def get_acquisition_object(
        self, acq_params, ctrl_params=None, parent_acq_params=None
    ):

        # Check if Acquisition Devices of dependant counters already exist
        acq_devices = []
        for node in self._calc_dep_nodes.values():
            acq_obj = node.acquisition_obj
            if acq_obj is None:
                raise ValueError(
                    f"cannot create CalcCounterAcquisitionSlave: acquisition object of {node}({node.controller}) is None!"
                )
            else:
                acq_devices.append(acq_obj)

        return self.controller.get_acquisition_object(
            acq_params, ctrl_params, parent_acq_params, acq_devices
        )
