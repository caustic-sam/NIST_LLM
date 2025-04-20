import time
import os.path
import numpy
import h5py
import gevent
import gevent.queue
import gevent.event
import gevent.lock

from bliss.common import event
from bliss.common.logtools import log_debug
from bliss.common.user_status_info import status_message

from .base import (
    BaseMCA,
    Brand,
    DetectorType,
    PresetMode,
    TriggerMode,
    MCABeaconObject,
)
from .counter import (
    Stats,
    mca_counters,
    mca_counter_groups,
)

global XGL_ENABLE
try:
    import XGL_DPP as xgl

    XGL_ENABLE = True
except ImportError:
    XGL_ENABLE = False


class XGLabDanteBeaconObject(MCABeaconObject):

    url = MCABeaconObject.config_getter("url")
    configuration_directory = MCABeaconObject.config_getter("configuration_directory")
    current_configuration = MCABeaconObject.property_setting(
        "current_configuration", default=None
    )


class XGLabDanteBoardConfig(object):
    def __init__(self, chan_id, dppconfig):
        self.__chan_id = chan_id
        self.__names = [name for name in dir(dppconfig) if not name.startswith("_")]
        for name in self.__names:
            self.__setattr__(name, getattr(dppconfig, name))

    def __info__(self):
        info = f"DPP CONFIG on channel #{self.__chan_id}:\n"
        for name in self.__names:
            value = self.__getattribute__(name)
            info += f".{name:20s} = {value}\n"
        return info

    def get_configuration(self):
        cfg = xgl.configuration()
        for name in self.__names:
            setattr(cfg, name, self.__getattribute__(name))
        return cfg

    def set(self, name, value):
        if name not in self.__names:
            raise ValueError(f"No config parameter names [{name}]")
        self.__setattr__(name, value)


class XGLabDanteConfig(dict):
    def __init__(self, channels, dpps):
        self.__config = dict()
        for chan, cfg in zip(channels, dpps):
            self.__config[chan] = XGLabDanteBoardConfig(chan, cfg)

    def __getitem__(self, chan):
        item = self.__config.get(chan)
        if item is None:
            raise ValueError(f"NO DPP config for channel #{chan}")
        return item

    def __info__(self):
        chans = list(self.__config.keys())
        info = f"DPP CONFIG for channels : {chans}\n\n"
        info += " - access one board config using dppconfig[channel]\n"
        info += " - set parameter to all boards using dppconfig.set(name, value)\n"
        return info

    def set(self, name, value):
        for chanconf in self.__config.values():
            chanconf.set(name, value)


class XGLabDante(BaseMCA):

    InputModes = ["DC_RinHighImp", "DC_RinLowImp", "AC_Slow", "AC_Fast"]

    GatingMode2Trigger = {
        "FreeRunning": TriggerMode.SOFTWARE,
        "TriggerRising": TriggerMode.SYNC,
        "TriggerFalling": TriggerMode.SYNC,
        "TriggerBoth": TriggerMode.SYNC,
        "GatedHigh": TriggerMode.GATE,
        "GatedLow": TriggerMode.GATE,
    }

    def __init__(self, name, config):
        super().__init__(name, config, beacon_obj_class=XGLabDanteBeaconObject)
        if not XGL_ENABLE:
            raise RuntimeError(
                "XGLabDante: library not installed in current environement"
            )

    def initialize_attributes(self):
        self.__lib_version = None
        self.__firmware_version = None
        self.__master_id = None
        self.__nboards = 0
        self.__head = f"XGLabDante[{self.name}]"
        self.__gating_set = None
        self.__call_time = None
        self.__watchdog_task = None
        self.__read_task = None
        self.__stop_event = gevent.event.Event()
        self.__single_count = False

        self.__nchans = 0
        self.__channum = list()
        self.__dppconfig = None
        self.__expo_time = 0.1
        self.__npoints = 1
        self.__spectrum_size = 4096
        self.__block_size = 1

        self.__watchdog_period = self.config.get("watchdog_period", 4.0)

        self.beacon_obj._initialize_with_setting()
        if not os.path.isdir(self.beacon_obj.configuration_directory):
            raise ValueError(
                f"{self.__head} configuration directory is not valid [{self.beacon_obj.configuration_directory}]"
            )

    def initialize_hardware(self):
        # --- init library
        xgl.pyInitLibrary()

        # --- discover devices
        ret = xgl.pyadd_to_query(self.beacon_obj.url)
        if ret is False:
            raise RuntimeError(
                f"{self.__head} failed to add IP [{self.beacon_obj.url}]"
            )

        xgl.pyautoScanSlaves(True)
        gevent.sleep(2.0)

        idx = 0
        while idx < 10:
            (ret, ndev) = xgl.pyget_dev_number()
            if ret is False:
                raise RuntimeError(f"{self.__head} failed to get device number")
            if ndev > 0:
                break
            idx += 1
            gevent.sleep(1.0)

        if ndev == 0:
            raise RuntimeError(f"{self.__head} could not found any device")
        if ndev != 1:
            raise RuntimeError(f"{self.__head} can manage only one master device !!")

        (ret, master_id) = xgl.pyget_ids(0, 64)
        if ret is False:
            raise RuntimeError(f"{self.__head} failed to get board ID")
        self.__master_id = master_id

        print(f"\n{self.__head} Connected to [{master_id}]")

        while idx < 10:
            (ret, nboards) = xgl.pyget_boards_in_chain(self.__master_id)
            if ret is False:
                raise RuntimeError(f"{self.__head} failed to get number of boards")
            if nboards > 0:
                break
            idx += 1
            gevent.sleep(1.0)

        if nboards == 0:
            raise RuntimeError(f"{self.__head} No boards found !!")

        self.__nboards = nboards
        self.__nchans = nboards
        self.__channum = list(range(nboards))

        print(f"{self.__head} {nboards} boards in chain.")

        xgl.pyautoScanSlaves(False)

        # --- connect callback
        self.__answers = dict()
        self.__answer_lock = gevent.lock.RLock()
        ret = xgl.pyregister_callback(self._xgl_callback)
        if ret is False:
            raise RuntimeError(f"{self.__head} failed to register callback")

        # --- start watchdog
        self._xgl_reset_call_time()
        self.__watchdog_stop = gevent.event.Event()
        self.__watchdog_task = gevent.spawn(self._xgl_watchdog)

        # --- reload previous config
        filename = self.beacon_obj.current_configuration
        if filename is not None:
            try:
                self.load_configuration(filename)
            except Exception:
                print(f"{self.__head} FAILED to load configuration [{filename}]")
                self.beacon_obj.current_configuration = None
        else:
            print(f"{self.__head} WARNING no configuration file defined yet !!")

    def __close__(self):
        if self.__watchdog_task is not None:
            self.__watchdog_stop.set()
            self.__watchdog_task.join()
        xgl.pyCloseLibrary()

    def __debug(self, msg, *args):
        log_debug(self, "%s " + msg, self.__head, *args)

    # ---
    # communication callback / errors
    # ---

    def _xgl_callback(self, call_type, call_id, length, data):
        with self.__answer_lock:
            self.__answers[call_id] = (call_type, data)

    def _xgl_reset_call_time(self):
        self.__call_time = time.time()

    def _xgl_wait_reply(self, call_id, cmdmsg, wait=0.1, retry=20):
        self._xgl_reset_call_time()
        if call_id == 0:
            errcode = self._xgl_last_error()
            raise RuntimeError(f"{self.__head} {cmdmsg} calling failed [err={errcode}]")
        idx = 0
        while idx < retry:
            gevent.sleep(wait)
            with self.__answer_lock:
                if call_id in self.__answers:
                    (call_type, data) = self.__answers.pop(call_id)
                    if call_type == 0:
                        errcode = self._xgl_last_error()
                        raise RuntimeError(
                            f"{self.__head} {cmdmsg} reply error [err={errcode}]"
                        )
                    if call_type == 2 and data[0] != 1:
                        errcode = self._xgl_last_error()
                        raise RuntimeError(
                            f"{self.__head} {cmdmsg} command failed [err={errcode}]"
                        )
                    self.__debug("got answer for %s", cmdmsg)
                    return data
            idx += 1
            self.__debug("waiting answer to %s try %d", cmdmsg, idx)
        self.__debug("failed to get answer to %s", cmdmsg)

    def _xgl_last_error(self):
        (ret, err) = xgl.pygetLastError()
        if ret:
            return err
        return 0

    def _xgl_reset_error(self):
        ret = xgl.pyresetLastError()
        if ret is False:
            raise RuntimeError(f"{self.__head} Failed to reset last error")

    def _xgl_flush(self):
        xgl.pyflush_local_eth_conn(self.__master_id)

    def _xgl_watchdog(self):
        while not self.__watchdog_stop.is_set():
            gevent.sleep(1.0)
            if time.time() - self.__call_time > self.__watchdog_period:
                call_id = xgl.pygetFirmware(self.__master_id, 0)
                self._xgl_wait_reply(call_id, "get firmware version")

    # ---
    # some specific properties
    # ---

    @property
    def url(self):
        return self.beacon_obj.url

    @property
    def master_id(self):
        return self.__master_id

    @property
    def library_version(self):
        if self.__lib_version is None:
            (ret, val) = xgl.pylibVersion(64)
            if ret is False:
                raise RuntimeError(f"{self.__head} cannot get library version")
            self.__lib_version = val
        return self.__lib_version

    @property
    def firmware_version(self):
        if self.__firmware_version is None:
            call_id = xgl.pygetFirmware(self.__master_id, 0)
            data = self._xgl_wait_reply(call_id, "get firmware version")
            self.__firmware_version = ".".join([str(a) for a in data])
        return self.__firmware_version

    # ---
    # configuration methods
    # ---

    @property
    def configuration_directory(self):
        return self.beacon_obj.configuration_directory

    @property
    def current_configuration(self):
        return self.beacon_obj.current_configuration

    def _read_configuration_file(self, filename):
        hfile = h5py.File(filename, "r")

        # --- parse channel
        chanids = hfile["ChannelID"]
        nchan = len(chanids)
        channum = list()
        for cid in chanids:
            channum.append(int(cid.tobytes().split(b"_Ch")[1]) - 1)

        # --- input mode
        hconf = hfile["Configuration"]
        input_config = [self.InputModes[int(val)] for val in hconf["InputMode"]]

        # --- input offset
        offset_config = list()
        for val1, val2 in zip(hconf["Input_Offset1"], hconf["Input_Offset2"]):
            offsets = xgl.configuration_offset()
            offsets.offset_val1 = int(val1)
            offsets.offset_val2 = int(val2)
            offset_config.append(offsets)

        # --- dpp parameters
        MapConf2HdfName = [
            ("base_offset", "Exponential_Offset", int),
            ("baseline_samples", "Baseline_Samples", int),
            ("edge_flat_top", "FastFilter_FlatTop", int),
            ("edge_peaking_time", "FastFilter_PeakTime", int),
            ("energy_filter_thr", "EnergyFilter_Th", int),
            ("fast_filter_thr", "FastFilter_Th", int),
            ("flat_top", "EnergyFilter_FlatTop", int),
            ("gain", "Gain", float),
            ("inverted_input", "InputInverted", int),
            ("max_peaking_time", "EnergyFilter_MaxPeakTime", int),
            ("max_risetime", "MaxRiseTime", int),
            ("other_param", None, None),
            ("overflow_recovery", None, None),
            ("peaking_time", "EnergyFilter_PeakTime", int),
            ("reset_recovery_time", "Recovery_Time", int),
            ("reset_threshold", "Reset_Th", int),
            ("tail_coefficient", None, None),
            ("time_constant", "Exponential_TimeConstant", float),
            ("zero_peak_freq", "ZeroPeakRate", float),
        ]

        dpp_config = list()
        for idx in range(nchan):
            chanconf = xgl.configuration()
            for (cname, hname, conv) in MapConf2HdfName:
                if hname is not None:
                    value = conv(hconf[hname][idx])
                    setattr(chanconf, cname, value)
            dpp_config.append(chanconf)

        return channum, input_config, offset_config, dpp_config

    def load_configuration(self, filename):
        filepath = os.path.join(self.configuration_directory, filename)
        if not os.path.isfile(filepath):
            raise ValueError(
                f"{self.__head} configuration file does not exist [{filepath}]"
            )

        # --- read config file
        (channum, modes, offsets, dpps) = self._read_configuration_file(filepath)
        nchans = len(channum)
        if nchans > self.__nboards:
            raise ValueError(
                f"{self.__head} File contains config for more than {self.__nboards} boards"
            )

        print(f"{self.__head} Loading configuration [{filename}] ...")
        with status_message() as update:

            # --- enable active channels
            update(f"{self.__head} Enable channels")
            for idx in range(self.__nboards):
                if idx in channum:
                    flag = False
                    msg = "enable"
                else:
                    flag = True
                    msg = "disable"
                self.__debug("%s board #%d", msg, idx)
                call_id = xgl.pydisableBoard(self.__master_id, idx, flag)
                self._xgl_wait_reply(call_id, f"{msg} board #{idx}")

            self.__nchans = nchans
            self.__channum = channum
            self.__dppconfig = None

            # --- clear counters cache
            mca_counters.cache_clear()
            mca_counter_groups.cache_clear()

            # --- set input config
            update(f"{self.__head} Configure input mode")
            self.__debug("configure input mode")
            for chan_id, mode in zip(channum, modes):
                call_id = xgl.pyconfigure_inputmode(self.__master_id, chan_id, mode)
                self._xgl_wait_reply(call_id, f"configure input on board #{chan_id}")

            # --- set input offset
            update(f"{self.__head} Configure input offset")
            self.__debug("configure input offset")
            for chan_id, offset in zip(channum, offsets):
                call_id = xgl.pyconfigure_offset(self.__master_id, chan_id, offset)
                self._xgl_wait_reply(call_id, f"configure offset on board #{chan_id}")

            # --- set dpp config
            for chan_id, chanconf in zip(channum, dpps):
                update(f"{self.__head} Configure dpp on board {chan_id}")
                self.__debug("configure dpp on board #%d", chan_id)
                call_id = xgl.pyconfigure(self.__master_id, chan_id, chanconf)
                self._xgl_wait_reply(call_id, f"configure dpp on board #{chan_id}")

            self.__dppconfig = XGLabDanteConfig(channum, dpps)

            update(f"{self.__head} {nchans} boards configured")

        # --- keep config filename
        self.beacon_obj.current_configuration = filename

        # --- reset gating mode so it will be send again on next acq
        self.__gating_set = None

    def available_configurations(self):
        path = self.configuration_directory
        ext = ".hdf"
        sep = "/"
        return [
            os.path.relpath(os.path.join(dp, f), path).lstrip(sep)
            for dp, dn, fn in os.walk(path)
            for f in fn
            if f.endswith(ext)
        ]

    @property
    def dppconfig(self):
        if self.__dppconfig is None:
            raise ValueError("No valid configuration loaded !!")
        return self.__dppconfig

    def apply_dppconfig(self, *chans):
        if not len(chans):
            chans = self.__channum
        for chan_id in chans:
            chanconf = self.dppconfig[chan_id].get_configuration()
            print(f"Apply dpp configuration on board #{chan_id}")
            call_id = xgl.pyconfigure(self.__master_id, chan_id, chanconf)
            self._xgl_wait_reply(call_id, f"configure dpp on board #{chan_id}")

    def __info__(self):
        info_str = super().__info__()
        info_str += "DANTE:\n"
        info_str += f"    configuration directory : {self.configuration_directory}\n"
        info_str += f"    current configuration   : {self.current_configuration}\n"
        info_str += f"    active channels         : {self.elements}\n"
        return info_str

    # ---
    # BaseMCA properties
    # ---

    @property
    def detector_brand(self):
        return Brand.XGLAB

    @property
    def detector_type(self):
        return DetectorType.DANTE

    @property
    def elements(self):
        return self.__channum

    @property
    def spectrum_size(self):
        return self.__spectrum_size

    @spectrum_size.setter
    def spectrum_size(self, value):
        asked = int(value)
        sizes = [1024, 2048, 4096]
        if asked not in sizes:
            raise ValueError(
                f"{self.__head} Invalid spectrum size. Should be one of {sizes}"
            )
        self.__spectrum_size = asked

    @property
    def supported_preset_modes(self):
        return [PresetMode.REALTIME]

    @property
    def preset_mode(self):
        return PresetMode.REALTIME

    @preset_mode.setter
    def preset_mode(self, mode):
        if mode not in self.supported_preset_modes:
            raise ValueError(f"{self.__head} Invalid preset mode")

    @property
    def preset_value(self):
        return self.__expo_time

    @preset_value.setter
    def preset_value(self, value):
        self.__expo_time = float(value)

    @property
    def supported_trigger_modes(self):
        return [TriggerMode.SOFTWARE, TriggerMode.SYNC, TriggerMode.GATE]

    @property
    def trigger_mode(self):
        if self.__gating_set is None:
            return TriggerMode.SOFTWARE
        return XGLabDante.GatingMode2Trigger[self.__gating_set]

    @trigger_mode.setter
    def trigger_mode(self, mode):
        if mode not in self.supported_trigger_modes:
            raise ValueError(f"{self.__head} Invalid trigger mode")
        if mode == TriggerMode.SOFTWARE:
            gating_mode = "FreeRunning"
        elif mode == TriggerMode.SYNC:
            gating_mode = "TriggerRising"
        else:
            gating_mode = "GatedHigh"
        if gating_mode != self.__gating_set:
            for idx in self.__channum:
                call_id = xgl.pyconfigure_gating(self.__master_id, gating_mode, idx)
                self._xgl_wait_reply(call_id, f"set trigger mode on chan {idx}")
            self.__debug("gating set to to %s", gating_mode)
            self.__gating_set = gating_mode

    @property
    def block_size(self):
        return self.__block_size

    @block_size.setter
    def block_size(self, value):
        if value is None:
            self.__block_size = 1
        else:
            self.__block_size = value

    # ---
    # soft acquisition
    # ---
    def trigger(self):
        self.__single_count = True

        # --- start acquisition
        call_id = xgl.pystart(self.__master_id, self.__expo_time, self.__spectrum_size)
        data = self._xgl_wait_reply(call_id, "start acquisition")
        self.__debug("single acquisition started")

        self.__stop_event.clear()

        # --- wait end of acquisition
        gevent.sleep(0.9 * self.__expo_time)
        received = [False] * self.__nchans
        while not all(received) and not self.__stop_event.is_set():
            for idx in range(self.__nchans):
                if received[idx] is False:
                    (ret, last) = xgl.pyisLastDataReceived(
                        self.__master_id, self.__channum[idx]
                    )
                    self._xgl_reset_call_time()
                    if ret and last:
                        received[idx] = True
            gevent.sleep(0.02)
            self.__debug("last data received %s", str(received))

        if self.__stop_event.is_set():
            self.__debug("acquisition aborted")
            self.__stop_event.clear()
            return

        self.__debug("acquisition finished")

        # --- read data
        spectra = {}
        statistics = {}
        for idx in self.__channum:
            (ret, data, idacq, stat) = xgl.pygetData(
                self.__master_id, idx, self.__spectrum_size
            )
            self._xgl_reset_call_time()
            if ret is False:
                raise RuntimeError(
                    f"{self.__head} Failed to read data for board #{idx}"
                )
            arrdata = numpy.array(data, dtype=numpy.uint64)
            spectra[idx] = arrdata[0 : self.__spectrum_size]
            statistics[idx] = Stats(
                stat.real_time / 1.0e6,
                stat.live_time / 1.0e6,
                0.0,
                stat.detected,
                stat.measured,
                stat.ICR / 1000.0,
                stat.OCR / 1000.0,
                stat.filt1_dt / 1.0e6,
            )
        self.__debug("reading finished")

        self.__stop_event.clear()

        # --- send data
        event.send(self, "data", (spectra, statistics))
        self.__debug("single acquisition finished")

    def test_soft_acquisition(self, expo_time):
        self.trigger_mode = TriggerMode.SOFTWARE
        self.preset_value = expo_time

        # --- start acquisition
        call_id = xgl.pystart(self.__master_id, self.__expo_time, self.__spectrum_size)
        self._xgl_wait_reply(call_id, "start acquisition")

        self.__debug("soft acquisition started")

        # --- wait end of acquisition
        gevent.sleep(0.9 * self.__expo_time)
        received = [False] * self.__nchans
        while not all(received):
            for idx in range(self.__nchans):
                if received[idx] is False:
                    (ret, last) = xgl.pyisLastDataReceived(
                        self.__master_id, self.__channum[idx]
                    )
                    self._xgl_reset_call_time()
                    if ret and last:
                        received[idx] = True
            gevent.sleep(0.02)

        self.__debug("soft acquisition finished")

    # ---
    # gate/sync acquisition
    # ---

    @property
    def hardware_points(self):
        return self.__npoints

    @hardware_points.setter
    def hardware_points(self, value):
        if value < 1:
            raise ValueError(f"{self.__head} hardware_points should be >= 1 !!")
        if value > 1 and self.trigger_mode == TriggerMode.SOFTWARE:
            raise ValueError(
                f"{self.__head} SOFTWARE trigger mode accept only one point !!"
            )
        self.__npoints = value

    def start_acquisition(self):
        self.__single_count = False
        self.__last_point_seen = -1
        cleared = xgl.pyclear_chain(self.__master_id)
        if not cleared:
            raise RuntimeError(f"{self.__head} Failed to clear chain")
        call_id = xgl.pystart_map(
            self.__master_id, 100, self.__npoints, self.__spectrum_size
        )
        self._xgl_wait_reply(call_id, "start acquisition")
        self.__debug("acquisition started for %s points", self.__npoints)

    def stop_acquisition(self):
        self.__debug("stop acquisition requested")
        if self.__single_count:
            self.__stop_event.set()
        else:
            call_id = xgl.pystop(self.__master_id)
            self.__debug("stop acquisition called")
            self._xgl_wait_reply(call_id, "stop acquisition")
        self.__block_size = 1
        self.__debug("acquisition stopped")

    def start_hardware_reading(self):
        if self.__read_task:
            raise RuntimeError(f"{self.__head} reading task still running !!")
        self.__read_task = gevent.spawn(self._do_hardware_reading)

    def wait_hardware_reading(self):
        try:
            self.__read_task.get()
        finally:
            self.__read_task = None

    def _do_hardware_reading(self):
        self.__debug("hardware reading task started")
        queue = gevent.queue.Queue()
        try:
            poll_task = gevent.spawn(self._hardware_poll_data, queue)
            for (spectra, stat) in queue:
                event.send(self, "data", (spectra, stat))
                gevent.sleep(0.0)
        finally:
            event.send(self, "data", StopIteration)
            if poll_task.ready():
                poll_task.get()  # in case of exception
            else:
                poll_task.kill()
            self.__debug("hardware reading task finished")

    def _hardware_poll_data(self, queue):
        finished = [False] * self.__nchans
        npoint_read = dict()
        for idx in self.__channum:
            npoint_read[idx] = 0
        npoint_sent = 0
        read_spectras = dict()
        read_stats = dict()

        try:
            while not all(finished):
                for idx in range(self.__nchans):
                    if finished[idx] is False:
                        (ret, last) = xgl.pyisLastDataReceived(
                            self.__master_id, self.__channum[idx]
                        )
                        self._xgl_reset_call_time()
                        if ret and last:
                            finished[idx] = True
                self.__debug("finished %r", finished)

                for idx in self.__channum:
                    (ret, available) = xgl.pygetAvailableData(self.__master_id, idx)
                    self._xgl_reset_call_time()
                    if not ret or not available:
                        continue
                    self.__debug("board #%d available = %d", idx, available)
                    (ret, spectra, spectraID, stats, adv_stats) = xgl.pygetAllData(
                        self.__master_id, idx, self.__spectrum_size, available
                    )
                    self._xgl_reset_call_time()

                    # -- unpack data to numpy arrays
                    spectra_arr = numpy.array(spectra)
                    spectra_arr.shape = (available, 4096)

                    stats_arr = numpy.array(stats)
                    realtime = stats_arr[0::4] / 1e6
                    livetime = stats_arr[1::4] / 1e6
                    icr = stats_arr[2::4] / 1000.0
                    ocr = stats_arr[3::4] / 1000.0

                    # -- split array per point for bliss
                    ipoint = npoint_read[idx]
                    for iread in range(available):
                        point_spectras = read_spectras.setdefault(ipoint + iread, {})
                        point_spectras[idx] = spectra_arr[iread]

                        point_stats = read_stats.setdefault(ipoint + iread, {})
                        point_stats[idx] = Stats(
                            realtime[iread],
                            livetime[iread],
                            0.0,
                            0.0,
                            0.0,
                            icr[iread],
                            ocr[iread],
                            0.0,
                        )
                        self.__debug(
                            "Board #%d > ID %d real %.3f live %.3f",
                            idx,
                            spectraID[iread],
                            point_stats[idx].realtime,
                            point_stats[idx].trigger_livetime,
                        )

                    npoint_read[idx] += available
                    self.__debug(
                        "Board #%d > total spectrum read = %d", idx, npoint_read[idx]
                    )

                self.__last_point_seen = max(npoint_read.values())
                last_point_read = min(npoint_read.values())
                for ipt in range(npoint_sent, last_point_read):
                    self.__debug("Send point number %d", ipt)
                    queue.put((read_spectras.pop(ipt), read_stats.pop(ipt)))
                npoint_sent = last_point_read

                gevent.sleep(0.2)

        except StopIteration:
            pass
        except Exception as exc:
            queue.put(exc)
            raise
        finally:
            queue.put(StopIteration)

    @property
    def last_pixel_triggered(self):
        return self.__last_point_seen
