from bliss.comm.util import get_comm
from bliss.common.axis.state import AxisState
from bliss.common.axis.axis import Axis as BaseAxis
from bliss.controllers.motor import Controller
from bliss.common.logtools import log_error, log_debug, log_warning

"""
Bliss controller for Zaber.

-
 controller:
     plugin: emotion
     class: Zaber
     name: zaber
     serial:
         url: "rfc2217://lid269:28410"
         baudrate: 115200
     axes:
      - name: zamot
        channel: 1
        velocity: 10 
        acceleration: 5
        steps_per_unit: 1000000 
        high_limit: 150
        low_limit: 0
        velocity_high_limit: 819200
        velocity_low_limit: 1
"""


class Axis(BaseAxis):
    @property
    def limit_max(self):
        return self.controller.raw_write_read("get limit.max", self)

    @limit_max.setter
    def limit_max(self, pos):
        self.controller.raw_write_read(f"set limit.max {pos}", self)

    @property
    def limit_min(self):
        return self.controller.raw_write_read("get limit.min", self)

    @limit_min.setter
    def limit_min(self, pos):
        self.controller.raw_write_read(f"set limit.min {pos}", self)


FAULTS = {
    "FF": "Fault - Critical System Error",
    "FN": "Fault - Peripheral Not Supported",
    "FZ": "Fault - Peripheral Inactive",
    "FH": "Fault - Hardware Emergency Stop Driver Disabled",
    "FV": "Fault - Overvoltage or Undervoltage Driver Disabled",
    "FO": "Fault - Driver Disabled",
    "FC": "Fault - Current Inrush Error",
    "FM": "Fault - Motor Temperature Error",
    "FD": "Fault - Driver Temperature/Current Error",
    "FQ": "Fault - Encoder Error",
    "FS": "Fault - Stalled and Stopped",
    "FB": "Fault - Stream Bounds Error",
    "FE": "Fault - Limit Error",
    "FT": "Fault - Excessive Twist",
}

WARNINGS = {
    "WL": "Warning - Unexpected Limit Trigger",
    "WV": "Warning - Voltage Out of Range",
    "WT": "Warning - Temperature High",
    "WS": "Warning - Stalled with Recovery",
    "WM": "Warning - Displaced When Stationary",
    "WP": "Warning - Invalid Calibration Type",
    "WR": "Warning - No Reference Position",
    "WH": "Warning - Device Not Homed",
    "NC": "Note - Manual Control",
    "NI": "Note - Movement Interrupted",
    "ND": "Note - Stream Discontinuity",
    "NR": "Note - Value Rounded",
}


class Zaber(Controller):
    def __init__(self, *args, **kwargs):
        Controller.__init__(self, *args, **kwargs)

    def __info__(self):
        _info = (
            f"Zaber Motion Controller {self.address} (controller name: {self.name})\n"
        )
        _info += "    axes:\n"
        for item in self.axes:
            axis = self.axes[item]
            if not hasattr(axis, "status"):
                self.initialize_axis(axis)
            _info += f"    - {axis.name}: {axis.status} - {axis.wr} \n"
        return _info

    def initialize(self):

        self.comm = get_comm(self.config.config_dict)
        self.address = self.config.get("id", str, "1")

    def finalize(self):
        self.comm.close()

    def initialize_hardware(self):
        """
        This method should contain all commands needed to initialize the controller hardware.
        i.e: reset, power on....
        This initialization will be called once (by the first client).
        """
        self.raw_write_read("system restore")
        # self.raw_write_read('system errors clear') #always ReJected

    def initialize_axis(self, axis):
        axis.channel = axis.config.get("channel", str)
        self.state(axis)

    def initialize_hardware_axis(self, axis):
        """
        This method should contain all commands needed to initialize the
        hardware for this axis.
        i.e: power, closed loop configuration...
        This initialization will call only once (by the first client).
        """
        pass

    def steps_position_precision(self, axis):
        """
        Return a float value representing the precision of the position in steps

        * 1e-6 is the default value: it means the motor can deal with floating point
          steps up to 6 digits
        * 1 means the motor controller can only deal with an integer number of steps
        """
        return 1e-6

    def _add_axis(self, axis):
        """
        This method is called when a new axis is attached to
        this controller.
        This is called only once per axis.
        """
        pass

    def prepare_all(self, *motion_list):
        raise NotImplementedError

    def prepare_move(self, motion):
        return

    def start_one(self, motion):
        self.raw_write_read(f"move abs {int(motion.target_pos)}", motion.axis)

    def start_all(self, *motion_list):
        for motion in motion_list:
            self.start_one(motion)

    def stop(self, axis):
        self.raw_write_read("stop", axis)

    def stop_all(self, *motion_list):
        for motion in motion_list:
            self.stop(motion.axis)

    def state(self, axis):
        self.raw_write_read("", axis)
        return AxisState(axis.status)

    def get_axis_info(self, axis):
        _info_str = f"Zaber AXIS {axis.name}: \n"
        _info_str += f"- channel: {axis.channel}\n"
        _info_str += f"- hardware limits (in steps): Min: {axis.limit_min} Max: {axis.limit_max}\n"
        return _info_str

    def get_id(self, axis):
        return f"Zaber Controller (self.address) axis {axis.channel}"

    def home_search(self, axis, switch):
        self.raw_write_read("home", axis)

    def home_state(self, axis):
        return self.state(axis)

    def limit_search(self, axis, velocity):
        """
        Moves the axis at the velocity specified until limit.min or limit.max is reached, a limit sensor is triggered,
        or the axis is pre-empted by another movement command such as stop.
        NDLR: how does it decide upon direction ???? always >0 ???
        """
        self.raw_write_read(f"move vel {abs(velocity*axis.steps_per_unit)}")

    def read_position(self, axis):
        return float(self.raw_write_read("get pos", axis))

    def set_position(self, axis, new_position):
        """Set the position of <axis> in controller to <new_position>.
        This method is called by `position` property of <axis>.
        """
        raise NotImplementedError

    def read_velocity(self, axis):
        return float(self.raw_write_read("get maxspeed", axis))

    def set_velocity(self, axis, new_velocity):
        new_velocity = int(new_velocity)
        try:
            self.raw_write_read(f"set maxspeed {new_velocity}", axis)
        except Exception as e:
            log_error(
                self,
                "{2}: Cannot set velocity {0} on {1} axis".format(
                    new_velocity, axis.name, e
                ),
            )
            # raise e

    def read_acceleration(self, axis):
        return float(self.raw_write_read("get accel", axis))

    def set_acceleration(self, axis, new_acc):
        new_acc = int(new_acc)
        self.raw_write_read(f"set accel {new_acc}", axis)

    def set_event_positions(self, axis_or_encoder, positions):
        """
        This method is use to load into the controller
        a list of positions for event/trigger.
        The controller should generate an event
        (mainly electrical pulses) when the axis or
        the encoder pass through one of this position.
        """
        raise NotImplementedError

    def get_event_positions(self, axis_or_encoder):
        """
        @see set_event_position
        """
        raise NotImplementedError

    def raw_write(self, com):
        raise NotImplementedError

    def raw_write_read(self, com, axis=None):
        """
        Zaber devices listen for commands sent to them over a serial port and then immediately respond with a reply.
        Commands always begin with a / and end with a newline.
        Some commands take parameters, which are separated by spaces.
        Two example commands are:
           /1 warnings↵
           /1 system reset↵

        Replies begin with a @, end with a newline, and have several space-delimited parameters in between.
        For example, the most common reply is:
           @01 0 OK IDLE -- 0

        Devices can also send two other types of messages:
        - alerts, which start with !; and
        - info, which start with #.

        @     A reply.
        01    The ID of the device sending the reply
        0     The reply scope: 0 for the device or all axes, 1 onwards for an individual axis
        OK/RJ The command succeeded/Rejected
        IDLE  The device isn't moving (or BUSY if it is moving).
        --    No faults or warnings are active.
        0     The return value, typically 0
        """

        id = axis.channel if axis is not None else "0"

        command = f"/{self.address} {id} {com}\n"

        reply = self.comm.write_readline(command.encode(), eol="\n")
        reply = reply.decode()

        device, scope, result, status, warning, data = reply.split()

        if device != f"@0{self.address}" or scope != id:
            log_error(self, "Wrong answer from device: {0}".format(reply))
            raise RuntimeError

        if axis:
            if status == "IDLE":
                axis.status = "READY"
            else:
                axis.status = "MOVING"

            if warning != "--":
                if warning in WARNINGS:
                    warning = WARNINGS[warning]
                if warning in FAULTS:
                    warning = FAULTS[warning]
                    axis.status = "FAULT"
                log_warning(self, "Zaber controller message: {0}".format(warning))

            axis.wr = warning

        log_debug(self, "command: {0}".format(command))
        log_debug(self, "reply: {0}".format(reply))

        if result != "OK":
            log_error(self, "Command rejected: {0}".format(reply))
            raise RuntimeError

        return data

    # methods specific to Zaber

    def reset(self):
        """
        Resets the device to the power-up state.
        Once the device begins performing the reset, it will be unresponsive for a few seconds as it powers up.
        """
        self.raw_write_read("system reset")
