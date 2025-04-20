import gevent
import time
from bliss.common.logtools import log_debug
from bliss.common.hook import MotionHook
from bliss.controllers.motors.icepap import Icepap
from bliss.controllers.motors.icepap.linked import LinkedAxis


class IceAncillaryError(Exception):
    pass


class IceAncillaryHook(MotionHook):
    def __init__(self, name, config):
        self.name = name
        self.config = config
        self.wago = config["wago"]
        self.cmd_key = config["command_channel"]
        self.sta_key = config["status_channel"]
        self.ctl_key = config.get("control_channel", None)
        self.cmd_value = dict()
        self.cmd_value["engage"] = 1 if config.get("command_engage", 0) else 0
        self.cmd_value["release"] = 1 - self.cmd_value["engage"]
        self.sta_value = dict()
        self.sta_value["engage"] = 1 if config.get("status_engaged", 0) else 0
        self.sta_value["release"] = 1 - self.sta_value["engage"]
        self.status_timeout = config.get("status_timeout", 1.0)
        self.error = None
        self.axis = None
        self.is_linked_axis = False
        self._scan_flag = False
        super(IceAncillaryHook, self).__init__()

    def _add_axis(self, axis):
        # check only one axes
        if self.axis:
            raise ValueError(
                "Cannot attach IceAncillaryHook {self.name} to {axis.name}. "
                "It is already attached to {self.axis.name}"
            )

        # check axis is icepap or linkedaxis
        if isinstance(axis, LinkedAxis):
            self.is_linked_axis = True
        elif not isinstance(axis.controller, Icepap):
            raise ValueError(
                "IceAncillaryHook only works with icepap axis/linked axis. "
                "Not valid for {axis.name}"
            )
        self.axis = axis
        super(IceAncillaryHook, self)._add_axis(axis)

    def _set(self, phase):
        self.error = None
        self.wago.set(self.cmd_key, self.cmd_value[phase])
        if self.ctl_key is not None:
            state = self.wago.get(self.ctl_key)
            if state != self.cmd_value[phase]:
                self.error = (
                    f"{phase} command FAILED : Cannot send command, motor(s) are off."
                )
                return False
        start_time = time.time()
        while (time.time() - start_time) < self.status_timeout:
            gevent.sleep(0.1)
            state = self.wago.get(self.sta_key)
            if state == self.sta_value[phase]:
                return True
        self.error = f"{phase} command FAILED : Timeout waiting for air pressure"
        return False

    def release(self):
        if not self._set("release"):
            raise IceAncillaryError(f"on [{self.name}] {self.error}")
        gevent.sleep(0.2)

    def engage(self):
        if not self._set("engage"):
            raise IceAncillaryError(f"on [{self.name}] {self.error}")

    @property
    def status(self):
        state = self.wago.get(self.sta_key)
        if state == self.sta_value["release"]:
            return "RELEASED"
        else:
            return "ENGAGED"

    def __info__(self):
        info = f"Current status : {self.status}"
        if self.error is not None:
            info += f"\nLast {self.error}"
        info += "\nWago status    :"
        for (field, key, value) in self._read_wago():
            info += f"\n{field} [{key}] = {value}"
        return info

    def _read_wago(self):
        read = list()
        cmd_val = self.wago.get(self.cmd_key)
        read.append(("command", self.cmd_key, cmd_val))
        sta_val = self.wago.get(self.sta_key)
        read.append(("status", self.sta_key, sta_val))
        if self.ctl_key is not None:
            ctl_val = self.wago.get(self.ctl_key)
            read.append(("control", self.ctl_key, ctl_val))
        return read

    def pre_scan(self, axes_list):
        self._scan_flag = True
        log_debug(self, f"relase ancillary on scan axis {self.axis.name}")
        self._do_pre_action()

    def pre_move(self, motion_list):
        if self._scan_flag is False:
            log_debug(self, f"relase ancillary on move axis {self.axis.name}")
            self._do_pre_action()

    def _do_pre_action(self):
        if self._set("release"):
            log_debug(self, f"reset closed loop on axis {self.axis.name}")
            self._reset_closed_loop()
        else:
            log_debug(
                self, f"engage ancillary on axis {self.axis.name} after release failed"
            )
            self._set("engage")
            raise IceAncillaryError(
                f"FAILED to release ancillary on axis {self.axis.name}"
            )

    def post_scan(self, axes_list):
        self._scan_flag = False
        log_debug(self, f"engage ancillary on scan axis {self.axis.name}")
        self._do_post_action()

    def post_move(self, motion_list):
        if self._scan_flag is False:
            log_debug(self, f"engage ancillary on move axis {self.axis.name}")
            self._do_post_action()

    def _do_post_action(self):
        if self._set("engage"):
            log_debug(self, f"open closed loop on axis {self.axis.name}")
            self._open_closed_loop()
        else:
            raise IceAncillaryError(
                f"FAILED to engage ancillary on axis {self.axis.name}"
            )

    def _reset_closed_loop(self):
        if self.is_linked_axis:
            self.axis.reset(use_hook=False)
        else:
            self.axis.reset_closed_loop()

    def _open_closed_loop(self):
        if self.is_linked_axis:
            self.axis.open_closed_loop()
        else:
            self.axis.activate_closed_loop(False)
