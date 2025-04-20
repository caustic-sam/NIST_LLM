import os.path
import csv
from bliss import global_map
from bliss.comm.util import get_comm, SERIAL
from bliss.common.logtools import log_debug


class Rvc300:
    """
    RVC 300 Pfieffer controller
    Ascii protocol:
    9600 bauds, 8 bits, 1 stop bit, no parity
    70ms between writing and reading response
    data transmision terminated by <cr><lf>
    """

    def __init__(self, config):
        self._config = config
        self._comm = None
        file_path = self._config["pressure_over_temp_table_file"]
        if os.path.isfile(file_path):
            self._lists_table = self._create_lists_from_file(file_path)

    def _init_com(self):
        default_options = {"baudrate": 9600}
        self._comm = get_comm(self._config, ctype=SERIAL, **default_options)
        self._eol = "\r\n"
        global_map.register(self, children_list=[self._comm])

    @property
    def comm(self):
        if self._comm is None:
            self._init_com()
        return self._comm

    def __info__(self, show_module_info=True):
        info_list = []
        info_list.append(f"\nGathering information from {self._config['name']}:")
        info_list.append("\tVersion:          %s" % self.send_cmd("VER?"))
        info_list.append("\tSetpoint:         %s" % self.send_cmd("PRS?"))
        info_list.append("\tOperating mode:   %s" % self.send_cmd("MOD?"))
        info_list.append("\tActual value:     %s" % self.send_cmd("PRI?"))
        info_list.append("\tPressure Sensor:  %s" % self.send_cmd("RTP?"))
        pidc = self.send_cmd("RAS?")
        info_list.append("\tPidc controller:  %s" % pidc)
        if pidc == "0":
            info_list.append("\tKp:               %s" % self.send_cmd("RSP?"))
            info_list.append("\tKi                %s" % self.send_cmd("RSI?"))
            info_list.append("\tKd:               %s" % self.send_cmd("RSD?"))

        return "\n".join(info_list)

    def send_cmd(self, cmd, arg=None):
        """Send a command to the hardware and read the answer"""
        msg = f"{cmd}"
        if arg is not None:
            msg += f"={arg}"
        msg += "\r\n"
        log_debug(self, f"send_cmd {msg}")
        self.comm.write(msg.encode())
        ans = self.comm.readline(eol="\r").decode()
        log_debug(self, f"receive {ans}")
        if "ERROR-INPUT" in ans:
            raise ValueError(f"Wrong answer: {ans}")
        elif "?" in cmd:
            reply = ans.split("=")
            return reply[1].strip()

    @property
    def kp(self):
        """Get the Kp value"""
        log_debug(self, "Rvc300:kp")
        ans = self._get_kp()
        return ans

    @kp.setter
    def kp(self, value):
        """Set the Kp value"""
        log_debug(self, "Rvc300:kp.setter %s" % value)
        self._set_kp(value)

    def _get_kp(self):
        return float(self.send_cmd("RSP?"))

    def _set_kp(self, kp):
        if kp < 0 or kp > 100:
            raise ValueError("Value out of range (0 to 100)")

        arg_str = "%05.1f" % kp
        cmd = "RSP=" + arg_str
        self.send_cmd(cmd)

    @property
    def ki(self):
        """Get the Ki value"""
        log_debug(self, "Rvc300:ki")
        ans = self._get_ki()
        return ans

    @ki.setter
    def ki(self, value):
        """Set the Ki value"""
        log_debug(self, "Rvc300:ki.setter %s" % value)
        self._set_ki(value)

    def _get_ki(self):
        return float(self.send_cmd("RSI?"))

    def _set_ki(self, ki):
        if ki < 0 or ki > 3600:
            raise ValueError("Value out of range (0 to 3600)")

        arg_str = "%06.1f" % ki
        cmd = "RSI=" + arg_str
        self.send_cmd(cmd)

    @property
    def kd(self):
        """Get the Kd value"""
        log_debug(self, "Rvc300:kd")
        ans = self._get_kd()
        return ans

    @kd.setter
    def kd(self, value):
        """Set the Kd value"""
        log_debug(self, "Rvc300:kd.setter %s" % value)
        self._set_kd(value)

    def _get_kd(self):
        return float(self.send_cmd("RSD?"))

    def _set_kd(self, kd):
        if kd < 0 or kd > 3600:
            raise ValueError("Value out of range (0 to 3600)")

        arg_str = "%06.1f" % kd
        cmd = "RSD=" + arg_str
        self.send_cmd(cmd)

    @property
    def set_pressure(self):
        """Get the pressure value"""
        log_debug(self, "Rvc300:set_pressure")
        ans = self._get_setpoint_pressure()
        return ans

    @set_pressure.setter
    def set_pressure(self, value):
        """Set the position value"""
        log_debug(self, "Rvc300:set_pressure.setter %s" % value)
        self._set_setpoint_pressure(value)

    @property
    def current_pressure(self):
        """Get the pressure value"""
        log_debug(self, "Rvc300:current_pressure")
        ans = self._get_current_pressure()
        return ans

    def _get_setpoint_pressure(self):
        ans = self.send_cmd("PRS?")
        value_str = ans.replace("mbar", "")
        value = float(value_str)
        return value

    def _set_setpoint_pressure(self, pressure):
        arg_str = "%1.2E" % pressure
        cmd = "PRS=" + arg_str
        self.send_cmd(cmd)

    def _get_current_pressure(self):
        ans = self.send_cmd("PRI?")
        value_str = ans.replace("mbar", "")
        value = float(value_str)
        return value

    def get_liste_table(self):
        return self._lists_table

    def _create_lists_from_file(self, file_path):
        lists_table = []
        with open(file_path, "r") as f:
            reader = csv.reader(f, delimiter=" ")
            for row in reader:
                if row[0] != "#":
                    lists_table.append(row)

        return lists_table

    def _get_press_from_temp(self, temp):
        lists = self._lists_table
        for list in lists:
            if temp <= int(list[0]):
                pressure = int(list[1])
                break
        return pressure
