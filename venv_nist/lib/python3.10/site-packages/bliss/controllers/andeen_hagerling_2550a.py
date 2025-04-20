from bliss.common.tango import DeviceProxy
from bliss import global_map
from bliss.comm.util import get_comm
from bliss.controllers.counter import SamplingCounterController
from bliss.common.counter import SamplingCounter
from bliss.common.logtools import log_debug


"""
Andeen-Hagerling AH2550A Ultra-Precision Capacitance Bridge

- class: AH2550A
  module: andeen_hagerling_2550a
  name: ah
  gpib:                          
      url: enet://gpibbcu2
      pad: 28
  counters:
  - counter_name: C
    measure: capacitance 
  - counter_name: V
    measure: voltage
  - counter_name: L
    measure: loss

"""


STB = {
    1: "Oven temperature is abnormal",
    2: "Command Error",
    4: "User command is executed",
    8: "Line Power to the bridge comes on",
    16: "Ready for command",
    32: "Execution error",
    64: "Master summary status",
    128: "Message available in the GPIB output buffer",
}


MEASURE_KEYS = {"capacitance": "C=", "loss": "L=", "voltage": "V="}


class AH2550ACounter(SamplingCounter):
    def __init__(self, name, controller, tag, **kwargs):
        super().__init__(name, controller, **kwargs)
        self._tag = tag
        log_debug(self, "Counter {0} created: tag:{1}".format(name, tag))


class AH2550A(SamplingCounterController):
    def __init__(self, name, config):

        super().__init__(name)
        # High frequency acquisition loop
        self.max_sampling_frequency = None

        yml_tango = config.get("tango", None)
        tg_device = None
        if yml_tango is not None:
            tg_device = yml_tango.get("url", None)
        if tg_device is not None:
            self._is_tango = True
            self.comm = DeviceProxy(tg_device)
        else:
            self._is_tango = False
            self.comm = get_comm(config, eol="\n")

        global_map.register(self, children_list=[self.comm], tag=name)

        for cnt in config.get("counters", list()):
            if "measure" in cnt.keys():
                if cnt["measure"].casefold() in MEASURE_KEYS:
                    cnt["tag"] = MEASURE_KEYS[cnt["measure"].casefold()]
                else:
                    print(
                        "WARNING: {0} measure unknown, {1} counter channel will be ignored".format(
                            cnt["measure"], cnt["counter_name"]
                        )
                    )
                    continue

            self.create_counter(AH2550ACounter, cnt["counter_name"], tag=cnt["tag"])

    def __del__(self):
        if not self._is_tango:
            self.comm.close()

    def __info__(self):
        info_str = "Andeen-Hagerling AH2550A Ultra-Precision Capacitance Bridge\n\n"
        if self._is_tango:
            info_str += f"Using {self.comm}\n\n"
        idn = self.idn()
        for msg in idn:
            info_str += msg + "\n"

        info_str += "\n"

        msg = self.measure()

        meas = msg.split("=")

        info_str += f"Capacitance: {meas[1][:-1]}\n"
        info_str += f"Loss:        {meas[2][:-1]}\n"
        info_str += f"Voltage:     {meas[3]}\n"

        return info_str

    def measure(self):
        if self._is_tango:
            return self.comm.measure_raw
        else:
            return self._io("Q")[0]

    def idn(self):
        if self._is_tango:
            return self.comm.idn
        else:
            return self._io("*IDN?", nb_lines=4)

    '''
    def status (self):
        """
        Return interpretation of status
        Not behaving as expected ...
        """
        sta = int(self._io ("*STB?")[0].split()[3])
        if sta & 16:
            return "READY"
        else:
            return "not READY"
    '''

    def _io(self, command, value=None, nb_lines=1):
        cmd = f"{command} {value if value is not None else ''}\n"
        reply = self.comm.write_readlines(cmd.encode(), nb_lines)
        return list(map(lambda x: x.decode(), reply))

    # SamplingCounterController methods

    def read_all(self, *counters):
        """Return the values of the given counters as a list.

        If possible this method should optimize the reading of all counters at once.
        """
        measure = self.measure()
        values = list()

        for cnt in counters:
            try:
                res = measure.split(cnt._tag)[1].split()
                assert len(res) >= 2
            except Exception as e:
                print(measure)
                raise e
            cnt.unit = res[1]
            values.append(float(res[0]))

        return values
