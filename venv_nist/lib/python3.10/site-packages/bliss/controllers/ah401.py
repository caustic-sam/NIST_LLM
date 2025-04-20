# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.


import numpy
import functools
import enum

from bliss import global_map
from bliss.comm.util import get_comm, TCP
from bliss.common.utils import autocomplete_property
from bliss.common.logtools import log_debug
from bliss.config.settings import HashObjSetting
from bliss.common.counter import SamplingCounter, SamplingMode
from bliss.controllers.bliss_controller import BlissController
from bliss.controllers.counter import (
    SamplingCounterController,
    IntegratingCounterAcquisitionSlave,
)
from bliss.scanning.acquisition.counter import SamplingCounterAcquisitionSlave


@enum.unique
class CountingMode(enum.IntEnum):
    """CountingMode modes:
    * STD:
    * AUTO:
    """

    STD = enum.auto()
    AUTO = enum.auto()


@enum.unique
class TriggerMode(enum.IntEnum):
    """CountingMode modes:
    * SOFTWARE: 0
    * HARDWARE: 1
    """

    SOFTWARE = 0
    HARDWARE = 1


def lazy_init(func):
    @functools.wraps(func)
    def func_wrapper(self, *args, **kwargs):
        if self.comm is None:
            self._initialize()
        return func(self, *args, **kwargs)

    return func_wrapper


class Ah401Device:

    """Controller class for the AH401 models B and D from CAEN (www.caenels.com).

    AH401 is a 4-channel, 20-bit resolution, low noise, high performance charge-integration picoammeter.

    It is composed of a particular charge-integration input stage for low current sensing,
    coupled with a 20-bit sigma-delta ADC converter including a noise reduction digital filter.
    Model D is able to use different integration ranges for channels 1-2 and channels 3-4,
    whereas model B uses same range for all channels (see self.scale_range).

    The AH401D performs current measurements from 50 pA (with a resolution of 50 aA) up to 2.0 μA (with a resolution of 2.0 pA),
    with integration times ranging from 1ms up to 1s. Moreover, each input channel has two parallel integrator stages,
    so that the current-to-voltage conversion can be performed continuously also during the ADC conversion,
    avoiding any dead time in the data throughput (see self.half_mode).

    It also performs digitization of the acquired current data, thus strongly minimizing the transmission length of
    analog current signals from the detector and providing directly digital data output, which is easy to transmit
    over long distances without any noise pick-up.

    The AH401D is housed in a light, practical and extremely compact metallic box that can be placed as close as
    possible to the current source (detector) in order to reduce cable lengths and minimize possible noise pick-up
    during the propagation of very low intensity analog signals. It is specially suited for applications where multi-channel
    simultaneous acquisitions are required, a typical application being the currents readout from 4-quadrant photodiodes
    used to monitor X-ray beam displacements. Low temperature drifts, good linearity and the very low intrinsic noise of
    the AH401D allow obtaining very high precision current measurements.

    The AH401D uses a standard Ethernet TCP communication layer with the DEFAULT_PORT = 10001.

    """

    # RANGE TO FULL SCALE IN COULOMBS
    FULL_SCALE_RANGE = {
        0: (2, 1e-9, "nC"),
        1: (50, 1e-12, "pC"),
        2: (100, 1e-12, "pC"),
        3: (150, 1e-12, "pC"),
        4: (200, 1e-12, "pC"),
        5: (250, 1e-12, "pC"),
        6: (300, 1e-12, "pC"),
        7: (350, 1e-12, "pC"),
    }

    UNIT_FACTOR = {"µA": 1e-6, "nA": 1e-9, "pA": 1e-12}

    DEFAULT_PORT = 10001
    DEFAULT_OFFSET = 4096
    FULL_SCALE_MAX = 1048575
    TIME_FACTOR = 10000
    VERSION = "AH401D"
    WEOL = "\r"
    REOL = "\r\n"

    CMD2PARAM = {
        "ACQ": ("ON", "OFF"),
        "BDR": (9600, 19200, 38400, 57600, 115200, 230400, 460800, 921600),
        "BIN": ("ON", "OFF"),
        "GET": (),
        "HLF": ("ON", "OFF"),
        "ITM": (1e-3, 1),  # in seconds
        "NAQ": (0, 20000000),  # 0=OFF
        "RNG": (
            0,
            7,
        ),  # ch1-2 and ch3-4 can have different ranges X and Y, passing 'XY' instead of 'Z'
        "SUM": ("ON", "OFF"),
        "TRG": ("ON", "OFF"),
        "VER": (),
    }

    def __init__(self, comconf):
        self.conf = comconf
        self.comm = None
        self.saturation_warnings = True
        self._integration_time = None
        self._trig_mode = TriggerMode.SOFTWARE
        self._data_offset = numpy.array([self.DEFAULT_OFFSET] * 4)
        self._fsrange12 = None
        self._fsrange34 = None
        self._fsrval12 = None
        self._fsrval34 = None
        self._acquiring = False
        self._bin_data_len = 12
        self._acq_stop_retry = 0

    def __del__(self):
        self.__close__()

    def __close__(self):
        if self._acquiring:
            self.acquistion_stop()
        self._close_com()

    def _close_com(self):
        if self.comm is not None:
            self.comm.close()
            self.comm = None

    def _init_com(self):
        """Initialize communication or reset if already connected"""
        self._close_com()  # close if already opened
        self.comm = get_comm(
            self.conf, ctype=TCP, eol=self.REOL, port=self.DEFAULT_PORT
        )

    def _initialize(self):
        """Initialize/reset communication layer and synchronize with hardware"""
        self._init_com()

        # update internal params
        self.integration_time
        self.sum_mode

        # force bin mode always
        self.send_cmd("BIN", "ON")

        if len(self.scale_range) == 2:
            self._model = "D"
        else:
            self._model = "B"

    @property
    def data_offset(self):
        return self._data_offset

    @data_offset.setter
    def data_offset(self, value):
        if not isinstance(value, (list, numpy.ndarray)):
            raise ValueError("value must be a list of 4 items (one for each channel)")
        self._data_offset = numpy.array(value)

    @property
    def baudrate(self):
        return int(self.send_cmd("BDR", "?"))

    @baudrate.setter
    def baudrate(self, value):
        if value not in self.CMD2PARAM["BDR"]:
            raise ValueError(f"baudrate must be in {self.CMD2PARAM['BDR']}")
        self.send_cmd("BDR", int(value))

    @property
    def half_mode(self):
        """
        The purpose of this mode is to select whether to process data from both integrator circuits (i.e. maximum speed, half_mode OFF)
        or only from one integrator circuit (i.e. best noise performance, half_mode ON) of the AH401D.

        * If Half_mode is disabled (OFF):
            The AH401D performs the current-to-voltage integration, continuously in time, using both channels (A and B) in parallel.
            when one of the integrator A is digitized by the ADC, the input channel is switched to the other integrator circuit (i.e. B)
            and the input current can thus be continuously integrated.
            At the end of the integration time, also the second integrator (i.e. B) is disconnected from the input and the ADC can start the
            digitization of the integrated current from B. At the same time, the input is connected to the previous integrator (i.e. A)
            and therefore the current can continue to be integrated by A.
            This sequence is continuously repeated as long as the AH401D is acquiring.
            Dead time in the data throughput is thus avoided and the sampled values are continuously sent to the host.
            This mode of operation (default) is useful when the maximum sampling rate is required since at the end of each
            integration cycle a digitized data set is sent to the host PC.
            The drawback is a slightly higher noise level on the sampled values due to the integrator capacitors mismatch between A and B
            and to the charge injection introduced by the internal switches.

        * If Half_mode is enabled (ON):
            If lowest noise performance is of uttermost importance, the half mode mode must always be enabled.
            In this operation mode only the integrated current of one integrator (i.e. A) is sampled, digitized and sent to the host.
            Using only and always the same integrator any capacitors mismatch and charge injection is avoided and the best signal to noise ratio is achieved.
            The operating sequence of this mode is as follow: the current is integrated on integrator A for the integration time (i.e. 100ms),
            then the value is digitized and sent to the host.
            During the following integration time (i.e. 100ms) no data is digitized (the value on the integrator B is discarded) and then the sequence repeats itself.
            Therefore a set of data is sent to the host every two integration times (i.e. 200ms).
            The drawback of this mode is that only “half” sampled values are sent to the host and hence the sampling rate is halved.

        Please note that the data rate throughput is directly related to the integration time and the “half mode” selection.
        For example, setting the integration time to 10ms and the “half mode” to False (disabled) generates a data stream at 100Hz.
        Whereas, setting the integration time to 10ms and the “half mode” to True (enabled), generates a data stream at 50Hz.
        """

        if self.send_cmd("HLF", "?") == "ON":
            return True
        return False

    @half_mode.setter
    def half_mode(self, enable):
        """
        The purpose of this mode is to select whether to process data from both integrator circuits (i.e. maximum speed, half_mode OFF)
        or only from one integrator circuit (i.e. best noise performance, half_mode ON) of the AH401D.

        * If Half_mode is disabled (OFF):
            The AH401D performs the current-to-voltage integration, continuously in time, using both channels (A and B) in parallel.
            when one of the integrator A is digitized by the ADC, the input channel is switched to the other integrator circuit (i.e. B)
            and the input current can thus be continuously integrated.
            At the end of the integration time, also the second integrator (i.e. B) is disconnected from the input and the ADC can start the
            digitization of the integrated current from B. At the same time, the input is connected to the previous integrator (i.e. A)
            and therefore the current can continue to be integrated by A.
            This sequence is continuously repeated as long as the AH401D is acquiring.
            Dead time in the data throughput is thus avoided and the sampled values are continuously sent to the host.
            This mode of operation (default) is useful when the maximum sampling rate is required since at the end of each
            integration cycle a digitized data set is sent to the host PC.
            The drawback is a slightly higher noise level on the sampled values due to the integrator capacitors mismatch between A and B
            and to the charge injection introduced by the internal switches.

        * If Half_mode is enabled (ON):
            If lowest noise performance is of uttermost importance, the half mode mode must always be enabled.
            In this operation mode only the integrated current of one integrator (i.e. A) is sampled, digitized and sent to the host.
            Using only and always the same integrator any capacitors mismatch and charge injection is avoided and the best signal to noise ratio is achieved.
            The operating sequence of this mode is as follow: the current is integrated on integrator A for the integration time (i.e. 100ms),
            then the value is digitized and sent to the host.
            During the following integration time (i.e. 100ms) no data is digitized (the value on the integrator B is discarded) and then the sequence repeats itself.
            Therefore a set of data is sent to the host every two integration times (i.e. 200ms).
            The drawback of this mode is that only “half” sampled values are sent to the host and hence the sampling rate is halved.

        Please note that the data rate throughput is directly related to the integration time and the “half mode” selection.
        For example, setting the integration time to 10ms and the “half mode” to False (disabled) generates a data stream at 100Hz.
        Whereas, setting the integration time to 10ms and the “half mode” to True (enabled), generates a data stream at 50Hz.
        """

        if bool(enable):
            self.send_cmd("HLF", "ON")
        else:
            self.send_cmd("HLF", "OFF")

    @property
    def integration_time(self):
        """get integration time in seconds"""
        self._integration_time = float(self.send_cmd("ITM", "?")) / self.TIME_FACTOR
        return self._integration_time

    @integration_time.setter
    def integration_time(self, value):
        """set integration time in seconds"""
        if value < self.CMD2PARAM["ITM"][0] or value > self.CMD2PARAM["ITM"][1]:
            raise ValueError(
                f"integration time must be in range {self.CMD2PARAM['ITM']} seconds"
            )

        raw_time = int(value * self.TIME_FACTOR)
        self.send_cmd("ITM", raw_time)
        self._integration_time = float(value)

    @property
    def sample_number(self):
        """
        The purpose of the sample_number is to define a fixed number of samples to acquire after starting an acquisition with
        the 'acquistion_start' command. Once the samples are acquired the acquisition stops automatically.
        The sample number should be in range [1, 2e7] or set to 0 to disable this behavior and allow continous data acquisition.
        If a number of acquisitions larger than 4096 is set, the 'sum_mode' is automatically disabled.
        """
        return int(self.send_cmd("NAQ", "?"))

    @sample_number.setter
    def sample_number(self, value):
        """
        The purpose of the sample_number is to define a fixed number of samples to acquire after starting an acquisition with
        the 'acquistion_start' command. Once the samples are acquired the acquisition stops automatically.
        The sample number should be in range [1, 2e7] or set to 0 to disable this behavior and allow continous data acquisition.
        If a number of acquisitions larger than 4096 is set, the 'sum_mode' is automatically disabled.
        """
        if value < self.CMD2PARAM["NAQ"][0] or value > self.CMD2PARAM["NAQ"][1]:
            raise ValueError(f"sample number must be in range {self.CMD2PARAM['NAQ']}")
        self.send_cmd("NAQ", int(value))
        self.sum_mode  # update sum mode

    @property
    def scale_range(self):
        """Get full scale range (charge integration range).
        If returned value has 2 digits, the first digit is the range of channels 1-2,
        and second digit the range of channel 3-4.
        """
        fsr = self.send_cmd("RNG", "?")
        if len(fsr) == 1:
            ifsr = int(fsr)
            self._fsrange12 = self._fsrange34 = ifsr
            self._fsrval12 = self._fsrval34 = (
                self.FULL_SCALE_RANGE[ifsr][0] * self.FULL_SCALE_RANGE[ifsr][1]
            )
            return fsr
        elif len(fsr) == 2:
            self._fsrange12 = int(fsr[0])
            self._fsrange34 = int(fsr[1])
            self._fsrval12 = (
                self.FULL_SCALE_RANGE[self._fsrange12][0]
                * self.FULL_SCALE_RANGE[self._fsrange12][1]
            )
            self._fsrval34 = (
                self.FULL_SCALE_RANGE[self._fsrange34][0]
                * self.FULL_SCALE_RANGE[self._fsrange34][1]
            )
            return fsr
        else:
            raise ValueError(f"cannot read full scale range from '{fsr}'")

    @scale_range.setter
    def scale_range(self, value):
        """Set full scale range (charge integration range) (as string).
        If value has 2 digits, the first digit is the range applied to channels 1-2,
        and second digit the range applied to channel 3-4.
        If value has a single digit, it is applied to all channels.
        """

        value = str(value)

        if len(value) == 2:
            val1 = int(value[0])
            val2 = int(value[1])

            if val1 < self.CMD2PARAM["RNG"][0] or val1 > self.CMD2PARAM["RNG"][1]:
                raise ValueError(
                    f"the first digit of the scale range number must be in range {self.CMD2PARAM['RNG']}"
                )
            if val2 < self.CMD2PARAM["RNG"][0] or val2 > self.CMD2PARAM["RNG"][1]:
                raise ValueError(
                    f"the second digit of the scale range number must be in range {self.CMD2PARAM['RNG']}"
                )

            self.send_cmd("RNG", value)
            self._fsrange12 = val1
            self._fsrange34 = val2
            self._fsrval12 = (
                self.FULL_SCALE_RANGE[self._fsrange12][0]
                * self.FULL_SCALE_RANGE[self._fsrange12][1]
            )
            self._fsrval34 = (
                self.FULL_SCALE_RANGE[self._fsrange34][0]
                * self.FULL_SCALE_RANGE[self._fsrange34][1]
            )

        elif len(value) == 1:
            val1 = int(value)
            if val1 < self.CMD2PARAM["RNG"][0] or val1 > self.CMD2PARAM["RNG"][1]:
                raise ValueError(
                    f"scale range number must be in range {self.CMD2PARAM['RNG']}"
                )

            self.send_cmd("RNG", value)
            self._fsrange12 = self._fsrange34 = val1
            self._fsrval12 = self._fsrval34 = (
                self.FULL_SCALE_RANGE[val1][0] * self.FULL_SCALE_RANGE[val1][1]
            )

        else:
            raise ValueError(
                f"full scale range number must have one or two digits, not '{value}'"
            )

    @property
    def sum_mode(self):
        """
        The purpose of this mode is to add the values of “N” data samples configured with the 'sample_number' command
        and hence to get a single channel value representing the summed samples.
        In order to avoid data overflow, the sum mode cannot be enabled if the number of acquisitions
        set via the 'sample_number' command is larger than 4096.
        """
        if self.send_cmd("SUM", "?") == "ON":
            self._sum_mode = True
            self._bin_data_len = 16
        else:
            self._sum_mode = False
            self._bin_data_len = 12
        return self._sum_mode

    @sum_mode.setter
    def sum_mode(self, enable):
        """
        The purpose of this mode is to add the values of “N” data samples configured with the 'sample_number' command
        and hence to get a single channel value representing the summed samples.
        In order to avoid data overflow, the sum mode cannot be enabled if the number of acquisitions
        set via the 'sample_number' command is larger than 4096.
        """
        if bool(enable):
            if self.sample_number > 4096:
                raise ValueError(
                    "Cannot enable the sum mode when sample_number is greater than 4096"
                )
            self.send_cmd("SUM", "ON")
            self._sum_mode = True
            self._bin_data_len = 16
        else:
            self.send_cmd("SUM", "OFF")
            self._sum_mode = False
            self._bin_data_len = 12

    @property
    def trigger_mode(self):
        """
        Return the current trigger mode: [SOFTWARE, HARDWARE]

        SOFTWARE:

            When staring an acquistion with the 'acquistion_start' command, the Ah401 starts to acquire data continuously.

            * If 'sample_number' == 0:
                The Ah401 acquires data continuously until acquisition is stopped with the 'acquisition_stop' command.

            * If 'sample_number' != 0:
                If the 'sample_number' is not zero, the acquisition automatically stops after the configured number of samples is acquired.
                Moreover, if the 'sum_mode' is enabled, only the summed values of the samples are returned.

        HARDWARE:

            When staring an acquistion with the 'acquistion_start' command, the Ah401 waits to receive a falling edge signal.
            As soon as this signal is detected, the AH401 starts to acquire data continuously.

            * If 'sample_number' == 0:
                When a second signal is received, the acquisition is paused. Then, another signal will resume the acquisition (unpause).
                This behavior is repeated until acquisition is stopped with the 'acquisition_stop' command.

            * If 'sample_number' != 0:
                If the 'sample_number' is not zero, the acquisition automatically stops after the configured number of samples is acquired
                and the instrument waits for a new TRIGGER signal.
                Moreover, if the 'sum_mode' is enabled, only the summed values of the samples are returned.
                This behaviour continues until acquisition is stopped with the 'acquisition_stop' command.
        """
        return self._trig_mode

    @trigger_mode.setter
    def trigger_mode(self, mode):
        """
        Set the trigger mode: [SOFTWARE, HARDWARE]

        SOFTWARE:

            When staring an acquistion with the 'acquistion_start' command, the Ah401 starts to acquire data continuously.

            * If 'sample_number' == 0:
                The Ah401 acquires data continuously until acquisition is stopped with the 'acquisition_stop' command.

            * If 'sample_number' != 0:
                If the 'sample_number' is not zero, the acquisition automatically stops after the configured number of samples is acquired.
                Moreover, if the 'sum_mode' is enabled, only the summed values of the samples are returned.

        HARDWARE:

            When staring an acquistion with the 'acquistion_start' command, the Ah401 waits to receive a falling edge signal.
            As soon as this signal is detected, the AH401 starts to acquire data continuously.

            * If 'sample_number' == 0:
                When a second signal is received, the acquisition is paused. Then, another signal will resume the acquisition (unpause).
                This behavior is repeated until acquisition is stopped with the 'acquisition_stop' command.

            * If 'sample_number' != 0:
                If the 'sample_number' is not zero, the acquisition automatically stops after the configured number of samples is acquired
                and the instrument waits for a new TRIGGER signal.
                Moreover, if the 'sum_mode' is enabled, only the summed values of the samples are returned.
                This behaviour continues until acquisition is stopped with the 'acquisition_stop' command.
        """
        if isinstance(mode, TriggerMode):
            self._trig_mode = mode
        else:
            self._trig_mode = TriggerMode[mode]

    @lazy_init
    def get_model(self):
        return self._model

    @lazy_init
    def get_version(self):
        # VER command has a bug (using a bad eol '/n/r' instead of '/r/n')
        # return self.send_cmd("VER", "?")

        self._check_not_running()

        eol = "\r\n"
        if self._model == "B":
            eol = "\n\r"

        ans = self.comm.write_readline(b"VER ?\r", eol=eol).decode()
        if ans.startswith("VER"):
            if self._model == "B":
                return ans.split(" ")[1]
            else:
                return ans.split(" ")[2]

        return ans

    @lazy_init
    def get_info(self):
        fsr12 = self.FULL_SCALE_RANGE[self._fsrange12]
        fsr34 = self.FULL_SCALE_RANGE[self._fsrange34]

        msg = f"\n=== AH401 Controller (model: {self._model}, version: {self.get_version()}) ===\n\n"  # check_not_running done here !
        msg += f"    Half mode:        {self.half_mode} \n"
        msg += f"    Sum mode:         {self.sum_mode} \n"
        msg += f"    Trigger mode:     {self.trigger_mode.name} \n"
        msg += f"    Sample number:    {self.sample_number} \n\n"

        msg += f"    Integration time:    {self.integration_time} s\n"
        msg += f"    Scale range ch1-ch2: {fsr12[0]} {fsr12[2]} (max {fsr12[0]/self._integration_time*fsr12[1]}A)\n"
        msg += f"    Scale range ch3-ch4: {fsr34[0]} {fsr34[2]} (max {fsr34[0]/self._integration_time*fsr34[1]}A)\n"

        msg += f"    Acquisition status:  {self.acquistion_status()}\n"

        return msg

    @lazy_init
    def read_channels(self, timeout=None, raw=False):
        """Single readout of channels values. Use raw=True to return unconverted values."""
        self._check_not_running()
        with self.comm.lock:
            self._acquiring = True
            try:
                self.comm.write(b"GET ?\r")
                values = self.read_data(timeout=timeout, raw=raw)
            finally:
                self._acquiring = False
        return values

    @lazy_init
    def read_data(self, timeout=None, raw=False):
        """Read channels data while an acquisition has been started with the 'acquistion_start' command.
        Use raw=True to return unconverted values.
        """
        rawdata = self._read_raw_data(timeout)
        chanvals = self._raw2array(rawdata)
        if self.saturation_warnings:
            saturating_channels = list(
                numpy.where(chanvals >= self.FULL_SCALE_MAX)[0] + 1
            )
            if saturating_channels:
                print(
                    f"warning AH401 device is saturating on channels {saturating_channels} ({self})"
                )
        if raw:
            return chanvals
        return self._convert_raw_data(chanvals)

    @lazy_init
    def calibrate_data_offset(self, samples=10):
        self._check_not_running()
        curr_samp_num = self.sample_number
        curr_sum_mode = self.sum_mode
        data_offset = self.data_offset
        timeout_fac = 2 if self.half_mode else 1
        try:
            self.sample_number = samples
            self.sum_mode = True
            timeout = self._integration_time * samples * timeout_fac + 0.5
            self.acquistion_start()
            sum = self.read_data(timeout=timeout, raw=True)
            data_offset = (sum / samples).astype(int)
        finally:
            self.acquistion_stop()
            self.sample_number = curr_samp_num
            self.sum_mode = curr_sum_mode
            self.data_offset = data_offset

    @lazy_init
    def acquistion_start(self):
        self._check_not_running()
        self._acq_stop_retry = 0
        log_debug(
            self, f"Ah401 acquistion_start in {self._trig_mode.name} trigger mode"
        )
        if self._trig_mode == TriggerMode.HARDWARE:
            self.send_cmd("TRG", "ON")
        else:
            self.send_cmd("ACQ", "ON")

        self._acquiring = True

    def acquistion_stop(self):
        if self._trig_mode == TriggerMode.HARDWARE:
            msg = f"TRG OFF{self.WEOL}".encode()
        else:
            msg = f"ACQ OFF{self.WEOL}".encode()

        ans = self.comm.write_readline(msg)

        if not ans.endswith(b"ACK"):
            if ans.endswith(b"NAK") and self._acq_stop_retry < 4:
                log_debug(self, f"Ah401 retry stop acquistion {self._acq_stop_retry}")
                self._acq_stop_retry += 1
                return self.acquistion_stop()

            raise RuntimeError(
                f'Error in acquistion_stop command with response "{ans}"'
            )

        if len(self.comm._data) != 0:
            print(
                f"Warning: unexpected remaining data have been flushed: {self.comm._data}"
            )
            self.comm.flush()

        self._acquiring = False
        log_debug(self, "Ah401 acquistion_stopped")
        return True

    def acquistion_status(self):
        if self._acquiring:
            return "RUNNING"
        return "READY"

    @lazy_init
    def send_cmd(self, cmd, arg):

        self._check_not_running()

        if cmd not in self.CMD2PARAM.keys():
            raise ValueError(
                f"Unknown command {cmd}, should be in {list(self.CMD2PARAM.keys())}"
            )

        msg = f"{cmd} {arg}{self.WEOL}".encode()
        ans = self.comm.write_readline(msg).decode()

        log_debug(self, "send_cmd %s %s => %s", cmd, arg, ans)

        if arg == "?":
            res = ans.split(" ")
            if len(res) != 2:
                raise RuntimeError(f"Cannot handle answer: {ans} to command {cmd}")

            if res[0] == cmd:
                return res[1]

        if ans == "ACK":
            return True

        raise RuntimeError(f"Error in command '{cmd}' with response '{ans}'")

    def _read_raw_data(self, timeout=None):
        timeout = timeout or self.comm._timeout  # temp fix to handle modif in tcp.py
        return self.comm.read(self._bin_data_len, timeout=timeout)

    def _raw2array(self, rawdata):
        if self._sum_mode:
            return numpy.array(
                [
                    rawdata[0 + 4 * i]
                    + rawdata[1 + 4 * i] * 256
                    + rawdata[2 + 4 * i] * 65536
                    + rawdata[3 + 4 * i] * 16777216
                    for i in range(4)
                ],
                dtype=float,
            )

        return numpy.array(
            [
                rawdata[0 + 3 * i]
                + rawdata[1 + 3 * i] * 256
                + rawdata[2 + 3 * i] * 65536
                for i in range(4)
            ],
            dtype=float,
        )

    def _convert_raw_data(self, rawdata):
        """convert rawdata to a current value (A)
        expect rawdata as a numpy array of 4 values (for each channel)
        """
        if self._model == "D":
            v1 = (
                (self._fsrval12 / float(self.FULL_SCALE_MAX))
                * (rawdata[0:2] - self.data_offset[0:2])
                / self._integration_time
            )
            v2 = (
                (self._fsrval34 / float(self.FULL_SCALE_MAX))
                * (rawdata[2:4] - self.data_offset[2:4])
                / self._integration_time
            )
            data = numpy.hstack((v1, v2))
        else:
            data = (
                (self._fsrval12 / float(self.FULL_SCALE_MAX))
                * (rawdata - self.data_offset)
                / self._integration_time
            )

        return data

    def dump_data(self, wait_for_missing_chunk=True):
        """Dump all data received until now.
        If wait_for_missing_chunk is True and if current data buffer size is not
        a multiple of bin_data_len, then it waits to receive the missing chunk.
        """
        buf_len = len(self.comm._data)
        to_dump = self._bin_data_len * (buf_len // self._bin_data_len)
        if wait_for_missing_chunk:
            if buf_len % self._bin_data_len != 0:
                to_dump += self._bin_data_len

        dumpped = self.comm.read(to_dump)
        log_debug(self, f"Ah401 dumpped {len(dumpped)}")
        return dumpped

    def _check_not_running(self):
        if self._acquiring:
            raise RuntimeError(
                "Cannot perform this action while acquisition is running, stop acquisition first"
            )


class Ah401CC(SamplingCounterController):
    def __init__(self, name, ah401):
        super().__init__(name, master_controller=None, register_counters=False)
        self.max_sampling_frequency = 1000
        self.ah401 = ah401

    def read_all(self, *counters):
        """Return the values of the given counters as a list.

        If possible this method should optimize the reading of all counters at once.
        """
        data = self.ah401.read_data()
        values = []
        for cnt in counters:
            values.append(data[cnt.channel - 1])
        return values

    def get_values(self, from_index, *counters):
        cnt_values = [[] for cnt in counters]

        while len(self.ah401.comm._data) >= self.ah401._bin_data_len:
            data = self.ah401.read_data()
            for idx, cnt in enumerate(counters):
                cnt_values[idx].append(data[cnt.channel - 1])

        return cnt_values

    def get_acquisition_object(self, acq_params, ctrl_params, parent_acq_params):
        trigger_mode = acq_params.pop("trigger_mode", TriggerMode.SOFTWARE.name)
        if trigger_mode == "SOFTWARE":
            return Ah401SAS(self, ctrl_params=ctrl_params, **acq_params)
        elif trigger_mode == "HARDWARE":
            return Ah401IAS(self, ctrl_params=ctrl_params, **acq_params)
        else:
            raise ValueError(f"Unknown trigger mode {trigger_mode}")


class Ah401Counter(SamplingCounter):
    def __init__(
        self,
        name,
        controller,
        channel,
        conversion_function=None,
        mode=SamplingMode.MEAN,
        unit=None,
    ):
        super().__init__(
            name, controller, conversion_function, mode, unit
        )  # TODO: add **kwargs to pass channel/tag/etc
        self.channel = int(channel)


class Ah401SAS(SamplingCounterAcquisitionSlave):
    def prepare_device(self):
        self.device.ah401.trigger_mode = TriggerMode.SOFTWARE
        self.device.ah401.sample_number = 0

        if self.device.ah401.counting_mode == CountingMode.AUTO:
            itime = self.count_time
            itime = max(itime, self.device.ah401.CMD2PARAM["ITM"][0])
            itime = min(itime, self.device.ah401.CMD2PARAM["ITM"][1])
            self.device.ah401.integration_time = itime
        elif self.count_time < self.device.ah401._integration_time:
            raise ValueError(
                f"count_time cannot be smaller than ah401 integration_time {self.device.ah401._integration_time}"
            )

        self.device.max_sampling_frequency = 1 / self.device.ah401._integration_time

        self.device.ah401.acquistion_start()

    def start_device(self):
        pass  # start in the prepare because timescan uses start_once=False

    def stop_device(self):
        self.device.ah401.acquistion_stop()

    def trigger(self):
        self.device.ah401.dump_data()
        super().trigger()


class Ah401IAS(IntegratingCounterAcquisitionSlave):
    def prepare_device(self):
        self.device.ah401.trigger_mode = TriggerMode.HARDWARE
        self.device.ah401.sample_number = 1
        self.device.ah401.acquistion_start()

    def start_device(self):
        pass

    def stop_device(self):
        self.device.ah401.acquistion_stop()


class Ah401(Ah401Device, BlissController):
    """BlissController class for the AH401 models B and D from CAEN (www.caenels.com).

    AH401 is a 4-channel, 20-bit resolution, low noise, high performance charge-integration picoammeter.

    It is composed of a particular charge-integration input stage for low current sensing,
    coupled with a 20-bit sigma-delta ADC converter including a noise reduction digital filter.
    Model D is able to use different integration ranges for channels 1-2 and channels 3-4,
    whereas model B uses same range for all channels (see self.scale_range).

    The AH401D performs current measurements from 50 pA (with a resolution of 50 aA) up to 2.0 μA (with a resolution of 2.0 pA),
    with integration times ranging from 1ms up to 1s. Moreover, each input channel has two parallel integrator stages,
    so that the current-to-voltage conversion can be performed continuously also during the ADC conversion,
    avoiding any dead time in the data throughput (see self.half_mode).

    It also performs digitization of the acquired current data, thus strongly minimizing the transmission length of
    analog current signals from the detector and providing directly digital data output, which is easy to transmit
    over long distances without any noise pick-up.

    The AH401D is housed in a light, practical and extremely compact metallic box that can be placed as close as
    possible to the current source (detector) in order to reduce cable lengths and minimize possible noise pick-up
    during the propagation of very low intensity analog signals. It is specially suited for applications where multi-channel
    simultaneous acquisitions are required, a typical application being the currents readout from 4-quadrant photodiodes
    used to monitor X-ray beam displacements. Low temperature drifts, good linearity and the very low intrinsic noise of
    the AH401D allow obtaining very high precision current measurements.

    The AH401D uses a standard Ethernet TCP communication layer with the DEFAULT_PORT = 10001.


    *** YML config ***

      - name: ah401
        class: Ah401
        plugin: generic
        module: ah401

        tcp:
            #url: 'bm32pico2:10001'
            url: '160.103.123.129:10001'

        counting_mode: STD

        counters:
          - name: pico_ch1
            channel: 1
            mode: MEAN
            unit: nA

          - name: pico_ch2
            channel: 2
            mode: MEAN
            unit: nA

          - name: pico_ch3
            channel: 3
            mode: MEAN
            unit: nA

          - name: pico_ch4
            channel: 4
            mode: MEAN
            unit: nA

    """

    def __init__(self, config):
        Ah401Device.__init__(self, config)
        BlissController.__init__(self, config)
        self._settings = HashObjSetting(f"{self.name}_settings")
        self._load_settings()
        self.data_offset = self._get_setting("data_offset")

        self._scc = Ah401CC(self.name, self)

        global_map.register(self, parents_list=["counters"])

    # ============ BlissController abstract methods =======================

    def _get_subitem_default_class_name(self, cfg, parent_key):
        # Called when the class key cannot be found in the item_config.
        # Then a default class must be returned. The choice of the item_class is usually made from the parent_key value.
        # Elements of the item_config may also by used to make the choice of the item_class.

        """
        Return the appropriate default class name (as a string) for a given item.

        Arguments:
            cfg: item config node
            parent_key: the key under which item config was found
        """

        if parent_key == "counters":
            return "Ah401Counter"
        else:
            raise NotImplementedError

    def _get_subitem_default_module(self, class_name, cfg, parent_key):
        # Called when the given class_name (found in cfg) cannot be found at the container module level.
        # Then a default module path must be returned. The choice of the item module is usually made from the parent_key value.
        # Elements of the item_config may also by used to make the choice of the item module.

        """
        Return the path (str) of the default module where the given class_name should be found.

        Arguments:
            class_name: item class name
            cfg: item config node
            parent_key: the key under which item config was found
        """

        # Note: If the default classes can be found in this module, this method can remains not implemented.

        raise NotImplementedError

    def _create_subitem_from_config(
        self, name, cfg, parent_key, item_class, item_obj=None
    ):
        # Called when a new subitem is created (i.e accessed for the first time via self._get_subitem)
        """
        Return the instance of a new item owned by this container.

        Arguments:
            name: item name
            cfg: item config
            parent_key: the config key under which the item was found (ex: 'counters').
            item_class: a class to instantiate the item (None if item is a reference)
            item_obj: the item instance (None if item is NOT a reference)

        return: item instance

        """

        if item_obj is not None:
            return item_obj

        if parent_key == "counters":
            channel = cfg["channel"]
            smode = cfg.get("sampling_mode", "MEAN")
            unit = cfg.get("unit", "A")
            if unit == "pA":

                def conv_pA(x):
                    return x * 1e12

                convfunc = conv_pA
            elif unit == "nA":

                def conv_nA(x):
                    if isinstance(x, list):
                        return [i * 1e9 for i in x]
                    return x * 1e9

                convfunc = conv_nA
            else:
                convfunc = None

            return self._scc.create_counter(
                item_class,
                name=name,
                channel=channel,
                conversion_function=convfunc,
                unit=unit,
                mode=smode,  # TODO: add **kwargs to pass channel/tag/etc to give flexibility with custom class
            )
        else:
            raise NotImplementedError

    def _load_config(self):
        # Called by the plugin via self._initialize_config
        # Called after self._subitems_config has_been filled.

        """
        Read and apply the YML configuration of this container.
        """

        self.counting_mode = self.config.get("counting_mode", CountingMode.STD)

        # create counters declared in the yml now so that they are available via 'counters' property
        for cfg in self.config["counters"]:
            self._get_subitem(cfg["name"])

    def _init(self):
        # Called by the plugin via self._initialize_config
        # Called just after self._load_config

        """
        Place holder for any action to perform after the configuration has been loaded.
        """
        pass

    def _get_default_chain_counter_controller(self):
        """Return the default counter controller that should be used
        when this controller is used to customize the DEFAULT_CHAIN
        """
        return self._scc

    @autocomplete_property
    def counters(self):
        return self._scc.counters

    def __info__(self):
        """Return controller info as a string"""
        txt = self.get_info()
        txt += f"    Counting mode:       {self.counting_mode.name}\n"
        return txt

    # === Persistant settings with caching (for minimal com with redis) =====================

    def _load_settings(self):
        """Get from redis the persistent parameters (redis access)"""
        cached = {}
        cached["data_offset"] = self._settings.get(
            "data_offset", numpy.array([self.DEFAULT_OFFSET] * 4)
        )
        self._cached_settings = cached

    def _clear_settings(self):
        self._settings.clear()
        self._load_settings()

    def _get_setting(self, key):
        """Get a persistent parameter from local cache (no redis access)"""
        return self._cached_settings[key]

    def _set_setting(self, key, value):
        """Store a persistent parameter in redis and update local cache (redis access)"""
        self._settings[key] = value
        self._cached_settings[key] = value

    # === Customization =================================================================

    @Ah401Device.data_offset.setter
    def data_offset(self, offset):
        Ah401Device.data_offset.fset(self, offset)
        self._set_setting("data_offset", self.data_offset)

    @property
    def counting_mode(self):
        return self._counting_mode

    @counting_mode.setter
    def counting_mode(self, mode):
        if isinstance(mode, CountingMode):
            self._counting_mode = mode
        else:
            self._counting_mode = CountingMode[mode]
