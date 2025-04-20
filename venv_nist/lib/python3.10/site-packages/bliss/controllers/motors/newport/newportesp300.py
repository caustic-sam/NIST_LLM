import gevent

from bliss import global_map
from bliss.controllers.motor import Controller
from bliss.common.axis.state import AxisState
from bliss.comm.util import get_comm, SERIAL
from bliss.comm.serial import SerialTimeout

from bliss.common.utils import object_method, rounder
from bliss.common.logtools import log_debug


class NewportESP300(Controller):

    HOME_SEARCH_METHOD = {
        0: "Find +0 Position Count",
        1: "Find Home and Index Signals",
        2: "Find Home Signal",
        3: "Find Positive Limit Signal",
        4: "Find Negative Limit Signal",
        5: "Find Positive Limit and Index Signals",
        6: "Find Negative Limit and Index Signals",
    }

    MOTOR_TYPES = {
        0: "motor type undefined (default)",
        1: "DC servo motor (single analog channel)",
        2: "step motor (digital control)*",
        3: "commutated step motor (analog control)",
        4: "commutated brushless DC servo motor",
    }

    PID_PARS = {
        "dc_proportional_gain": "KP",
        "dc_integral_gain": "KI",
        "dc_derivative_gain": "KD",
        "dc_following_error": "FE",
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.comm = None

        self.__ctrl_version = "UNKNOWN"
        self.__address = dict()
        self.__stage = dict()
        self.__state = dict()

    def initialize(self):
        config = self.config.config_dict
        self.comm = get_comm(config, SERIAL, eol="\r\n")

        global_map.register(self, children_list=[self.comm])

    def initialize_hardware(self):
        self.__ctrl_version = self.raw_read("VE")
        log_debug(self, "version", self.__ctrl_version)

        self.clear_errors()

    def clear_errors(self):
        # --- read last errors to clear it (buffer of 10)
        for idx in range(10):
            try:
                self.comm.write_readline(b"TE?\r\n", timeout=0.5)
            except SerialTimeout:
                continue
        self.comm.flush()

    def reset(self):
        self.raw_send("RS")
        self.initialize_hardware()

    def initialize_axis(self, axis):
        addr = axis.config.get("address", int, 1)
        self.__address[axis.name] = addr
        axis.address = addr

    def initialize_hardware_axis(self, axis):
        stage = self.axis_read(axis, "ID")
        if stage == "Unknown":
            raise RuntimeError("NewportESP300 : axis #{axis.address} is not recognized")
        self.__stage[axis.name] = stage
        self.set_on(axis)
        self.__state[axis.name] = AxisState("READY")

    def set_velocity(self, axis, new_vel):
        self.axis_send(axis, "VA", new_vel)

    def read_velocity(self, axis):
        vel = self.axis_read(axis, "VA")
        return float(vel)

    def set_acceleration(self, axis, new_acc):
        self.axis_send(axis, "AC", new_acc)
        self.axis_send(axis, "AG", new_acc)

    def read_acceleration(self, axis):
        acc = self.axis_read(axis, "AC")
        return float(acc)

    def read_position(self, axis):
        pos = self.axis_read(axis, "TP")
        return float(pos)

    def state(self, axis):
        axnum = axis.address - 1
        state = AxisState()

        # --- motion done flag
        done = self.axis_read(axis, "MD")
        if int(done) == 0:
            state.set("MOVING")

        # --- hardware status
        value = self.raw_read("PH")
        status = [int(val.replace("H", "").strip(), 16) for val in value.split(",")]
        if status[0] & (1 << axnum):
            state.set("LIMPOS")
        if status[0] & (1 << (8 + axnum)):
            state.set("LIMNEG")
        if status[0] & (1 << (16 + axnum)):
            state.set("FAULT")
        if status[1] & (1 << axnum):
            state.set("HOME")

        # --- nothing found, check power status
        if not len(state.current_states_names):
            power = self.axis_read(axis, "MO")
            if int(power) == 0:
                state.set("OFF")

        # --- nothing found, set state to ready
        if not len(state.current_states_names):
            state.set("READY")
            if "MOVING" in self.__state[axis.name]:
                gevent.sleep(0.1)

        self.__state[axis.name] = state
        return state

    def home_search(self, axis, switch):
        self.axis_send(axis, "OR")

    def home_state(self, axis):
        return self.state(axis)

    def start_one(self, motion):
        position = rounder(0.000001, motion.target_pos)
        self.axis_send(motion.axis, "PA", position)

    def stop(self, axis):
        self.axis_send(axis, "ST")

    def set_on(self, axis):
        self.axis_send(axis, "MO")

    def set_off(self, axis):
        self.axis_send(axis, "MF")

    @object_method(types_info=("None", "None"))
    def read_stage(self, axis):
        stage = self.__stage[axis.name]
        print(f"STAGE : {stage}")

    @object_method(types_info=("None", ("float", "float")))
    def read_limits(self, axis):
        low_limit = self.axis_read(axis, "SL")
        high_limit = self.axis_read(axis, "SR")
        return (low_limit, high_limit)

    @object_method(types_info=("None", "None"))
    def configure_limits(self, axis):
        (low, high) = self.read_limits(axis)
        axis.dial_limits = (low, high)

    @object_method(types_info=("None", "int"))
    def read_home_search_method(self, axis):
        ans = self.axis_read(axis, "OM")
        ans = int(ans)
        txt = self.HOME_SEARCH_METHOD[ans]
        print(f"Home Search Method is: {txt}")
        return ans

    @object_method(types_info=("int", "None"))
    def set_home_search_method(self, axis, value):
        txt = self.HOME_SEARCH_METHOD.get(value, None)
        if txt is None:
            raise ValueError("Invalid home search method")
        print(f"Set Home Search Method to: {txt}")
        self.axis_send(axis, "OM", value)

    @object_method(types_info=("None", "None"))
    def read_motor_type(self, axis):
        ans = self.axis_read(axis, "QM")
        txt = self.MOTOR_TYPES.get(int(ans), "Unknwon")
        print(f"Motor type : {txt}")

    @object_method(types_info=("None", "dict"))
    def read_pid(self, axis):
        pars = dict()
        for (parameter, command) in self.PID_PARS.items():
            pars[parameter] = self.axis_read(axis, command)
        return pars

    @object_method(types_info=("dict", "None"))
    def set_pid(self, axis, pars):
        inpars = self.read_pid(axis)
        setpars = dict()
        for (parameter, value) in pars.items():
            if parameter not in inpars:
                raise ValueError(f"Invalid parameter name <{parameter}>")
            if value != inpars[parameter]:
                setpars[parameter] = value
        if not len(setpars):
            print("No parameter change.")
            return

        for (parameter, value) in setpars.items():
            print(f"Set {parameter} = {value}")
            command = self.HWPARS[parameter]
            self.axis_send(axis, command, value)

        print("Validate parameters")
        self.axis_send(axis, "UF")

    def __info__(self):
        info = "CONTROLLER NEWPORT ESP300:\n"
        info += f"\tVERSION: {self.controller_version}\n"
        info += f"\tCOMM: {self.comm}"
        return info

    def get_axis_info(self, axis):
        stage = self.__stage[axis.name]
        info = "AXIS:\n"
        info += f"\taddress: {axis.address}\n"
        info += f"\tstage: {stage}"
        return info

    @property
    def controller_version(self):
        return self.__ctrl_version

    def axis_read(self, axis, command):
        cmd = f"{axis.address}{command}"
        return self.raw_read(cmd)

    def raw_read(self, command):
        cmd = command.encode()
        ser_cmd = cmd + b"?\r\n"
        ser_ans = self.comm.write_readline(ser_cmd)
        return ser_ans.decode().strip()

    def axis_send(self, axis, command, parameter=None):
        cmd = f"{axis.address}{command}"
        self.raw_send(cmd, parameter)

    def raw_send(self, command, parameter=None):
        if parameter is not None:
            cmd = f"{command}{parameter}\r\n"
        else:
            cmd = f"{command}\r\n"
        ser_cmd = cmd.encode()
        self.comm.write(ser_cmd)

        err = self.raw_read("TB")
        try:
            (err_code, err_time, err_txt) = err.split(",")
            err_code = int(err_code)
            if err_code > 0:
                raise RuntimeError(f"NewportESP300 reply to {command} : {err_txt}")
        except ValueError:
            if "NO ERROR" not in err:
                raise RuntimeError(f"NewportESP300 reply to {command}: {err}")
