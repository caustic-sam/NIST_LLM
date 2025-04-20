# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

"""
Motion Control Systems, Inc. (MCS) LA2000 Linear Servo Amplifier class.

The LA2000 is a four quadrant velocity servo amplifier intended to control
three phase permanent magnet synchronous motors (commonly called brushless
DC motors).
It is used in the control of the PIC heatload chopper.
The command interface is or RS232 (9600 baud, 8 data bits, 1 stop bit,
no parity) or Ethernet.
Commands are ASCII strings, terminated by a carriage-return (ASCII 0DH).
Commands can be chained as one string by separating them with semicolons.
The maximum command string length is 511 characters. A carriage-return at the
end of the string will initiate the action of decoding the entire command
string.
If the command is accepted, the system will respond with a line feed character.
In case of transmission or syntax errors, the system will respond with an
error code followed by a "?" and a line feed character (ASCII 0AH).

Example yml file:

.. code-block:: yaml

    name: la2000
    class: la2000
    #serial line name /dev/tty** as in /users/blissadm/local/config/ser2net.conf
    serial:
      url: "rfc2217://lid231:280**"
    or
    telnet:
      url: 160.103.x.y:23
"""
from enum import Enum, unique
from telnetlib import Telnet
from gevent import Timeout, sleep
from bliss.comm.util import get_comm, get_comm_type, SERIAL, TCP


@unique
class MCS_LA2000ErrorEnum(Enum):
    """defines the errors"""

    UNKNOWN = 0, "Unrecognized command"
    INVALID = 2, "Invalid request"
    SETUP = 3, "Unable to change setup"
    COMMAND = 6, "Command string too long"


MCS_LA2000_STATUS = {
    0: "System Enabled",
    1: "System Ready",
    3: "Speed = 0",
    4: "Motor at Speed",
    5: "Direction Status CCW",
    7: "System Fault",
}

MCS_LA2000_FAULT_XX = {
    1: "Amplifier Over-Temperature",
    2: "Logic Power Fault",
    3: "Auxiliary #1 Fault",
    4: "Transformer Over-Temperature",
}
MCS_LA2000_FAULT_YY = {
    0: "Bearing Air Pressure Fault",
    1: "Clamp Air Pressure Fault",
    2: "Main Air Fault",
    3: "Over Speed Fault",
}


class MCS_LA2000:
    """Main class"""

    def __init__(self, config):
        self.comm = None
        eol = b"\n\n"
        try:
            self.type = get_comm_type(config)
        except ValueError:
            self.type = "telnet"
            self.telnet_is_open = None
        if self.type == TCP:
            self.comm = get_comm(config, eol=eol)
        if self.type == SERIAL:
            self.comm = get_comm(config, baudrate=9600, eol=eol)
        if self.type == "telnet":
            host, port = config.get("telnet")["url"].split(":")
            if not port:
                port = 23  # default telnet port
            self.comm = Telnet(host=host, port=port, timeout=2)
            self.telnet_is_open = True

    def telnet_open(self):
        """Open the communication with the controller, if telnet."""
        if self.type == "telnet" and not self.telnet_is_open:
            self.comm.open(self.comm.host)
            self.telnet_is_open = True

    def telnet_close(self):
        """Close the communication channel."""
        if self.type == "telnet" and self.telnet_is_open:
            self.comm.close()
            self.telnet_is_open = False

    def __info__(self):
        """Return the exhaustive status of the object.

        Returns:
            (str): The status as string
        """
        info_str = f"MCS: {self.idinfo}\n"
        info_str += f"State:\n{self.state}\n"
        info_str += f"{self.configuration}\n"
        info_str += f"Motor is turning: {self.is_turning}\n"

        return info_str

    def _translate_error(self, err_nb):
        """Translate return error code to human readable error.

        Args:
            (int): Error code.
        Returns:
            (str): Error string
        """
        err_list = list(map(lambda c: c.value, MCS_LA2000ErrorEnum))
        for err in err_list:
            if err[0] == err_nb:
                return err[1]
        return "Unknown error"

    def _send_cmd(self, cmd, value=None):
        """Send a command to.

        Args:
            cmd (string): the bare command
            value (int): Value to set, if any
        Returns:
            (bool) or (int): True, False or the bare answer if command allows
        Raises:
            RuntimeError: Command not executed
        """
        # flush the communication buffer
        # self.comm.flush()
        # construct the command
        if value is None:
            _cmd = f"{cmd}\r".encode()
        else:
            _cmd = f"{cmd} {value}\r".encode()
        if self.type == "telnet":
            self.comm.write(_cmd)
            _asw = self.comm.read_until(b"\n\n", timeout=3).decode()
            _asw = _asw.strip()
        else:
            _asw = self.comm.write_readline(_cmd).decode()
        if "?" in _asw:
            raise RuntimeError(self._translate_error(_asw[1]))
        return _asw

    @property
    def idinfo(self):
        """Get the ID information.

        Returns:
            (str): ID information
        """
        info = self._send_cmd("ID?")
        return info.replace(";", "\n")

    @property
    def state(self):
        """Get the controller state in text format.

        Returns:
            (str): State.
        """
        # _asw = self._send_cmd("STATQ?")
        # work around as the command answer eol is not \n\n, but \n only
        self.comm.write("STATQ?\r".encode())
        sleep(0.5)
        _asw = self.comm.raw_read(1000).decode()
        if "Error" in _asw:
            raise RuntimeError(_asw)
        return _asw

    @property
    def enable(self):
        """Requests the enable status.

        Returns:
            (bool): True if enabled, False otherwise
        """
        return "ENABLED" in self._send_cmd("ENABLE?")

    @enable.setter
    def enable(self, value):
        """Enable/disable the unit. When enable, the motor accelerates to
        the requested speed. When disable, the motor coasts to zero speed,
        rather than being actively decelerated to a stop.
        Args:
            value (bool): True = enable, False = disable.
        """
        if value:
            self._send_cmd("ENABLE")
        else:
            self._send_cmd("DISABLE")

    def run(self, timeout=None):
        """Enable the controller and command the motor to accelerate to the
        requested speed.

        Args:
            timeout (float): Timeout [s] (None - wait forever, 0 - do nothing).
        Raises:
            RuntimeError: - from _send_cmd
                          - Timeout from wait_ready
        """
        self._send_cmd("RUN")
        self.wait_ready("enable", timeout=timeout)

    def stop(self, timeout=None):
        """Command the motor to decelerate to zero speed and the controller to
        disable when the motor speed is less than the stop speed.

        Args:
            timeout (float): Timeout [s] (None - wait forever, 0 - do nothing).
        Raises:
            RuntimeError: - Timeout, when applicable
        """
        self._send_cmd("STOP")
        self.wait_ready("disable", timeout=timeout)

    def abort(self):
        """Disable the controller. The motor coasts to zero speed rather than
        being actively decelerated to a stop.
        """
        self._send_cmd("DISABLE")

    @property
    def is_turning(self):
        """Requests if the motor has stopped or is still turning.

        Returns:
            (bool): False if motor has stopped, True if still turning.
        """
        return self._send_cmd("ZERO?") == "NO"

    @property
    def configuration(self):
        """Read the configuration parameters.

        Retuns:
            (str): The configuration parameters.
        """
        return self._send_cmd("CONFIG?")

    @property
    def ready(self):
        """Requests the ready status (whether the drive can be enabled).

        Returns:
            (bool): True if ready, False otherwise
        """

        return self._send_cmd("READY?") == "YES"

    @property
    def _state_hexa(self):
        """Get the controller state hexadecimal values.

        Returns:
            (list): State as list of two converted to integer hexadecimal values.
        """
        return [int(i, 16) for i in self._send_cmd("STATA?").split()]

    def wait_ready(self, action, timeout=None):
        """Wait for the system to be enabled/disabled.

        Args:
            action (str): enable, disable
            timeout (float): Timeout [s] (None - wait forever, 0 - do nothing).
        Raises:
            RuntimeError: - Timeout, when applicable.
        """
        if timeout != 0:
            with Timeout(timeout, RuntimeError(f"Timeout waiting to {action}")):
                state = self._state_hexa[0]
                if action == "enable":
                    # bit 0 set = system enabled
                    while not state & (1 << 0):
                        sleep(1)
                        state = self._state_hexa[0]
                elif action == "disable":
                    # bit 0 set = system enabled, bit 4 set = motor at speed
                    while state & (1 << 0) and not state & (1 << 4):
                        sleep(1)
                        state = self._state_hexa[0]

    def get_sysfault(self):
        """Read the system fault status. Report the error, if any.

        Returns:
            (bool) or (str): False if no error, Error string if error
        """
        if "OK" in self._send_cmd("FAULT?"):
            return False
        error_str = ""
        fault = self._send_cmd("FLTA?").split()

        for key, val in MCS_LA2000_FAULT_XX.items():
            if int(fault[0], 16) & (1 << key):
                error_str += f"{val}; "

        for key, val in MCS_LA2000_FAULT_YY.items():
            if int(fault[1], 16) & (1 << key):
                error_str += f"{val}; "

        return error_str
