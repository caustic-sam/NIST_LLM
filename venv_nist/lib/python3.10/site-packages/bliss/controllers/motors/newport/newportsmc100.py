import gevent

from bliss import global_map
from bliss.controllers.motor import Controller
from bliss.common.axis.state import AxisState
from bliss.comm.util import get_comm, SERIAL
from bliss.comm.serial import SerialTimeout

from bliss.common.utils import object_method, rounder
from bliss.common.logtools import log_debug


class NewportSMC100(Controller):

    HWSTATES = {
        0x0A: ("NOT REFERENCED from reset", "NOTREFERENCED"),
        0x0B: ("NOT REFERENCED from HOMING", "NOTREFERENCED"),
        0x0C: ("NOT REFERENCED from CONFIGURATION", "NOTREFERENCED"),
        0x0D: ("NOT REFERENCED from DISABLE", "NOTREFERENCED"),
        0x0E: ("NOT REFERENCED from READY", "NOTREFERENCED"),
        0x0F: ("NOT REFERENCED from MOVING", "NOTREFERENCED"),
        0x10: ("NOT REFERENCED ESP stage error", "NOTREFERENCED"),
        0x11: ("NOT REFERENCED from JOGGING", "NOTREFERENCED"),
        0x14: ("CONFIGURATION", "CONFIGURATION"),
        0x1E: ("HOMING commanded from RS-232-C", "MOVING"),
        0x1F: ("HOMING commanded by SMC-RC", "MOVING"),
        0x28: ("MOVING", "MOVING"),
        0x32: ("READY from HOMING", "READY"),
        0x33: ("READY from MOVING", "READY"),
        0x34: ("READY from DISABLE", "READY"),
        0x35: ("READY from JOGGING", "READY"),
        0x3C: ("DISABLE from READY", "DISABLED"),
        0x3D: ("DISABLE from MOVING", "DISABLED"),
        0x3E: ("DISABLE from JOGGING", "DISABLED"),
        0x46: ("JOGGING from READY", "MOVING"),
        0x47: ("JOGGING from DISABLE", "MOVING"),
    }

    HWERRORS = {
        1 << 0: ("Negative end of run", "LIMNEG"),
        1 << 1: ("Positive end of run", "LIMPOS"),
        1 << 2: ("Peak current limit", "FAULT"),
        1 << 3: ("RMS current limit", "FAULT"),
        1 << 4: ("Shot circuit detection", "FAULT"),
        1 << 5: ("Following error", "FAULT"),
        1 << 6: ("Homing time out", "FAULT"),
        1 << 7: ("Wrong ESP stage", "FAULT"),
        1 << 8: ("DC voltage too low", "FAULT"),
        1 << 9: ("80W output power exceeded", "FAULT"),
    }

    HWPARS = {
        "low_pass_filter": "FD",
        "following_error": "FE",
        "friction_compensation": "FF",
        "jerk_time": "JR",
        "derivative_gain": "KD",
        "integral_gain": "KI",
        "proportionnal_gain": "KP",
        "velocity_feed_forward": "KV",
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.comm = None

        self.__ctrl_version = "UNKNOWN"
        self.__address = dict()
        self.__axis_state = AxisState()
        self.__axis_state.create_state("NOTREFERENCED", "axis need a homing")
        self.__axis_state.create_state("CONFIGURATION", "axis in configuration mode")
        self.__referenced = dict()

    def initialize(self):
        config = self.config.config_dict
        self.comm = get_comm(config, SERIAL, baudrate=57600, eol="\r\n")

        global_map.register(self, children_list=[self.comm])

    def initialize_hardware(self):
        try:
            self.__ctrl_version = self.raw_read("VE")
        except (RuntimeError, SerialTimeout):
            self.__ctrl_version = self.raw_read("VE")
        log_debug(self, "version", self.__ctrl_version)

    def initialize_axis(self, axis):
        addr = axis.config.get("address", int, 1)
        if addr in self.__address.values():
            raise ValueError(
                f"NewportSMC100 has multiple axis with address {addr}. Check config."
            )
        self.__address[axis.name] = addr
        axis.address = addr

    def initialize_hardware_axis(self, axis):
        self.__referenced[axis.name] = False
        self.state(axis)

    def set_velocity(self, axis, new_vel):
        if self.__referenced[axis.name]:
            self.axis_send(axis, "VA", new_vel)

    def read_velocity(self, axis):
        vel = self.axis_read(axis, "VA")
        return float(vel)

    def set_acceleration(self, axis, new_acc):
        if self.__referenced[axis.name]:
            self.axis_send(axis, "AC", new_acc)

    def read_acceleration(self, axis):
        acc = self.axis_read(axis, "AC")
        return float(acc)

    def read_position(self, axis):
        pos = self.axis_read(axis, "TP")
        return float(pos)

    def state(self, axis):
        (state, smc_state, smc_errors) = self._get_states(axis)
        if "NOTREFERENCED" not in state:
            self.__referenced[axis.name] = True
        return state

    def _get_states(self, axis):
        state = self.__axis_state.new()
        raw_state = self.axis_read(axis, "TS")
        error_code = int(raw_state[0:4], 16)
        ctrl_state = int(raw_state[4:6], 16)

        (smc_state, bliss_state) = self.HWSTATES.get(ctrl_state, ("UNKNOWN", "UNKNOWN"))
        state.set(bliss_state)

        smc_errors = list()
        for code in self.HWERRORS:
            if error_code & code:
                (txt_error, bliss_state) = self.HWERRORS[code]
                state.set(bliss_state)
                smc_errors.append(txt_error)

        # if state is not referenced, need to add READY state
        # otherwise we cannot start a home search !!
        if "NOTREFERENCED" in state:
            state.set("READY")

        return (state, smc_state, smc_errors)

    def home_search(self, axis, switch):
        state = self.state(axis)
        if "NOTREFERENCED" not in state:
            raise RuntimeError(
                "home only allowed if in NOTREFERENCED state. Use axis.reset_controller() first."
            )
        self.axis_send(axis, "OR")

    def home_state(self, axis):
        return self.state(axis)

    def start_one(self, motion):
        address = motion.axis.address
        position = rounder(0.00001, motion.target_pos)
        self.raw_send("PA", position, address)

    def stop(self, axis):
        self.axis_send(axis, "ST")

    def set_on(self, axis):
        self.axis_send(axis, "MM", 1)

    def set_off(self, axis):
        self.axis_send(axis, "MM", 0)

    @object_method(types_info=("None", "None"))
    def read_status(self, axis):
        (state, smc_state, smc_errors) = self._get_states(axis)
        print(f"CONTROLLER STATE : {smc_state}")
        if len(smc_errors):
            print("CONTROLLER ERRORS :")
            for txt in smc_errors:
                print(f" - {txt}")
        else:
            print("CONTROLLER ERRORS : NONE.")
        print(f"BLISS AXIS STATE :\n  {state}\n")

    @object_method(types_info=("None", "None"))
    def read_stage(self, axis):
        stage = self.axis_read(axis, "ID")
        print(f"STAGE : {stage}")

    @object_method(types_info=("None", ("float", "float")))
    def read_limits(self, axis):
        low_limit = self.axis_read(axis, "SL")
        high_limit = self.axis_read(axis, "SR")
        return (low_limit, high_limit)

    @object_method(types_info=("None", "dict"))
    def read_parameters(self, axis):
        pars = dict()
        for (parameter, command) in self.HWPARS.items():
            pars[parameter] = self.axis_read(axis, command)
        return pars

    @object_method(types_info=("dict", "None"))
    def write_parameters(self, axis, pars, save=False):
        inpars = self.read_parameters(axis)
        setpars = dict()
        for (parameter, value) in pars.items():
            if parameter not in inpars:
                raise ValueError(f"Invalid parameter name <{parameter}>")
            if value != inpars[parameter]:
                setpars[parameter] = value
        if not len(setpars):
            print("No parameter change.")
            return

        print("Go to DISABLED state")
        self.set_off(axis)
        for (parameter, value) in setpars.items():
            print(f"Set {parameter} = {value}")
            command = self.HWPARS[parameter]
            self.axis_send(axis, command, value)
        print("Go to READY state")
        self.set_on(axis)

    @object_method(types_info=("None", "None"))
    def configure_limits(self, axis):
        (low, high) = self.read_limits(axis)
        axis.dial_limits = (low, high)

    @object_method(types_info=("None", "None"))
    def reset_controller(self, axis):
        self.axis_send(axis, "RS")
        self.initialize_hardware()

    @object_method(types_info=("None", "None"))
    def configure_stage(self, axis):
        print(">>> Reset controller")
        self.reset_controller(axis)
        (_, state, _) = self._get_states(axis)
        print(f"Controller state is: {state}")
        print(">>> Enter configuration state")
        self.axis_send(axis, "PW", 1)
        (_, state, _) = self._get_states(axis)
        print(f"Controller state is: {state}")
        print(">>> Update ESP stage information")
        self.axis_send(axis, "ZX", 2)
        print(">>> Enable ESP stage check")
        self.axis_send(axis, "ZX", 3)
        print(">>> Leave configuration state")
        self.axis_send(axis, "PW", 0)
        (_, state, _) = self._get_states(axis)
        print(f"Controller state is: {state}")
        self.read_stage(axis)

    def __info__(self):
        info = "CONTROLLER NEWPORT SMC100:\n"
        info += f"\tVERSION: {self.controller_version}\n"
        info += f"\tCOMM: {self.comm}"
        return info

    def get_axis_info(self, axis):
        info = f"AXIS:\n\taddress: {axis.address}"
        return info

    @property
    def controller_version(self):
        return self.__ctrl_version

    def axis_read(self, axis, command):
        return self.raw_read(command, axis.address)

    def raw_read(self, command, address=1):
        cmd = f"{address}{command}"
        cmd = cmd.encode()
        ser_cmd = cmd + b"?\r\n"
        ser_ans = self.comm.write_readline(ser_cmd)
        if not ser_ans.startswith(cmd):
            raise RuntimeError("NewportSMC100: no command echo on serial comm")

        ans = ser_ans[len(cmd) :].decode().strip()
        return ans

    def axis_send(self, axis, command, parameter=None):
        self.raw_send(command, parameter, axis.address)

    def raw_send(self, command, parameter=None, address=1):
        if parameter is not None:
            cmd = f"{address}{command}{parameter}\r\n"
        else:
            cmd = f"{address}{command}\r\n"
        ser_cmd = cmd.encode()
        self.comm.write(ser_cmd)
        try:
            err = self.raw_read("TE", address)
        except SerialTimeout:
            gevent.sleep(1.0)
            err = self.raw_read("TE", address)
        if err != "@":
            errtxt = self.raw_read(f"TB{err[0]}", address)
            raise RuntimeError(f"NewportSMC100: {errtxt}")
