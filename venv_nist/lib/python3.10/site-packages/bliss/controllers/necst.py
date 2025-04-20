import numpy

from bliss.controllers.counter import CounterController
from bliss.common.counter import Counter
from bliss.scanning.chain import AcquisitionSlave
from bliss.controllers.motors.icepap import comm
from bliss.comm.tcp import Command


class Necst:
    def __init__(self, name, config):
        hostname = config.get("host")
        self._cnx = Command(hostname, 5000, eol="\n")
        self._cnt_counter_controller = NecstCounterController(name, self, config)

        # comm._command(self._cnx, "#CNTCFG CNT15 50OHM ON FILT OFF SRC CLK1 TRIG OUT1 FALL GATE DI1 START SOFT STOP SOFT CLEAR OMODE GATE")
        # comm._command(self._cnx, "#CNTCFG CNT16 50OHM ON FILT OFF SRC CLK1 TRIG OUT1 FALL GATE OFF START SOFT STOP SOFT CLEAR OMODE GATE")
        # comm._command(self._cnx, "#DICFG DI1 50OHM ON")
        # comm._command(self._cnx, "#DICFG DI2 50OHM ON")
        # comm._command(self._cnx, "#DOCFG DO1 SRC OUT16")

        comm._command(self._cnx, "#CLKCFG CLK1 1MHZ")
        comm._command(
            self._cnx,
            "#CNTCFG CNT16 50OHM ON FILT OFF SRC CLK1 TRIG OUT1 FALL GATE OFF START SOFT STOP SOFT CLEAR OMODE GATE",
        )
        comm._command(self._cnx, "#SOFTSIG ENB CNT16")
        comm._command(self._cnx, "#DICFG DI1 50OHM ON")
        comm._command(self._cnx, "#DOCFG DO1 SRC OUT16")

    def close(self):
        self.s.close()

    @property
    def counters(self):
        return self._cnt_counter_controller.counters


class NecstAcquisitionSlave(AcquisitionSlave):
    def __init__(self, necst, count_time=1.0):
        self._necst = necst
        self.count_time = count_time
        super().__init__(
            necst._cnt_counter_controller,
            trigger_type=AcquisitionSlave.SOFTWARE,
            prepare_once=False,
            start_once=False,
        )

    def prepare(self):
        count_time = self.count_time
        cnx = self._necst._cnx

        counter_cmd = ["CNT%d" % cnt.ch for cnt in self._counters]
        cmd = "SOFTSIG ENB %s" % " ".join(counter_cmd)
        comm._command(cnx, cmd)

        counter_cmd = ["CNT%d" % cnt.ch for cnt in self._counters]
        cmd = "SOFTSIG CLR %s" % " ".join(counter_cmd)
        comm._command(cnx, cmd)

        cmd = f"CNTCFG CNT16 PRESET {int(count_time*1e6)}"
        comm._command(cnx, cmd)

    def start(self):
        cnx = self._necst._cnx
        counter_cmd = ["CNT%d" % cnt.ch for cnt in self._counters]
        cmd = "SOFTSIG START %s" % " ".join(counter_cmd)
        comm._command(cnx, cmd)

    def trigger(self):
        cnx = self._necst._cnx
        comm._command(cnx, "SOFTSIG START CNT16")

        while True:
            # WARNING: the value is returned in hexadecimal
            reply = comm._command(cnx, "?CNTSTAT CNT16")
            val = int(reply, 16)
            # bit0:  counter/timer running (1) or not (0)
            # bit31: timer (1) or counter (0)
            if not (val & 0x01):
                break

        channels_values = {}
        for cnt in self._counters:
            cnt_value = comm._command(cnx, f"?CNTVAL CNT{cnt.ch} LATCH")
            cnt_val = int(cnt_value, 16)
            channels_values[cnt.name] = numpy.array([cnt_val], dtype=numpy.float64)

        self.channels.update(channels_values)

    def stop(self):
        pass


class NecstCounterController(CounterController):
    def __init__(self, name, controller, config):
        super().__init__(name)
        self._ctrl = controller
        cnx = self._ctrl._cnx
        # ## NECSTID121
        # comm._command(self._cnx, "#CNTCFG CNT1 50OHM OFF FILT OFF SRC INPUT TRIG OUT1 FALL GATE DI1 START SOFT STOP SOFT OMODE GATE")
        # comm._command(self._cnx, "#CNTCFG CNT2 50OHM OFF FILT OFF SRC INPUT TRIG OUT1 FALL GATE DI1 START SOFT STOP SOFT OMODE GATE")
        # comm._command(self._cnx, "#CNTCFG CNT3 50OHM OFF FILT OFF SRC INPUT TRIG OUT1 FALL GATE DI1 START SOFT STOP SOFT OMODE GATE PRESET 0")
        # comm._command(self._cnx, "#CNTCFG CNT4 50OHM OFF FILT OFF SRC INPUT TRIG OUT1 FALL GATE DI1 START SOFT STOP SOFT OMODE GATE")
        # comm._command(self._cnx, "#CNTCFG CNT5 50OHM OFF FILT OFF SRC INPUT TRIG OUT1 FALL GATE DI1 START SOFT STOP SOFT OMODE GATE")
        # comm._command(self._cnx, "#CNTCFG CNT6 50OHM OFF FILT OFF SRC INPUT TRIG OUT1 FALL GATE DI1 START SOFT STOP SOFT OMODE GATE")
        # comm._command(self._cnx, "#CNTCFG CNT7 50OHM ON FILT OFF SRC INPUT TRIG OUT1 FALL GATE DI1 START SOFT STOP SOFT OMODE GATE")
        # comm._command(self._cnx, "#CNTCFG CNT8 50OHM ON FILT OFF SRC INPUT TRIG OUT1 FALL GATE DI1 START SOFT STOP SOFT OMODE GATE")
        # comm._command(self._cnx, "#CNTCFG CNT11 50OHM ON FILT OFF SRC INPUT TRIG OUT1 FALL GATE DI1 START SOFT STOP SOFT OMODE GATE")
        # comm._command(self._cnx, "#CNTCFG CNT12 50OHM ON FILT OFF SRC INPUT TRIG OUT1 FALL GATE DI1 START SOFT STOP SOFT OMODE GATE")
        # comm._command(self._cnx, "#CNTCFG CNT15 50OHM ON FILT OFF SRC CLK1 TRIG OUT1 FALL GATE DI1 START SOFT STOP SOFT CLEAR OMODE GATE")
        # comm._command(self._cnx, "#CNTCFG CNT16 50OHM ON FILT OFF SRC CLK1 TRIG OUT1 FALL GATE OFF START SOFT STOP SOFT CLEAR OMODE GATE")
        # ## NECSTID122
        # comm._command(self._cnx, "#CNTCFG CNT1 50OHM OFF FILT OFF SRC INPUT TRIG OUT1 FALL GATE DI1 START SOFT STOP SOFT OMODE GATE")
        # comm._command(self._cnx, "#CNTCFG CNT2 50OHM OFF FILT OFF SRC INPUT TRIG OUT1 FALL GATE DI1 START SOFT STOP SOFT OMODE GATE")
        # comm._command(self._cnx, "#CNTCFG CNT3 50OHM OFF FILT OFF SRC INPUT TRIG OUT1 FALL GATE DI1 START SOFT STOP SOFT OMODE GATE PRESET 0")
        # comm._command(self._cnx, "#CNTCFG CNT4 50OHM OFF FILT OFF SRC INPUT TRIG OUT1 FALL GATE DI1 START SOFT STOP SOFT OMODE GATE")
        # comm._command(self._cnx, "#CNTCFG CNT5 50OHM OFF FILT OFF SRC INPUT TRIG OUT1 FALL GATE DI1 START SOFT STOP SOFT OMODE GATE")
        # comm._command(self._cnx, "#CNTCFG CNT6 50OHM OFF FILT OFF SRC INPUT TRIG OUT1 FALL GATE DI1 START SOFT STOP SOFT OMODE GATE")
        # comm._command(self._cnx, "#CNTCFG CNT7 50OHM OFF FILT OFF SRC INPUT TRIG OUT1 FALL GATE DI1 START SOFT STOP SOFT OMODE GATE")
        # comm._command(self._cnx, "#CNTCFG CNT8 50OHM OFF FILT OFF SRC INPUT TRIG OUT1 FALL GATE DI1 START SOFT STOP SOFT OMODE GATE")
        # comm._command(self._cnx, "#CNTCFG CNT11 50OHM OFF FILT OFF SRC INPUT TRIG OUT1 FALL GATE DI1 START SOFT STOP SOFT OMODE GATE")
        # comm._command(self._cnx, "#CNTCFG CNT12 50OHM OFF FILT OFF SRC INPUT TRIG OUT1 FALL GATE DI1 START SOFT STOP SOFT OMODE GATE")
        for cnt_cfg in config.get("counters", []):
            counter_name = cnt_cfg["counter_name"]
            ch = cnt_cfg["ch"]
            cnt = Counter(counter_name, self)
            cnt.ch = ch
            cmd = "CNTCFG CNTX 50OHM XXX FILT OFF SRC INPUT TRIG OUT1 FALL GATE DI1 START SOFT STOP SOFT OMODE GATE"
            cmd_split = cmd.split(" ")
            cnt_nb = f"CNT{cnt.ch}"
            cmd_split[1] = cnt_nb
            cmd_split[3] = cnt_cfg["50Ohm"]
            cmd = " ".join(cmd_split)
            # print(f"{counter_name} {cmd}")
            comm._command(cnx, cmd)

    def get_default_chain_parameters(self, acq_params, ctrl_params):
        return {"count_time": acq_params.get("count_time")}

    def get_acquisition_object(self, acq_params, ctrl_params, parent_acq_params):
        return NecstAcquisitionSlave(self._ctrl, **acq_params)
