# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.


"""
Bliss controller for ethernet Galil DMC controller for DC motors.
Also supports Piezomotor controller embeding a galil controller.
"""

import tabulate

from gevent import lock

from bliss.controllers.motor import Controller

from bliss import global_map
from bliss.common.logtools import log_debug, log_warning, log_error
from bliss.common.axis.state import AxisState
from bliss.comm.util import get_comm, TCP
from bliss.common.greenlet_utils import protect_from_kill

from bliss.common.utils import object_method

# from bliss.shell.cli import getval

from .galil_config import MODEL_DICT
from . import galil_errors


class GalilDMC(Controller):
    """
    GalilDMC Controller main class.
    """

    def __init__(self, *args, **kwargs):
        Controller.__init__(self, *args, **kwargs)
        self.sock = None
        self.model_id = None
        self.model_serie = None
        self.axes_count_config = None
        self.axes_count_hdw = None
        self.switches_level = None
        print("GalilDMC __init__   (SW = NONE...)")

        self.socket_lock = lock.Semaphore()

    def initialize(self):
        """
        Called at session startup
        Tasks related to BLISS controller configuration.
        """
        log_debug(self, "initialize")
        print(f"-------------initialize({self.name})")
        self.sock = get_comm(self.config.config_dict, ctype=TCP, port=23)
        global_map.register(self, children_list=[self.sock])

        # Model ID and serie
        self.model_id = self.config.get("model_id", str, "UNKNOWN")
        model_str = self.get_firmware().split(" ")[0]
        if model_str not in MODEL_DICT.keys():
            log_error(self, "Mismatch in controller model:")
            log_error(self, f"   from controller: {model_str}")
            log_error(self, f"   from config: {self.model_id }")

        try:
            self.model_serie = MODEL_DICT[self.model_id]
        except KeyError:
            log_warning(self, f"cannot determnine model serie for {self.model_id}...")

        if self.model_id not in model_str:
            log_warning(
                self,
                f"it seems your controller ({model_str})",
                f"is not the same model as declared in config ({self.model_id}).",
            )

        self.switches_level = self.config.get("limit_switches", str, "UNKNOWN")

    def initialize_hardware(self):
        """
        Controller initialization.
        Called *once* at first access to any of the axes.
        Tasks related to Hardware controller initialization (ex: to activate closed-loop)
        """
        log_debug(self, f"initialize_hardware({self.name})")

    def finalize(self):
        """
        blabla
        """
        log_debug(self, "finalize()")
        self.close()

    def close(self):
        """
        blabla
        """
        log_debug(self, "close()")
        self.sock.close()

    def initialize_axis(self, axis):
        """
        Called at first access to <axis>.
        """
        print(f"----------------------initialize_axis({axis.name})")
        axis.channel = axis.config.get("channel")
        if axis.channel not in "ABCDEFGH":
            raise RuntimeError(
                f"Invalid channel {axis.channel}, should be one of: A,B,C,D,E,F,G,H"
            )
        axis.channel_idx = ord(axis.channel) - ord("A")
        if axis.channel_idx not in [0, 1, 2, 3, 4, 5, 6, 7]:
            raise RuntimeError(f"Invalid channel index {axis.channel_idx}")

    def initialize_hardware_axis(self, axis):
        """
        Called at first access to <axis>.

        NB: After initialize_hardware_axis(), velocity and accelerartion are set/re-read
        """
        print(f"---------------------------------initialize_hardware_axis({axis.name})")
        # Read AXIS config values and apply to axis

        #
        # self.set_motor_off(axis)
        # self.set_motor_on(axis)

    def initialize_encoder(self, encoder):
        """
        Check encoder channel
        """
        print(f"initialize_encoder({encoder.name})")
        encoder.channel = encoder.config.get("channel")
        if encoder.channel not in "ABCDEFGH":
            raise RuntimeError(
                "Invalid encoder channel, should be one of: A,B,C,D,E,F,G,H"
            )

    def set_on(self, axis):
        """
        NB: Called on a spec reconfig if used as BlissAxisManager
        """
        self.set_motor_on(axis)

    def set_off(self, axis):
        """
        NB: Called on a spec reconfig if used as BlissAxisManager
        """
        self.set_motor_off(axis)

    """
    Controller / Axis informations
    """

    def __info__(self):
        """
        Galil Specific informations.
        """
        info_str = "GALIL CONTROLLER:\n"
        th0 = self.get_com_info()[0]
        info_str += f"     {th0} \n"  # TH[0]
        info_str += f"     Firmware           = {self.get_firmware()}\n"
        info_str += f"     Inputs          TI = {self._galil_query('TI ?')}\n"
        info_str += (
            f"     Status Byte     TB = {self._galil_query('TB ?')}\n"  # controller
        )
        #        info_str += f"     Polarities lim_sw:{self.limit_switch}  home_sw:{self.home_switch}  latch:{self.latch_polarity}\n"
        info_str += f"     Sampling Period TM = {self._galil_query('TM ?')} µs\n"

        return info_str

    def get_axis_info(self, axis=None):
        """
        Return Controller specific information about <axis>
        """
        info_str = f"GALIL SPECIFIC VALUES FOR {axis.name} AXIS:\n"
        info_str += f"     Position     PA = {self.read_position(axis)}\n"
        info_str += f"     Encoder      TP = {self.read_encoder(axis)}\n"
        info_str += f"     Aux Enc.     TD = {self._galil_query('TDA')}\n"
        info_str += f"     Torque       TT = {self._galil_query('TTA')}\n"
        info_str += f"     Torque limit TL = {self._galil_query('TL ?')}\n"
        ts_dict = self.get_TS(axis)
        info_str += f"     Status   TS = {ts_dict['TS']}\n"
        info_str += self.format_ts_dict(ts_dict)
        #        info_str += f"      = {self._galil_query('')}\n"

        return info_str

    @object_method(types_info=("None", "string"))
    def get_axis_expert_info(self, axis=None):
        """
        Return a string of 'expert' only values.
        To be completed...
        """
        info_str = ""
        pid = self.get_axis_pid(axis)
        pid_kp = pid["kp"]
        pid_ki = pid["ki"]
        pid_kd = pid["kd"]
        info_str += f"              KP = {pid_kp}\n"
        info_str += f"              KI = {pid_ki}\n"
        info_str += f"              KD = {pid_kd}\n"

        return info_str

    @object_method(types_info=("None", "string"))
    def get_info(self, axis):
        """
        Method used by tango server.
        """
        return self.get_hw_info()

    def get_hw_info(self):
        """
        Return a set of usefull information about controller.
        Helpful to tune the device.
        """
        info_str = "GALIL CONTROLLER INFO (get_hw_info):"
        info_str += f"    status       TB = {self._galil_query('TB')}\n"
        info_str += f"    error code   TC = {self._galil_query('TC')}\n"
        info_str += f"    dual encoder TD = {self._galil_query('TD')}\n"
        info_str += f"    error        TE = {self._galil_query('TE')}\n"
        info_str += f"    comm        IA? = {self._galil_query('IA?')}\n"
        return info_str

    """
    Bliss Motor commands
    """

    def state(self, axis):
        """
        TA: Tell Amplifier error status
        TB: Tell status Byte
        TC: Tell error Code
        TE: Tell Error
        TS: Tell Switches
        SC: Stop Code
        _NO: Check if code is running 1 -> Thread 0 running

        NB: Use the AM trippoint to wait for motion complete between moves from embedded code.

        NB: From host code, Poll MG_BG<m> to determine if motion is complete
            GALIL_BS [26]: carrier.controller._galil_query("MG_BGA")
                 Out [26]: '0.0000'

        """
        states_list = list()

        ts_dict = self.get_TS(axis)

        if ts_dict["axis_moving"]:
            states_list.append("MOVING")
        else:
            states_list.append("READY")

        if ts_dict["fwd_lim_sw"]:
            states_list.append("LIMPOS")

        if ts_dict["rev_lim_sw"]:
            states_list.append("LIMNEG")

        state = AxisState(*states_list)
        return state

    def read_position(self, axis):
        """
        Return the current Target position of the motor.
        Command: PA - Position Absolute (read target/ setpoint)
        Related Command : PF - Position Formatting
        Related Command : TP - Tell Position (read encoder)
        """
        return float(self._galil_query(f"PA{axis.channel}=?"))

    def read_encoder(self, encoder):
        """
        Return Encoder Value in steps.
        Command: TP - Tell Position (encoder)
        Related Command: TD - Tell Dual encoder
        """
        return float(self._galil_query(f"TP{encoder.channel}"))

    def read_acceleration(self, axis):
        """
        AC command.
        """
        return int(self._galil_query(f"AC{axis.channel}=?"))

    def set_acceleration(self, axis, new_acc):
        """
        Set controller accelerartion AND deceleration.
        AC and DC commands.
        new_acc is in controller units.

        Acceleration must be an unsigned number in the range 1024 (2^10) to 1073740800 (2^30-1024).
        The parameters input will be rounded down to the nearest factor of 1024.
        Unit: counts per second squared.

        GALIL_BS [14]: carrier.controller._galil_query("ACA=73422")
        GALIL_BS [15]: carrier.controller._galil_query("ACA=?")
             Out [15]: '72704'
        73422 / 1024 = 71.701171
        71 * 1024 = 72704
        le compte est bon.
        """
        if new_acc < 1024 or new_acc > 1073740800:
            raise ValueError(f"Acceleration ({new_acc}) is out of bounds")

        self._galil_query(f"AC{axis.channel}={new_acc}")
        self._galil_query(f"DC{axis.channel}={new_acc}")

    def read_velocity(self, axis):
        return int(self._galil_query(f"SP{axis.channel}=?"))

    def set_velocity(self, axis, new_velocity):
        """
        Velocity sent to controller must be an unsigned number
        * in the range 0 to 22,000,000 for servo motors.
        * in the range 0 to  6,000,000 for stepper motors.

        The resolution of the SP command is dependent upon the update rate setting (TM).
        With the default rate of TM 1000 the resolution is 2 cnts/second.
        The equation to calculate the resolution of the SP command is:
                       resolution = 2*(1000/TM)
        example:
        With TM 250 the resolution of the SP command is 8 cnts/second
                       resolution = 2*(1000/250) = 8

        !! for the ICM-42100: Velocity is an unsigned number in the range of 0 to 50,000,000.
        The units are interpolated encoder counts per second.

        """
        if new_velocity < 0:
            raise ValueError(f"Invalid value for velocity: {new_velocity}")

        self._galil_query(f"SP{axis.channel}={new_velocity}")
        return self.read_velocity(axis)

    def home_search(self, axis, switch):
        """
        start home search.
        What if absolute encoder ?
        """
        # self._galil_query(f"OE{axis.channel}=0")  # why to change ???
        # self._galil_query(f"OE{axis.channel}=1")  # should be the default
        self._galil_query(f"SH{axis.channel}")
        # ??? a movement must be sent ???  (ex: JG 500)
        self._galil_query(f"FI{axis.channel}")
        self._galil_query(f"BG{axis.channel}")
        # ??? chck SC (stop code)

    def home_state(self, axis):
        """
        # reading home switch
        # home_switch = self.get_TS(axis)["home_sw"]
        """
        return self.state(axis)

    def prepare_move(self, motion):
        """
        Prepare movement: set target position of motion.axis to
        <motion.target_pos> absolute position.

        PA (Position Absolute) command.
        Position must be in [-2^32; +2^31-1]
        """
        self._galil_query(f"PA{motion.axis.channel}={motion.target_pos}")

    def start_one(self, motion):
        """
        BG (Begin) command.
        """
        self._galil_query(f"BG {motion.axis.channel}")

    def stop(self, axis):
        """
        ST command stops motion on the specified axis. Motors will come to
        a decelerated stop.
        """
        self._galil_query(f"ST{axis.channel}")

    """
    Programs
    """
    # DL DownLoad program host->controller
    # UL UpLoad program  controller -> host
    # XQ #<prog_label>,0 - eXecute program
    def load_program(self, prog_str):
        """
        Load <prog_str> as a program into controller.
        Command: DL - DownLoad Program

        The DL command transfers a data file from the host computer to the
        controller. Instructions in the file will be accepted as a data
        stream without line numbers.
        The file is terminated using <control> Z, <control> Q, <control> D, or '\'.

        This command will be rejected by Galil software if sent via the terminal.
        In order to download a program using a Galil software package, use that
        package's prescribed programming interface (I.E. GDK's Editor Tool).

        """
        pass

    """
    Galil protocol
    """

    def get_firmware(self):
        """
        Return firmware string read with '' command.
        # ctrl-Q ctrl-r ctrl-Q ctrl-v in emacs or BLISS shell
        This string includes model number.

        ex: DMC4010 Rev 1.3c-SER   (MLL CMCS Bestec deposition machine)
            DMC30010 Rev 1.2i-SER  (ID21 tripod (Galil packaged by Piezomotor))
            2xxx ?                 (MLL deposition machine)
        """
        return self._galil_query("")

    def get_com_info(self):
        """
        Return the list of strings returned by TH command.

        ex:
        ['CONTROLLER IP ADDRESS 169,254,33,110 ETHERNET ADDRESS 00-50-4C-20-6C-01',
         'IHA UDP PORT 23 TO IP ADDRESS 169,254,33,1 PORT 58769',
         'IHB TCP PORT 23 TO IP ADDRESS 169,254,33,2 PORT 38848',
         'IHC AVAILABLE ', ... 'IHH AVAILABLE']
        """
        ans = self._galil_query("TH")
        th_list = ans.split("\r\n")

        return th_list

    def get_qz(self):
        """
        Return QZ values as a list.
        QZ[0] : number of axes present
        QZ[1] : number of bytes in general block of data record (18 for the DMC-30000)
        QZ[2] : number of bytes in coordinate plane block of data record (16 for the DMC-30000)
        QZ[3] : number of bytes the axis block of data record (36 for the DMC-30000)
        """
        ans = self._galil_query("QZ")
        qz_list = ans.split(", ")
        return qz_list

    """
    PID
    KI 12,14,16,20       Specify a,b,c,d-axis integral
    KI 7                 Specify a-axis only
    KI ,,8               Specify c-axis only
    KI ?,?,?,?           Return A,B,C,D
    """

    def get_axis_pid(self, axis):
        """
        Return a dict of 3 keys : 'kp' 'ki' 'kd' for <axis>.
        """
        kp_ans = self._galil_query("KP ?,?,?,?,?,?,?,?")
        pid_kp = kp_ans.split(",")[axis.channel_idx]

        ki_ans = self._galil_query("KI ?,?,?,?,?,?,?,?")
        pid_ki = ki_ans.split(",")[axis.channel_idx]

        kd_ans = self._galil_query("KD ?,?,?,?,?,?,?,?")
        pid_kd = kd_ans.split(",")[axis.channel_idx]

        return {"kp": pid_kp, "ki": pid_ki, "kd": pid_kd}

    def set_motor_off(self, axis):
        """
        Switche motor OFF (MO command)
        """
        print(f"{self.name} set_motor_off({axis.name})  (MO)")
        self._galil_query(f"MO{axis.channel}")

    def set_motor_on(self, axis):
        """
        Switche motor ON (SH command)
        """
        print(f"{self.name} set_on({axis.name})  (SH)")
        self._galil_query(f"SH{axis.channel}")

    def get_TS(self, axis):
        """
        Return Axis status
        TS : Tell Switches byte
        ex: 45 = 32 + 8 + 4 + 1
            bits: 5   3   2   0
        """
        TS = int(self._galil_query(f"TS {axis.channel}"))
        ts_status = dict()
        ts_status["TS"] = TS
        ts_status["axis_moving"] = bool(TS & (1 << 7))  # Axis in motion if high
        ts_status["error_exceeded"] = bool(
            TS & (1 << 6)
        )  # Axis error exceeds error limit if high
        ts_status["motor_off"] = bool(TS & (1 << 5))  # Motor off
        # ts_status[""] = bool(TS & (1 << 4))              # Reserved

        if self.switches_level == "ACTIVE_LOW":
            fwd_lim_sw = not bool(
                TS & (1 << 3)
            )  # Forward Limit Switch Status inactive if HIGH
            rev_lim_sw = not bool(
                TS & (1 << 2)
            )  # Reverse Limit Switch Status inactive if HIGH
        else:
            fwd_lim_sw = bool(
                TS & (1 << 3)
            )  # Forward Limit Switch Status inactive if LOW
            rev_lim_sw = bool(
                TS & (1 << 2)
            )  # Reverse Limit Switch Status inactive if LOW

        ts_status["fwd_lim_sw"] = fwd_lim_sw
        ts_status["rev_lim_sw"] = rev_lim_sw
        ts_status["home_sw"] = bool(TS & (1 << 1))  # Home Switch Status
        ts_status["pos_latch"] = bool(TS & (1 << 0))  # Position latch has occurred

        return ts_status

    def format_ts_dict(self, ts_dict):
        """
        Return a table as a string to display TS axis status.
        Ex:
        """
        ts_labels = [
            " " * 17,
            "7(moving)",
            "6(err_ex.)",
            "5(mot_off)",
            "3(sw+)",
            "2(sw-)",
            "1(home)",
            "0(latch)",
        ]
        ts_values = [
            " " * 17,
            ts_dict["axis_moving"],
            ts_dict["error_exceeded"],
            ts_dict["motor_off"],
            ts_dict["fwd_lim_sw"],
            ts_dict["rev_lim_sw"],
            ts_dict["home_sw"],
            ts_dict["pos_latch"],
        ]
        tabulate.PRESERVE_WHITESPACE = True
        ts_table = tabulate.tabulate([ts_labels, ts_values], tablefmt="plain") + "\n"
        return ts_table

    def get_TB(self):
        """
        Return Controller Status
        TB: Tell status Byte
        """
        TB = int(self._galil_query("TB ?"))
        tb_status = dict()
        tb_status["executing_program"] = bool(
            TB & (1 << 7)
        )  # Executing application program
        tb_status["daisy_chained"] = bool(TB & (1 << 6))  # DMC-2000 only
        tb_status["contouring"] = bool(TB & (1 << 5))  # Contouring
        tb_status["exec_routine"] = bool(
            TB & (1 << 4)
        )  # Executing error or limit switch routine
        tb_status["input_interrupt"] = bool(TB & (1 << 3))  # Input interrupt enabled
        tb_status["exec_input_intr"] = bool(
            TB & (1 << 2)
        )  # Executing input interrupt routine
        #              = bool(TB & (1 << 1))                  # N/A
        tb_status["echo_on"] = bool(TB & (1 << 0))  # Echo on

        return tb_status

    def get_SC(self):
        """
        Return Controller stop code number + description string.
        """
        SC = self._galil_query("SC")
        sc_value = int(SC)
        sc_desc = galil_errors.galil_get_stop_code_str(sc_value)

        return (sc_value, sc_desc)

    @protect_from_kill
    def _galil_query(self, cmd, raw=False):
        """
        * encode <cmd>
        * add terminal character to <cmd>
        * send <cmd>
        * return decoded answer


        TODO: check LS command -> garbage in socket ???
        """

        # Check <cmd> is clean.
        if "\r" in cmd:
            raise ValueError("command must not contain '\\r'")
        if "\n" in cmd:
            raise ValueError("command must not contain '\\n'")
        if ";" in cmd:
            raise ValueError("command must not contain ';'")

        # seems similar to add "\r" or "\r\n"
        # not documented ?
        if not cmd.endswith(";"):
            cmd += ";"

        with self.socket_lock:
            cmd_b = cmd.encode()
            self.sock.write(cmd_b)
            ans_b = self.sock.raw_read()
            ans = ans_b.decode()

            # Depending on commands, ans can finnish by "\r\n:" or ":" or "?"
            if ans[0] == "?":
                raise RuntimeError(f"Invalid command ({cmd})")
            ans = ans.strip(": \r\n")
            if len(ans) == 0:
                return None
            return ans

    def raw_write_read(self, cmd):
        return self._galil_query(cmd)

    def raw_write(self, cmd):
        return self._galil_query(cmd)

    def raw_read(self):
        return self.sock.read()

    def raw_readline(self):
        return self.sock.readline(eol=b"\r\n:")

    """
    Controller configuration
    Should not be easily accessible to end-users.
    """

    def _configure_controller(self, burn=False):
        """
        Expert function to set configuration parameters in galil controller.
        """
        print(
            f"\n --------- GALIL  Controller '{self.name}' Configuration ---------------"
        )

    def _configure_axis(self, axis, burn=False):
        """
        Expert function to set configuration parameters for <axis> in galil controller.
        """

        print(f"\n --------- GALIL  axis '{axis.name}' Configuration ---------------")

        # BR Brush Axis
        """
        Value    Description
        -1    Configured for external drive
         0    Configured for Brushless servo
         1    Configured for Brush-type servo
        """


#        # Vector Acceleration / Vector Deceleration  / Vector Slewrate
#        self.vect_acceleration = self.config.get("vect_acceleration", int, 262144)  # VA
#        self.vect_deceleration = self.config.get("vect_deceleration", int, 262144)  # VD
#        self.vect_slewrate = self.config.get("vect_slewrate", int, 8192)  # VS
#        self._galil_query("VA %d" % self.vect_acceleration)
#        self._galil_query("VD %d" % self.vect_deceleration)
#        self._galil_query("VS %d" % self.vect_slewrate)
#
#        # Vector Time constant (motion smoothing)
#        if self.model_id not in ["DMC4010", "DMC30010"]:
#            if self.model_serie in ["MODEL_2000"]:
#                self.vect_smoothing = self.config.get("vect_smoothing", int, 1)  # VT
#                self._galil_query("VT 1.0")


#        # motor type / ratio / encoder
#        motor_type = MOTOR_TYPE_DICT[
#            axis.config.get("motor_type", str, "SERVO")
#        ]  # MT : motor type  SERVO = 1 INV_SERVO = -1 ...
#
#        # axis_ratio = axis.config.get("axis_ratio", float, 1.0)  # GR : gearing ratio
#        axis_encoder_type = ENCODER_TYPE_DICT[
#            axis.config.get("encoder_type", str, "QUADRA")
#        ]  # CE : config encoder (not for BISS => 0 => QUADRA)
#        self._galil_query("MT%s=%d" % (axis.channel, motor_type))
#        # self._galil_query("GR%s=%d" % (axis.channel, axis_ratio))
#        self._galil_query("CE%s=%d" % (axis.channel, axis_encoder_type))
#
#        # PID
#        axis_kp = axis.config.get("kp", float, default=1.0)
#        axis_ki = axis.config.get("ki", float, default=6.0)
#        axis_kd = axis.config.get("kd", float, default=7.0)
#        self._galil_query("KP%s=%f" % (axis.channel, axis_kp))
#        self._galil_query("KI%s=%f" % (axis.channel, axis_ki))
#        self._galil_query("KD%s=%f" % (axis.channel, axis_kd))
#
#        # Integrator Limit / Independent Time constant (smoothing)
#        axis_integrator_limit = axis.config.get(
#            "integrator_limit", float, default=9.998
#        )  # IL
#        axis_smoothing = axis.config.get("smoothing", float, default=1.0)  # IT
#        self._galil_query("IL%s=%f" % (axis.channel, axis_integrator_limit))
#        self._galil_query("IT%s=%f" % (axis.channel, axis_smoothing))
#
#        # ACceleration / DeCeleration / SPeed
#        axis_acceleration = axis.config.get("acceleration", float, default=10000)  # AC
#        axis_deceleration = axis.config.get("deceleration", float, default=10000)  # DC
#        axis_slewrate = axis.config.get("slew_rate", float, default=1000)  # SP
#        self._galil_query("AC%s=%d" % (axis.channel, axis_acceleration))
#        self._galil_query("DC%s=%d" % (axis.channel, axis_deceleration))
#        self._galil_query("SP%s=%d" % (axis.channel, axis_slewrate))
#
#        # OE 0: DISABLED       : Disabled
#        #    1: POS_AMP_AB     : Motor shut off by position error, amplifier error or abort input
#        #    2: LSW            : Motor shut off by hardware limit switch
#        #    3: POS_AMP_AB_LSW : Motor shut off by position error, amplifier error, abort input or by hardware limit switch
#        # TODO  # axis_onoff = axis.config.get("off_on_error", str, default=DISABLED)
#        # TODO  # self._galil_query("OE%s=%d" % (axis.channel, axis_onoff))
#
#        #  ERror limit    / command OFfset   /   Torque Limit
#        axis_error_limit = axis.config.get(
#            "error_limit", int, default=16384
#        )  # ER : max acceptable error
#        axis_cmd_offset = axis.config.get("cmd_offset", float, default=0.0)  # ?
#        axis_torque_limit = axis.config.get(
#            "torque_limit", float, default=3.0
#        )  # TL : max continous torque
#        self._galil_query("ER%s=%d" % (axis.channel, axis_error_limit))
#        self._galil_query("OF%s=%f" % (axis.channel, axis_cmd_offset))
#        self._galil_query("TL%s=%f" % (axis.channel, axis_torque_limit))
#
#    #        # start motor (power on)
#    #        self._galil_query("SH%s" % axis.channel)
#
#    #        # set on/off error to ENABLED
#    #        self._galil_query("OE%s=1" % axis.channel)
#
#
#
#
#    def _configure_polarities(
#        self, limit_switch=LOW_LEVEL, home_switch=LOW_LEVEL, latch_polarity=LOW_LEVEL
#    ):
#        """
#        Configure switches and latch polarity (CN command).
#
#        CN applies to DMC4000,  DMC4200,  DMC4103,  DMC2103,  DMC1806, DMC1802,
#                      DMC30010, DMC50000, DMC52000, EDD37010, DMC2105
#        CN n0, n1, n2, n3, n4   (n4 not for 2xxx ?)
#
#        old MLL deposition machine
#          mlayer/galil/1/limitswitch:               HIGH_LEVEL
#          mlayer/galil/1/homeswitch:                HIGH_LEVEL
#          mlayer/galil/1/latchpolarity:             LOW_LEVEL
#
#        n0
#         limit_switch    = 1 # Limit switches active high.
#         limit_switch    = -1 # Limit switches active low (default).
#
#        n1  See HM and FE commands.
#         home_sw_mot_fw =  1 # home switch config. to drive motor in forward direction when input is high.
#         home_sw_mot_fw = -1 # home switch config. to drive motor in reverse direction when input is high (default).
#
#        n2
#         latch_input_trig =   1 # Latch input triggers on rising edge.
#         latch_input_trig =  -1 # Latch input triggers on falling edge (default).
#        """
#        log_debug(
#            self, f"polarity config: {limit_switch}, {home_switch}, {latch_polarity}"
#        )
#
#        # self._galil_query("CN %d,%d,%d" % (limit_switch, home_switch, latch_polarity))
#        pass
