import math
import numpy as np
from bliss.common import event
from bliss.controllers.motor import CalcController


class calc_motor_mockup(CalcController):
    """
    Calculation Bliss controller

    real_mot
        real motor axis alias

    calc_mot
        calculated axis alias

    s_param
        specific_parameter to use for the calculation axis (e.g. gain factor)
        As it can change, we want to treat it as settings parameter as well.
        The parameter can have an initial value in the config file.

    Example of the config file:

    .. code-block:: yaml

        controller:
            class: calc_motor_mockup
            axes:
                -
                    name: $real_motor_name
                    tags: real real_mot
                -
                    name: calc_mot
                    tags: calc_mot
                    s_param: 2 #this is optional
    """

    def __init__(self, *args, **kwargs):
        CalcController.__init__(self, *args, **kwargs)
        self._axis = None
        self.axis_settings.add("s_param", float)

    def initialize_axis(self, axis):
        self._axis = axis
        CalcController.initialize_axis(self, axis)
        event.connect(axis, "s_param", self._calc_from_real)
        axis._unit = "keV"

    def close(self):
        if self._axis is not None:
            event.disconnect(self._axis, "s_param", self._calc_from_real)
            self._axis = None
        super(calc_motor_mockup, self).close()

    """
    #Example to use s_param as property instead of settings.
    #s_param is set in the YAML config file.
    @property
    def s_param(self):
        return self.__s_param

    @s_param.setter
    def s_param(self, s_param):
        self.__s_param = s_param
        self._calc_from_real()
    """

    def calc_from_real(self, positions_dict):
        calc_mot_axis = self._tagged["calc_mot"][0]
        calc_mot_axis._unit == "keV"
        s_param = calc_mot_axis.settings.get("s_param")
        # this formula is just an example
        calc_pos = s_param * positions_dict["real_mot"]

        return {"calc_mot": calc_pos}

    def calc_to_real(self, positions_dict):
        calc_mot_axis = self._tagged["calc_mot"][0]
        s_param = calc_mot_axis.settings.get("s_param")
        # this formula is just an example
        real_pos = positions_dict["calc_mot"] / s_param

        return {"real_mot": real_pos}


# issue 3822
class calc_motor_mockup2(CalcController):
    def calc_from_real(self, positions_dict):
        r1 = positions_dict["r1"]
        r2 = positions_dict["r2"]
        calc_pos = (r1 + r2) / 2
        return {"calc_mot": calc_pos}

    def calc_to_real(self, positions_dict):
        return {"r1": positions_dict["calc_mot"], "r2": positions_dict["calc_mot"]}


# issue 1909
class llangle_mockup(CalcController):
    def initialize(self):
        CalcController.initialize(self)
        self.bend_zero = self.config.get("bend_zero", float)
        self.bend_y = self.config.get("bend_y", float)
        self.ty_zero = self.config.get("ty_zero", float)

    def calc_from_real(self, positions_dict):
        # Angle due to pusher not being a rotation
        # Effect of bending
        # Effect of translation
        bend = positions_dict["bend"]
        rz = positions_dict["rz"]
        ty = positions_dict["ty"]

        truebend = bend - self.bend_zero  # pass through
        absty = ty - self.ty_zero
        bend_offset = np.degrees(truebend * absty / self.bend_y)
        # only for bent crystal and mono in beam
        valid = (truebend > 0) & (absty < 75.0)
        angle = np.where(valid, rz + bend_offset, rz)
        calc_dict = {"angle": angle, "truebend": truebend, "absty": absty}  # computed
        return calc_dict

    def calc_to_real(self, positions_dict):
        #
        angle = positions_dict["angle"]
        # Effect of bending
        truebend = positions_dict["truebend"]
        bend = truebend + self.bend_zero
        # Effect of translation
        absty = positions_dict["absty"]  # llty1 / llty2
        ty = absty + self.ty_zero
        # Assume we go to the destination ty / bend.
        # Compute the effect for the angle only
        bend_offset = np.degrees(truebend * absty / self.bend_y)
        # only for bent crystal and mono in beam
        valid = (truebend > 0) & (absty < 75.0)
        # - versus + above:
        rz = np.where(valid, angle - bend_offset, angle)
        calc_dict = {"bend": bend, "ty": ty, "rz": rz}
        return calc_dict


class FaultyCalc(CalcController):
    def calc_from_real(self, positions_dict):
        return {"calc_mot": None}

    def calc_to_real(self, positions_dict):
        return {self._axis_tag(x): None for x in self.reals}


class CoupledMotionCalc(CalcController):
    def initialize(self):
        CalcController.initialize(self)
        self.factor = self.config.config_dict.get("factor", 1)

    def calc_from_real(self, positions_dict):
        return {"calc_mot": positions_dict["mot1"]}

    def calc_to_real(self, positions_dict):
        real_pos = positions_dict["calc_mot"] * self.factor
        return {self._axis_tag(x): real_pos for x in self.reals}


class XposToAngleCalc(CalcController):
    def calc_from_real(self, positions_dict):
        angle = np.arccos(positions_dict["px"])
        return {"angle": np.rad2deg(angle)}

    def calc_to_real(self, positions_dict):
        angle = np.deg2rad(positions_dict["angle"])
        return {"px": np.cos(angle)}


class AngleToXposCalc(CalcController):
    def _load_config(self):
        super()._load_config()
        self._radius = self._config["radius"]

    @property
    def radius(self):
        return self._radius

    @radius.setter
    def radius(self, value):
        self._radius = value
        self.sync_pseudos()  # update pseudo pos and setpos

    def calc_from_real(self, positions_dict):
        angle = np.deg2rad(positions_dict["angle"])
        return {"px": self.radius * np.cos(angle)}

    def calc_to_real(self, positions_dict):
        angle = np.arccos(positions_dict["px"] / self.radius)
        return {"angle": np.rad2deg(angle)}


class TwoTheta(CalcController):
    def calc_from_real(self, positions_dict):
        return {"twotheta": positions_dict["theta"] * 2}

    def calc_to_real(self, positions_dict):
        return {"theta": positions_dict["twotheta"] / 2}


class Tracker(CalcController):
    def calc_from_real(self, positions_dict):
        return {"track_undu": positions_dict["angle"]}

    def calc_to_real(self, positions_dict):
        return {
            "angle": positions_dict["track_undu"],
            "undu": positions_dict["track_undu"] * 0.1,
        }


class Followers(CalcController):
    def calc_from_real(self, positions_dict):
        return {"leader": positions_dict["follower_1"] - 1}

    def calc_to_real(self, positions_dict):
        return {
            "follower_1": positions_dict["leader"] + 1,
            "follower_2": positions_dict["leader"] + 2,
            "follower_3": positions_dict["leader"] + 3,
        }


class Parameteric(CalcController):
    def calc_from_real(self, positions_dict):
        return {"out": positions_dict["in1"] - positions_dict["axpar"]}

    def calc_to_real(self, positions_dict):
        real_pos = positions_dict["out"] + positions_dict["axpar"]
        return {self._axis_tag(x): real_pos for x in self.reals}


class DynamicReals(CalcController):
    def initialize(self):
        CalcController.initialize(self)
        self.wkmode = 0  # 0 => subset of reals and 1 => all reals

    def calc_from_real(self, positions_dict):
        return {"mumu": positions_dict["r1"]}

    def calc_to_real(self, positions_dict):
        p = positions_dict["mumu"]
        reals_dict = {"r1": p, "r2": p}
        if self.wkmode:
            reals_dict.update({"r3": p})
        return reals_dict


class LogCalc(CalcController):
    def calc_from_real(self, positions_dict):
        return {"out": math.exp(positions_dict["in"])}

    def calc_to_real(self, positions_dict):
        pos = math.log(
            positions_dict["out"]
        )  # USING math to raise an error on log( x <= 0)
        return {"in": pos}
