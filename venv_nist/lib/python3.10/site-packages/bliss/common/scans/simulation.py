# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

import time
import numpy
import gevent
import functools
from collections.abc import Iterable
from itertools import repeat

from bliss.common.logtools import log_debug
from bliss.common.axis.axis import Axis
from bliss.scanning.chain import AcquisitionMaster
from bliss.scanning.toolbox import ChainBuilder
from bliss.scanning.chain import AcquisitionSlave, TRIGGER_MODE_ENUM
from bliss.scanning.acquisition.motor import MotorMaster, LinearStepTriggerMaster
from bliss.scanning.scan import Scan
from bliss.scanning.chain import AcquisitionChain
from bliss.scanning.scan_info import ScanInfo
from bliss.common.counter import Counter
from bliss.common import event as levent
from bliss.controllers.counter import CounterController
from bliss.controllers.counter import SamplingCounterController
from bliss.controllers.demo.sample_stage_diode import SampleStageDiode
from bliss.common.utils import all_equal, deep_update
from bliss.common.scans.meshes import amesh
from bliss.common.image_tools import test_image
from bliss.common.protocols import HasMetadataForScan


class Clock:
    def __init__(self):
        self.offset = None
        self.reset()

    def reset(self):
        self.offset = time.time()

    def time(self):
        return time.time() - self.offset


_CLOCK = Clock()


class HWTriggeringSimulator(AcquisitionMaster):
    def __init__(
        self,
        on_trigger_callback,
        npoints,
        triggers_number,
        triggers_delta,
        trigger_delay=0,
        prepare_once=True,
        start_once=False,
    ):

        self.on_trigger_callback = on_trigger_callback
        if isinstance(triggers_number, Iterable):
            self._triggers_number_iter = iter(triggers_number)
        else:
            self._triggers_number_iter = repeat(triggers_number)

        self.triggers_number = None
        self.triggers_delta = triggers_delta
        self._task = None
        self._nb_point = 0
        self._trigger_delay = trigger_delay

        AcquisitionMaster.__init__(
            self,
            None,
            name="HWTriggeringSimulator",
            npoints=npoints,
            trigger_type=AcquisitionMaster.HARDWARE,
            prepare_once=prepare_once,
            start_once=start_once,
            ctrl_params=None,
        )

    def __iter__(self):
        if self.npoints > 0:
            for i in range(self.npoints):
                self._nb_point = i
                self.triggers_number = next(self._triggers_number_iter)
                yield self
        else:
            self._nb_point = 0
            while True:
                yield self
                self._nb_point += 1

    def _do_hw_triggering_loop(self):
        if self._trigger_delay:
            gevent.sleep(self._trigger_delay)
        for i in range(self.triggers_number):
            t0 = time.perf_counter()
            self.on_trigger_callback()
            dt = max(self.triggers_delta - (time.perf_counter() - t0), 0)
            gevent.sleep(dt)

    def prepare(self):
        if self._nb_point > 1 and self.prepare_once:
            return

    def start(self):
        if self._nb_point > 1 and self.start_once:
            return

        if self._task:
            raise RuntimeError(
                f"An hardware triggering task is arleady running {self._task}"
            )
        self._task = gevent.spawn(self._do_hw_triggering_loop)

    def stop(self):
        if self._task:
            self._task.kill()

    def trigger(self):
        raise NotImplementedError

    def trigger_ready(self):
        return True

    def wait_ready(self):
        if self._task:
            self._task.join()


class FakeCounter(Counter):
    """Fake Counter 1D associated with FakeController"""

    def __init__(
        self,
        name,
        controller,
        conversion_function=None,
        unit=None,
        data_gen=None,
        data_size=None,
        data_type=None,
    ):

        if data_gen is None:
            data_gen = "random"

        if data_size is None:
            data_size = ()
        elif not isinstance(data_size, (tuple, list)):
            data_size = (int(data_size),)

        if data_type is None:
            data_type = int

        self._data_gen = data_gen
        self._data_size = data_size
        self._data_type = data_type

        super().__init__(name, controller, conversion_function, unit, data_type)

    @property
    def data_gen(self):
        return self._data_gen

    @property
    def data_size(self):
        return self._data_size

    @property
    def data_type(self):
        return self._data_type

    @property
    def dtype(self):
        return self._data_type

    @property
    def shape(self):
        return self._data_size


class FakeController(CounterController):
    """
    Fake controller which can simulates hardware triggering.

    If placed under a FakeAcquisitionCard it will react to signals
    sent by the fake card. On this event the method 'on_trigger_event()'
    is called.

    Default config:
    cfg = {'counters':
            [ {'counter_name':'fake0D', 'data_gen':'random', } ,  # random scalars
              {'counter_name':'fake1D', 'data_gen': 1, 'data_size':1024, } ,  # spectrums of 1024 values, all equal to 1:
              {'counter_name':'fake0D', 'data_gen': 'img_square', } , # pixels of an image of a square
            ], }
    """

    def __init__(self, name, config=None):
        super().__init__(name)

        if config is None:
            config = {
                "counters": [
                    {"counter_name": "fake0D", "data_size": (), "data_gen": "random"},
                    {"counter_name": "fake1D", "data_size": 1024, "data_gen": 1},
                    {"counter_name": "fakePix", "data_gen": "img_square"},
                ]
            }

        self._config = config
        self._trigger_type = TRIGGER_MODE_ENUM.SOFTWARE
        self._recv_triggers = 0

        self.load_config()

    def load_config(self):
        """Create counters with fake data from configuration"""

        for cfg in self._config.get("counters", []):
            self.create_counter(
                FakeCounter,
                name=cfg["counter_name"],
                data_gen=cfg.get("data_gen"),
                data_size=cfg.get("data_size"),
            )

    @property
    def trigger_type(self):
        return self._trigger_type

    @trigger_type.setter
    def trigger_type(self, value):
        if value not in TRIGGER_MODE_ENUM:
            raise ValueError(
                f"invalid trigger type, should be in {list(TRIGGER_MODE_ENUM)}"
            )
        self._trigger_type = value

    def get_acquisition_object(self, acq_params, ctrl_params, parent_acq_params):
        return FakeAcquisitionSlave(self, ctrl_params=ctrl_params, **acq_params)

    def get_default_chain_parameters(self, scan_params, acq_params):

        # Return required parameters
        params = {}
        params["npoints"] = acq_params.get("npoints", scan_params.get("npoints", 1))
        params["count_time"] = acq_params.get(
            "count_time", scan_params.get("count_time", 1)
        )
        params["trigger_type"] = acq_params.get("trigger_type", self._trigger_type)
        params["prepare_once"] = acq_params.get("prepare_once", True)
        params["start_once"] = acq_params.get("start_once", True)
        params["read_all_triggers"] = acq_params.get("read_all_triggers", False)

        return params

    def _prepare_counters_data(self, npoints):
        log_debug(self, "=== FakeController._prepare_counters_data")
        self._cnts_values = {}
        self._last_indexes = {}
        for cnt in self.counters:
            if isinstance(cnt, FakeCounter):
                cnt_values = self._generate_cnt_data(cnt, npoints)
                self._cnts_values[cnt] = cnt_values
                self._last_indexes[cnt] = 0

    def _generate_cnt_data(self, cnt, npoints):

        # TO DO: take in charge the cnt.data_type

        if cnt.data_gen == "img_square":
            w = h = int(npoints**0.5)
            cnt_values = numpy.zeros((h, w), dtype=int)
            cnt_values[1, :] = 1
            cnt_values[h - 2, :] = 1
            cnt_values[:, 1] = 1
            cnt_values[:, w - 2] = 1
            return cnt_values.ravel()

        dim = len(cnt.data_size)
        if dim > 2:
            raise ValueError(f"cannot deal with a dimension of {dim}")

        if dim == 0:
            if cnt.data_gen == "random":
                cnt_values = numpy.random.randint(0, 100, npoints)
            else:
                cnt_values = [
                    int(cnt.data_gen)
                ] * npoints  # an iterator would be much better

        elif dim == 1:
            if cnt.data_gen == "random":
                cnt_values = [
                    numpy.random.randint(0, 100, cnt.data_size[0])
                    for _i in range(npoints)
                ]
            else:
                cnt_value = [cnt.data_gen] * cnt.data_size[0]
                cnt_values = [cnt_value] * npoints  # an iterator would be much better

        elif dim == 2:
            w, h = cnt.data_size
            if cnt.data_gen == "random":
                cnt_values = [
                    numpy.random.randint(0, 100, w * h).reshape(h, w)
                    for _i in range(npoints)
                ]
            else:
                cnt_value = numpy.ones((h, w)) * cnt.data_gen
                cnt_values = [cnt_value] * npoints  # an iterator would be much better

        else:
            raise ValueError(f"cannot deal with a dimension of {dim}")

        return cnt_values

    def _incr_data_index(self, cnt):
        self._last_indexes[cnt] += 1
        if self._last_indexes[cnt] >= len(self._cnts_values[cnt]):
            self._last_indexes[cnt] = 0

    def get_values(self, from_index, *counters):
        """Used when simulating Hardware trigger mode"""
        return [
            self._cnts_values[cnt][from_index : self._recv_triggers] for cnt in counters
        ]

    def read_all(self, *counters):
        """Used when simulating Software trigger mode.

        It returns the counters values read from the pre-built data '_cnts_values'.
        Each read_all() increments '_last_indexes[cnt]' ( looping if index >= len(data) ).
        """
        cnts_value = []
        for cnt in counters:
            idx = self._last_indexes[cnt]
            value = self._cnts_values[cnt][idx]
            cnts_value.append(value)
            self._incr_data_index(cnt)
        return cnts_value

    def on_trigger_event(self):
        log_debug(self, f"=== FakeController.on_trigger_event@{_CLOCK.time()}")
        self._recv_triggers += 1


class FakeAcquisitionSlave(AcquisitionSlave):
    """AcquisitionObject for FakeControllers.

    As an AcquisitionSlave it won't trigger anyone below in the AcqChain
    and self.reading is spwaned at each scan iteration (if not alive already).

    ** In Hardware trigger mode **:
       Designed for devices that accumulate the measurements into a buffer when
       they are periodically triggered by a (fake) hardware signal (usually between 2 scan iterations/steps).

       (reading): self.device is polled until 'npoints' are read for all counters.
       Data packets are read with self.device.get_values(from_index, *cnts).

     ** In software trigger mode **:
        Designed for devices that perform  measurements at each scan iteration only
        (i.e. nothing between 2 scan iterations/steps)

    """

    def __init__(
        self,
        device,
        npoints=1,
        count_time=1,
        trigger_type=None,
        prepare_once=True,
        start_once=False,
        ctrl_params=None,
        read_all_triggers=False,
    ):

        # Use controller trigger_type by default
        if trigger_type is None:
            trigger_type = device.trigger_type

        name = f"{device.name}_FS"
        super().__init__(
            device,
            npoints=npoints,
            trigger_type=trigger_type,
            prepare_once=prepare_once,
            start_once=start_once,
            ctrl_params=ctrl_params,
            name=name,
        )

        self.count_time = count_time
        self.read_all_triggers = read_all_triggers
        self._stop_flag = False

    def _emit_new_data(self, data):
        self.channels.update_from_iterable(data)

    def get_acquisition_metadata(self, timing=None):
        tmp_dict = super().get_acquisition_metadata(timing=timing)
        if timing != self.META_TIMING.PREPARED:
            return tmp_dict
        for cnt in self._counters:
            if isinstance(cnt, HasMetadataForScan):
                mdata = cnt.scan_metadata()
                if mdata is not None:
                    if tmp_dict is None:
                        tmp_dict = dict()
                    deep_update(tmp_dict, mdata)
        return tmp_dict

    def prepare(self):
        self.device._prepare_counters_data(self.npoints)

    def start(self):
        self._nb_acq_points = 0
        self.device._recv_triggers = 0

    def stop(self):
        self._stop_flag = True
        if hasattr(self.device, "disconnect"):
            # In the case of FakeAcquisitionCard
            self.device.disconnect()

    def trigger(self):
        pass

    def reading(self):
        """Reading is always spawn (AcqSlave) by the Scan at each iteration"""

        # WARNING: in HARDWARE trigger mode:
        # reading is spawn (by AcqSlave.start) before the AcqMaster.start.
        # so the while loop should wait for the AcqMaster to start before polling on data.
        # If the data buffer of the slave controller is empty it will works because data polling will not
        # find data, so it wont increment _nb_acq_points.
        # If the data buffer is not empty (full of last acq data) then the polling while find all data and retrieve them!
        # to avoid that, at the begining of the loop, it should wait that its triggering master has started.

        counters = list(self._counters.keys())

        if self.trigger_type == TRIGGER_MODE_ENUM.SOFTWARE:

            cnts_values = [
                counters[i].conversion_function(cnt_values_from_index)
                for i, cnt_values_from_index in enumerate(
                    self.device.read_all(*counters)
                )
            ]

            self._emit_new_data(cnts_values)

        elif self.trigger_type == TRIGGER_MODE_ENUM.HARDWARE:

            while self.device._recv_triggers == 0 and not self._stop_flag:
                gevent.sleep(0.005)

            while (
                not self.npoints or self._nb_acq_points < self.npoints
            ) and not self._stop_flag:

                # FOR EACH COUNTER GET THE LAST MEASUREMENTS FROM LAST INDEX (self._nb_acq_points)
                # ONE MEASUREMENT COULD BE A SPECTRUM
                cnts_values = [
                    counters[i].conversion_function(cnt_values_from_index)
                    for i, cnt_values_from_index in enumerate(
                        self.device.get_values(self._nb_acq_points, *counters)
                    )
                ]

                # CHECK THAT ALL COUNTERS HAS RETURNED THE SAME AMOUT OF MEASUREMENTS
                if not all_equal([len(cnt_values) for cnt_values in cnts_values]):
                    raise RuntimeError("Read data can't have different sizes")

                # CHECK THAT THE NUMBER OF MEASUREMENTS OF THE FIRST COUNTER IS > 0
                nb_values = len(cnts_values[0])
                if nb_values > 0:
                    self._nb_acq_points += nb_values
                    self._emit_new_data(cnts_values)

                gevent.sleep(0.02)


class FakeAcquisitionCard(FakeController):

    """
    Simulates an acquisition card that sends signals to
    registered devices and can read data on its channels.

    A device is registered by giving a callback that will by called
    when the hardware trigger is emitted.

    A FakeAcquisitionCardMaster on top of this object will automatically register
    any slave device under him if it has a 'device.on_trigger_event' method.

    ** Like a Musst card **:

    - a custom program can be defined by overloading the method
    'program(nb_triggers, delta_time)'

    - channels can be configured and can be read during the program.
      To simulate the reading of the card channels, a callback is
      associated to the channel number.

      ex: cfg = {'channels':[ {'channel':1,'read_func':read_fast_motor,},
                              {'channel':2,'read_func':read_slow_motor,},
                            ] }

      Counters are automatically created for all declared channels.
      The counter values are obtained from the internal data buffer (see STORE()).

    To write a custom program, basic methods are provided:

    - ATRIG(): send a signal (SIG_HARD_TRIG) to trigger registered callbacks
    - CLEAN(): empty the internal data buffer
    - STORE(): read the card channels values (using callbacks associated to channels)
      and store them in the internal data buffer:
      => '_channels_values[cname]' = [measure1, measure2, ...]


    """

    SIG_HARD_TRIG = "HARD_TRIG"

    def __init__(self, name, config):
        super().__init__(name, config)

        # monitor the musst-like program
        self._prg_task = None
        self._abort = False

        # register devices callbacks that will by called
        # when sending the fake hardware triggers
        self._registered_callbacks = []

        # buffer for data filled by program
        self._channels_values = {}

        self.load_config()

    def __del__(self):
        try:
            self.disconnect()
        except Exception:
            pass

    def disconnect(self):
        self._clean_cb_register()

    def load_config(self):
        self._card_channels = {}
        for cfg in self._config.get("channels"):
            chnum = cfg["channel"]
            rfunc = cfg["read_func"]
            cname = f"ch{chnum}"
            self._card_channels[cname] = (chnum, rfunc)
            self.create_counter(Counter, name=cname)

    def register_to_trigger_event(self, device):
        if hasattr(device, "on_trigger_event"):
            cb = device.on_trigger_event
            self._registered_callbacks.append(cb)
            levent.connect(self, self.SIG_HARD_TRIG, cb)
            log_debug(self, f"=== FakeAcquisitionCard: registering {device.name}")

    def run_program(self, nb_triggers, delta_time):
        if not self._prg_task:
            self._abort = False
            self.CLEAN()
            self._prg_task = gevent.spawn(self.program, nb_triggers, delta_time)
        else:
            raise RuntimeError("program already running")

    def program(self, nb_triggers, delta_time):
        """Here you can simulate a kind of musst program
        Overload to write custom program.
        This method is called by the associated AcquitionMaster (see self.get_acquisition_object).

        This basic one just send 'nb_triggers' fake hardware triggers
        via registered callbacks each 'delta_time'.

        """
        log_debug(self, "=== PROGRAM STARTS", nb_triggers, delta_time)
        _CLOCK.reset()

        for i in range(nb_triggers):
            log_debug(self, f"=== CARD PROG: STORE AND ATRIG @trig {i}")
            self.STORE()
            self.ATRIG()
            gevent.sleep(delta_time)
            if self._abort:
                log_debug(self, "=== ABORTING PROGRAM")
                break

        log_debug(self, "=== PROGRAM FINISHED")

    def abort(self):
        if self.is_running:
            self._abort = True

    @property
    def is_running(self):
        return self._prg_task

    def ATRIG(self):
        self._send_trigger(self.SIG_HARD_TRIG)

    def STORE(self):
        for cname in self._card_channels:
            _chnum, rfunc = self._card_channels[cname]
            chvalue = float(rfunc())
            if self._channels_values.get(cname) is None:
                self._channels_values[cname] = [chvalue]
            else:
                self._channels_values[cname].append(chvalue)

    def CLEAN(self):
        self._channels_values = {}

    def get_values(self, from_index, *counters):
        cnts_values = []
        for cnt in counters:
            cnt_values = self._channels_values.get(cnt.name)
            if cnt_values is None:
                return [[]]
            cnts_values.append(cnt_values)

        dmin = min([len(values) for values in cnts_values])

        cnts_values = [values[from_index:dmin] for values in cnts_values]
        return cnts_values

    def read_all(self, *counters):
        self.STORE()
        cnts_value = []
        for cnt in counters:
            value = self._channels_values[cnt.name].pop(-1)
            cnts_value.append(value)

        return cnts_value

    def _send_trigger(self, signal):
        levent.send(self, signal)

    def _clean_cb_register(self):
        for cb in self._registered_callbacks:
            levent.disconnect(self, self.SIG_HARD_TRIG, cb)

    def _prepare_counters_data(self, npoints):
        pass


class FakeAcquisitionCardMaster(AcquisitionMaster):
    """
    AcquisitionMaster for the FakeAcquisitionCard to manage the card program that
    will sends fake hardware triggers and read/store the card channels data.

    This object is only necessary when working with devices in hardware trigger mode
    that preform measurements between scan iterations (like with a MotoMaster/Continuous scans)

    """

    def __init__(
        self,
        device,
        npoints=1,
        count_time=None,
        trigger_type=TRIGGER_MODE_ENUM.SOFTWARE,
        prepare_once=True,
        start_once=False,
        ctrl_params=None,
    ):

        super().__init__(
            device,
            npoints=npoints,
            trigger_type=trigger_type,
            prepare_once=prepare_once,
            start_once=start_once,
            ctrl_params=ctrl_params,
        )

        self._count_time = count_time
        self._start_epoch = None
        self._running_state = False

    def prepare(self):
        self.device.register_to_trigger_event(self.device)
        for slave in self.slaves:
            self.device.register_to_trigger_event(slave.device)

    def start(self):
        self.device.run_program(self.npoints, self._count_time)

    def stop(self):
        if self.device.is_running:
            self.device.abort()
            self.wait_ready()

        self.device.disconnect()

    def wait_ready(self):
        while self.device.is_running:
            gevent.sleep(0.02)


class XYSampleData:
    """
    Simulates a 2D sample.

    use `SampleStageDiode` devices to read value at position (x,y).

    Two motors (xmot, ymot) can be specified so that a call to `read_diode` will retrieve
    the pixel value at motors positions: (xmot.position, ymot.position).

    This `SampleStageDiode` handle the axis-to-pixel transformation.
    """

    def __init__(
        self,
        xmot=None,
        ymot=None,
        sample_stage_diodes: list[SampleStageDiode] = None,
    ):
        self._sample_stage_diodes: list[SampleStageDiode] = sample_stage_diodes

        self._xmot = xmot
        self._ymot = ymot

        self._xpos = 0.0
        self._ypos = 0.0

        if xmot:
            levent.connect(self._xmot, "position", self._on_update_xpos)
        if ymot:
            levent.connect(self._ymot, "position", self._on_update_ypos)

    def disconnect(self):
        if self._xmot:
            levent.disconnect(self._xmot, "position", self._on_update_xpos)
        if self._ymot:
            levent.disconnect(self._ymot, "position", self._on_update_ypos)

    def __del__(self):
        try:
            self.disconnect()
        except Exception:
            pass

    def get_xpos(self):
        return self._xpos

    def get_ypos(self):
        return self._ypos

    def nb_diodes(self):
        return len(self._sample_stage_diodes)

    def read_diode(self, i):
        """Read a diode from it's id"""
        x = self.get_xpos()
        y = self.get_ypos()
        return self._sample_stage_diodes[i].read_signal_from_pos(y, x)

    def _on_update_xpos(self, value):
        self._xpos = value

    def _on_update_ypos(self, value):
        self._ypos = value


def create_sample_stage_diode(simdatapath, scale) -> SampleStageDiode:
    """Create a SampleStageDiode with the path filename provided.

    If None, create a synthetic image.
    """
    if simdatapath is None:
        simdata = test_image()
    else:
        simdata = None
    config = {
        "axis1": None,
        "axis2": None,
        "scale": scale,
        "data": simdata,
        "data_filename": simdatapath,
    }
    ss = SampleStageDiode(name="simdata", config=config)
    return ss


def create_fake_card_from_sample_stage_diodes(
    name: str,
    slow_motor: Axis,
    fast_motor: Axis,
    sample_stage_diodes: list[SampleStageDiode],
    simdatapath: str = None,
    scale: float = None,
    imshow=False,
) -> FakeAcquisitionCard:
    sample_stage_diodes = list(sample_stage_diodes)
    if simdatapath or len(sample_stage_diodes) == 0:
        ss = create_sample_stage_diode(simdatapath, scale)
        sample_stage_diodes.append(ss)

    if imshow:
        for d in sample_stage_diodes:
            d.show()

    data = XYSampleData(fast_motor, slow_motor, sample_stage_diodes=sample_stage_diodes)

    channels = [
        {"channel": 1, "read_func": data.get_xpos},
        {"channel": 2, "read_func": data.get_ypos},
    ]
    for diodeid in range(data.nb_diodes()):
        channels.append(
            {
                "channel": diodeid + 3,
                "read_func": functools.partial(data.read_diode, diodeid),
            }
        )

    class Card(FakeAcquisitionCard):
        def disconnect(self):
            super(Card, self).disconnect()
            data.disconnect()

    card_config = {"channels": channels}
    simu_card = Card(name, card_config)
    return simu_card


def simu_mesh(
    fast_motor,
    slow_motor,
    *counters,
    size=(9, 9),
    start=(0, 0),
    scale=0.1,
    backnforth=True,
    imshow=True,
    simdatapath=None,
):
    """Simulates a 2D mesh scan.

    - scale: a scale factor to apply to the 'sample image' (read from 'simu_card.ch3') (see scatter plot in Flint)
    - imshow: show the 'sample image' (the data source for 'simu_card.ch3').
      This image is the expected result of a scatter plot in Flint with
    { X: 'axis:roby', Y: 'axis:robz', V: 'simu_card.ch3' }

    """
    # --- fake controller
    fake_ctrl = FakeController("fake_ctrl")

    ssdiodes = [c for c in counters if isinstance(c, SampleStageDiode)]
    counters = [c for c in counters if not isinstance(c, SampleStageDiode)]
    simu_card = create_fake_card_from_sample_stage_diodes(
        name="simu_card",
        slow_motor=slow_motor,
        fast_motor=fast_motor,
        sample_stage_diodes=ssdiodes,
        simdatapath=simdatapath,
        scale=scale,
        imshow=imshow,
    )
    # --- scan
    xstop = start[0] + size[0] * 1
    ystop = start[1] + size[1] * 1
    s = amesh(
        fast_motor,
        start[0],
        xstop,
        size[0] - 1,
        slow_motor,
        start[1],
        ystop,
        size[1] - 1,
        0.01,
        *counters,
        fake_ctrl,
        simu_card,
        run=False,
        backnforth=backnforth,
    )
    s.run()
    return s


def simu_lscan(
    fast_motor,
    x_start,
    x_stop,
    x_intervals,
    count_time,
    *counters,
    save=True,
    save_images=False,
    backnforth=False,
    scale=0.1,
    imshow=False,
    simdatapath=None,
):
    """
    Simulates a fast scan (continuous scan along X).

    - scale: a scale factor to apply to the 'sample image' (read from 'simu_card.ch3') (see scatter plot in Flint)
    - imshow: show the 'sample image' (the data source for 'simu_card.ch3').

    usage example:
      low res scan: s=simu_lscan(roby,5,20,100,0.1, backnforth=False, scale=0.2)
      mid res scan: s=simu_lscan(roby,10,40,100,0.1, backnforth=False, scale=0.4)
    """

    # --- init scan parameters ---
    x_npoints = x_intervals + 1
    x_travel_time = count_time * x_intervals
    xoffset = 0
    undershoot = None

    # --- build the acquisition chain ---
    chain = AcquisitionChain()
    fast_master = MotorMaster(
        fast_motor,
        x_start,
        x_stop,
        time=x_travel_time,
        undershoot=undershoot,
        backnforth=backnforth,
    )

    # --- add a fake acquisition card that will send fake hardware triggers to children

    ssdiodes = [c for c in counters if isinstance(c, SampleStageDiode)]
    counters = [c for c in counters if not isinstance(c, SampleStageDiode)]
    simu_card = create_fake_card_from_sample_stage_diodes(
        name="simu_card",
        slow_motor=None,
        fast_motor=fast_motor,
        sample_stage_diodes=ssdiodes,
        simdatapath=simdatapath,
        scale=scale,
        imshow=imshow,
    )
    simu_card.trigger_type = TRIGGER_MODE_ENUM.HARDWARE  # TO DO HANDLE THIS BETTER

    simu_master = FakeAcquisitionCardMaster(
        simu_card,
        npoints=x_npoints,
        count_time=count_time,
        trigger_type=TRIGGER_MODE_ENUM.HARDWARE,
        prepare_once=True,
        start_once=False,
        ctrl_params=None,
    )

    chain.add(fast_master, simu_master)
    counters = list(counters)
    counters.append(simu_card)
    builder = ChainBuilder(counters)
    for node in builder.get_nodes_by_controller_type(FakeController):
        if node.controller.trigger_type == TRIGGER_MODE_ENUM.HARDWARE:
            node.set_parameters(
                acq_params={"npoints": x_npoints, "count_time": count_time}
            )
            chain.add(simu_master, node)

    total_points = x_npoints
    simu_card._counters["ch1"].fullname
    fast_axis_name = simu_card._counters["ch1"].fullname

    scan_info_dict = {
        "npoints": total_points,
        "type": "lscan",
        "count_time": count_time,
        "data_dim": 1,
        "start": x_start - xoffset,
        "stop": x_stop - xoffset,
    }

    scan_info = ScanInfo()
    scan_info.update(scan_info_dict)

    scan_info.set_channel_meta(
        fast_axis_name,
        # The group have to be the same for all this channels
        group="g1",
        # This is the fast axis
        axis_id=0,
        # In forth direction only
        axis_kind="forth",
        # The grid have to be specified
        start=x_start - xoffset,
        stop=x_stop - xoffset,
        axis_points=x_npoints,
        # Optionally the full number of points can be specified
        points=total_points,
    )

    cnt_name = "simu_card:ch3"  # 'fake_ctrl:fake0D'
    scan_info.set_channel_meta(cnt_name, group="g1")

    command_line = (
        f"lscan {fast_motor.name} {x_start} {x_stop} {x_intervals} {count_time}"
    )
    sc = Scan(
        chain,
        name=command_line,
        scan_info=scan_info,
        save=save,
        save_images=save_images,
        scan_saving=None,
    )
    sc.run()
    return sc


def simu_l2scan(
    fast_motor,
    x_start,
    x_stop,
    x_intervals,
    slow_motor,
    y_start,
    y_stop,
    y_intervals,
    count_time,
    *counters,
    save=True,
    save_images=False,
    backnforth=False,
    scale=0.1,
    imshow=False,
    simdatapath=None,
):
    """
    Simulates a Zap scan (continuous scan along X and step_by_step along Y).

    - scale: a scale factor to apply to the 'sample image' (read from 'simu_card.ch3') (see scatter plot in Flint)
    - imshow: show the 'sample image' (the data source for 'simu_card.ch3').
      This image is the expected result of a scatter plot in Flint with
      { X: 'simu_card.ch1', Y: 'simu_card.ch2', V: 'simu_card.ch3' }


    usage example:
      low res scan: s=simu_l2scan(roby,5,20,100,robz,10,20,10,0.1, backnforth=False, scale=0.2)
      mid res scan: s=simu_l2scan(roby,10,40,100,robz,20,40,20,0.1, backnforth=False, scale=0.4)
    """
    # --- init scan parameters ---
    x_npoints = x_intervals + 1
    y_npoints = y_intervals + 1

    x_travel_time = count_time * x_intervals
    xoffset = 0
    undershoot = None

    # --- build the acquisition chain ---
    chain = AcquisitionChain()
    fast_master = MotorMaster(
        fast_motor,
        x_start,
        x_stop,
        time=x_travel_time,
        undershoot=undershoot,
        backnforth=backnforth,
    )

    slow_master = LinearStepTriggerMaster(y_npoints, slow_motor, y_start, y_stop)

    chain.add(slow_master, fast_master)

    # --- add a fake acquisition card that will send fake hardware triggers to children

    ssdiodes = [c for c in counters if isinstance(c, SampleStageDiode)]
    counters = [c for c in counters if not isinstance(c, SampleStageDiode)]
    simu_card = create_fake_card_from_sample_stage_diodes(
        name="simu_card",
        slow_motor=slow_motor,
        fast_motor=fast_motor,
        sample_stage_diodes=ssdiodes,
        simdatapath=simdatapath,
        scale=scale,
        imshow=imshow,
    )
    simu_card.trigger_type = TRIGGER_MODE_ENUM.HARDWARE  # TO DO HANDLE THIS BETTER

    simu_master = FakeAcquisitionCardMaster(
        simu_card,
        npoints=x_npoints,
        count_time=count_time,
        trigger_type=TRIGGER_MODE_ENUM.HARDWARE,
        prepare_once=True,
        start_once=False,
        ctrl_params=None,
    )

    chain.add(fast_master, simu_master)

    # ------ BUILDER for counters ----------------------------------------------

    # --- add a fake controller
    cfg = {
        "counters": [
            {"counter_name": "fake0D", "data_size": (), "data_gen": "random"},
            {"counter_name": "fake1D", "data_size": 1024, "data_gen": 1},
        ]
    }

    fake_ctrl = FakeController("fake_ctrl", cfg)
    fake_ctrl.trigger_type = TRIGGER_MODE_ENUM.HARDWARE  # TO DO HANDLE THIS BETTER

    # --- introspect
    counters = list(counters)
    counters.append(fake_ctrl)
    counters.append(simu_card)
    builder = ChainBuilder(counters)

    for node in builder.get_nodes_by_controller_type(FakeController):
        if node.controller.trigger_type == TRIGGER_MODE_ENUM.HARDWARE:
            node.set_parameters(
                acq_params={"npoints": x_npoints, "count_time": count_time}
            )
            chain.add(simu_master, node)
        else:
            node.set_parameters(
                acq_params={"npoints": y_npoints, "count_time": count_time}
            )
            chain.add(slow_master, node)

    for node in builder.get_nodes_by_controller_type(SamplingCounterController):
        node.set_parameters(acq_params={"npoints": y_npoints, "count_time": count_time})
        chain.add(slow_master, node)

    total_points = x_npoints * y_npoints

    simu_card._counters["ch1"].fullname

    fast_axis_name = simu_card._counters["ch1"].fullname
    slow_axis_name = simu_card._counters["ch2"].fullname

    scan_info_dict = {
        "npoints": total_points,
        "npoints1": x_npoints,
        "npoints2": y_npoints,
        "type": "l2scan",
        "count_time": count_time,
        "data_dim": 2,
        "start": [y_start, x_start - xoffset],
        "stop": [y_stop, x_stop - xoffset],
    }

    scan_info = ScanInfo()
    scan_info.update(scan_info_dict)

    scan_info.set_channel_meta(
        fast_axis_name,
        # The group have to be the same for all this channels
        group="g1",
        # This is the fast axis
        axis_id=0,
        # In forth direction only
        axis_kind="forth",
        # The grid have to be specified
        start=x_start - xoffset,
        stop=x_stop - xoffset,
        axis_points=x_npoints,
        # Optionally the full number of points can be specified
        points=total_points,
    )

    scan_info.set_channel_meta(
        slow_axis_name,
        group="g1",
        axis_id=1,
        axis_kind="forth",
        start=y_start,
        stop=y_stop,
        axis_points=y_npoints,
        points=total_points,
    )

    cnt_name = "simu_card:ch3"  # 'fake_ctrl:fake0D'
    scan_info.set_channel_meta(cnt_name, group="g1")

    # Request a specific scatter to be displayed
    scan_info.add_scatter_plot(x=fast_axis_name, y=slow_axis_name, value=cnt_name)

    command_line = f"l2scan {fast_motor.name} {x_start} {x_stop} {x_intervals} "
    command_line += f"{slow_motor.name} {y_start} {y_stop} {y_intervals} {count_time}"

    sc = Scan(
        chain,
        name=command_line,
        scan_info=scan_info,
        save=save,
        save_images=save_images,
        scan_saving=None,
    )

    sc.run()
    return sc
