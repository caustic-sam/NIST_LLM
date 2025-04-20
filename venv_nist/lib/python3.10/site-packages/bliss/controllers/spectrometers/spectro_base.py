# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

import time
import numpy
import weakref

from bliss import global_map

from bliss.common import event
from bliss.common.protocols import HasMetadataForScan
from bliss.common.protocols import counter_namespace
from bliss.common.hook import MotionHook
from bliss.common.utils import autocomplete_property
from bliss.common.motor_group import Group

from bliss.controllers.motor import CalcController
from bliss.controllers.motors.mockup import Mockup
from bliss.controllers.motors.icepap import Icepap
from bliss.controllers.monochromator.monochromator import XtalManager
from bliss.controllers.spectrometers.spectro_plot import SpectroPlot

from bliss.config.plugins.generic import ConfigItemContainer
from bliss.config.settings import HashObjSetting

from bliss.shell.formatters.table import IncrementalTable


DIGITS_TOLERANCE = 8  # tolerance digits


# --------------- Note about array and matrix algebra ---------------------
#
# with a vector:
# V = | 1
#     | 0
#     | 0
# expressed as: V = numpy.array( [1, 0, 0] )
#
# with a matrix:
# P = |  0  1  0 |
#     | -1  0  0 |
#     |  0  0  1 |
# expressed as: P = numpy. array( [ [0, 1, 0], [ -1, 0, 0], [0, 0, 1] ] )
#
#
# then VV = P@V  is the matrix product:
#
# | 0 = |  0  1  0 | * | 1
# |-1   | -1  0  0 |   | 0
# | 0   |  0  0  1 |   | 0
#
# with VV = numpy.array( [0, -1, 0] )
#
# --------------------------------------------------------------------------


def rotation_matrix(axis, angle):
    """
    Return the rotation matrix associated with counterclockwise rotation about
    the given axis by a given angle (degree).
    """
    theta = numpy.deg2rad(angle)
    axis = axis / getnorm(axis)
    a = numpy.cos(theta / 2.0)
    b, c, d = -axis * numpy.sin(theta / 2.0)
    aa, bb, cc, dd = a * a, b * b, c * c, d * d
    bc, ad, ac, ab, bd, cd = b * c, a * d, a * c, a * b, b * d, c * d
    return numpy.array(
        [
            [aa + bb - cc - dd, 2 * (bc + ad), 2 * (bd - ac)],
            [2 * (bc - ad), aa + cc - bb - dd, 2 * (cd + ab)],
            [2 * (bd + ac), 2 * (cd - ab), aa + dd - bb - cc],
        ]
    )


def getnorm(vector):
    return numpy.sqrt(numpy.dot(vector, vector))


def normalize(vector):
    n = getnorm(vector)
    if n == 0:
        return numpy.array([0, 0, 0])
    else:
        return vector / n


def get_normal_plane(vector, w, h, center):
    w, h = w / 2, h / 2
    ex = normalize(vector)
    ez = numpy.array([0, 0, 1])
    ey = numpy.cross(ez, ex)
    ez = numpy.cross(ex, ey)

    p0 = ez * h - ey * w
    p1 = ez * h + ey * w
    p2 = -p0
    p3 = -p1

    return [p0 + center, p1 + center, p2 + center, p3 + center]


def get_angle(v1, v2):
    n1 = getnorm(v1)
    n2 = getnorm(v2)
    return numpy.arccos(numpy.dot(v1, v2) / (n1 * n2))


def transpose(data2d):
    return list(zip(*data2d))


def direction_to_angles(direction):
    x, y, z = direction
    r = getnorm(direction)
    if r != 0:
        pitch = -numpy.rad2deg(numpy.arcsin(z / r))
        yaw = numpy.rad2deg(numpy.arctan2(y, x))
    else:
        pitch = yaw = 0

    return pitch, yaw


def bragg_from_vect(incident, normal, miscut):
    beta = numpy.rad2deg(numpy.arccos(numpy.dot(incident, normal)))
    if normal[2] >= incident[2]:
        sign = 1
    else:
        sign = -1
    beta = beta * sign
    return 90 - beta + miscut


# ======== CALC CONTROLLERS =======================================
class BraggCalcController(CalcController):
    def __init__(self, positioner, cfg):
        self._positioner = positioner
        super().__init__(cfg)

    def calc_to_real(self, positions_dict):
        """bragg_i to real motors"""
        return self._positioner._bragg2reals(positions_dict["bragg"])

    def calc_from_real(self, positions_dict):
        """real motors to bragg_i"""
        if self.axes_are_moving():
            bragg = self._positioner.geo_bragg
        else:
            bragg = self._positioner._reals2bragg(positions_dict)
        return {"bragg": bragg}


class EnergyCalcController(CalcController):
    def __init__(self, positioner, cfg):
        self._positioner = positioner
        super().__init__(cfg)

    @autocomplete_property
    def xtals(self):
        return self._positioner.xtals

    @property
    def dspacing(self):
        return self.xtals.xtal[self.xtals.xtal_sel].d

    def energy2bragg(self, energy):
        return self.xtals.energy2bragg(energy)

    def bragg2energy(self, bragg):
        return self.xtals.bragg2energy(bragg)

    def calc_to_real(self, positions_dict):
        return {"bragg": self.energy2bragg(positions_dict["energy"])}

    def calc_from_real(self, positions_dict):
        return {"energy": self.bragg2energy(positions_dict["bragg"])}


class IdentityCalcController(CalcController):
    def __init__(self, spectro, cfg):
        self._spectro = spectro
        super().__init__(cfg)

    def calc_to_real(self, positions_dict):
        raise NotImplementedError

    def calc_from_real(self, positions_dict):
        raise NotImplementedError


class BraggIdentityCalcController(IdentityCalcController):
    def calc_to_real(self, positions_dict):
        return {
            f"bragg_{item.name}": positions_dict["bragg"]
            for item in self._spectro._active_positioners
        }

    def calc_from_real(self, positions_dict):
        if self.axes_are_moving():
            bragg = self._spectro.detector.geo_bragg
        else:
            braggi = [
                positions_dict[f"bragg_{item.name}"]
                for item in self._spectro._active_positioners
            ]
            if len(set(braggi)) == 1:
                bragg = braggi[0]
            else:
                bragg = numpy.nan
        return {"bragg": bragg}


class EnergyIdentityCalcController(IdentityCalcController):
    def _init(self):
        super()._init()
        energy_axis = self._tagged["energy"][0]
        hook = Energy2BraggCheckHook()
        hook._add_axis(energy_axis)
        energy_axis.motion_hooks.append(hook)

    def calc_to_real(self, positions_dict):
        return {
            f"energy_{item.name}": positions_dict["energy"]
            for item in self._spectro._active_positioners
        }

    def calc_from_real(self, positions_dict):
        if self.axes_are_moving():
            energy = self._spectro.detector.geo_energy
        else:
            energies = [
                positions_dict[f"energy_{item.name}"]
                for item in self._spectro._active_positioners
            ]
            if len(set(energies)) == 1:
                energy = energies[0]
            else:
                energy = numpy.nan
        return {"energy": energy}


class Energy2BraggCheckHook(MotionHook):
    """check if the bragg angle associate to the energy is reachable (i.e. not nan)"""

    def pre_move(self, motion_list):
        for motion in motion_list:
            energy = motion.target_pos
            if not numpy.isnan(energy):
                spectro_ene_calc = motion.axis.controller
                for ax in spectro_ene_calc.reals:
                    if (
                        ax.controller._positioner
                        in spectro_ene_calc._spectro._active_positioners
                    ):
                        if numpy.isnan(ax.controller.energy2bragg(energy)):
                            raise ValueError(
                                f"{ax.name} cannot reach energy {energy} keV (bragg=nan)"
                            )


# ======== SPECTROMETER ITEMS =======================================
class SpectrometerItem(ConfigItemContainer):
    def __init__(self, config):
        self._bragg_calc_controller = None
        self._energy_calc_controller = None
        self.__settings = None
        self.__cached_settings = None
        super().__init__(config)

    def close(self):
        if self._bragg_calc_controller is not None:
            self._bragg_calc_controller.close()

        if self._energy_calc_controller is not None:
            self._energy_calc_controller.close()

    def _get_subitem_default_class_name(self, cfg, parent_key):
        if parent_key == "bragg_axis":
            return "__pass__"
        elif parent_key == "energy_axis":
            return "__pass__"

        raise NotImplementedError

    def _create_subitem_from_config(
        self, name, cfg, parent_key, item_class, item_obj=None
    ):
        if item_obj is not None:
            return item_obj

        elif parent_key == "energy_axis":
            return self.energy_calc_controller._get_subitem(name)

        elif parent_key == "bragg_axis":
            return self.bragg_calc_controller._get_subitem(name)

        raise NotImplementedError

    def _load_config(self):
        pass

    def _init(self):
        self._update()

    def _update(self):
        pass

    @property
    def _settings(self):
        if self.__settings is None:
            self.__settings = HashObjSetting(f"{self.name}_settings")
        return self.__settings

    @property
    def _cached_settings(self):
        if self.__cached_settings is None:
            self.__cached_settings = self._load_settings()
        return self.__cached_settings

    def _load_settings(self):
        """Get from redis the dict of persistent parameters (redis access)"""
        cached = {}
        return cached

    def _clear_settings(self):
        self._settings.clear()
        self._load_settings()

    def _get_setting(self, key):
        """Get a persistent parameter from local cache (no redis access)"""
        return self._cached_settings[key]

    def _set_setting(self, key, value):
        """Store a persistent parameter in redis and update local cache (redis access)"""
        self._settings[key] = value
        self._cached_settings[key] = value

    def _build_bragg_calc_controller(self):
        raise NotImplementedError

    def _build_energy_calc_controller(self):
        raise NotImplementedError

    @autocomplete_property
    def bragg_calc_controller(self):
        if self._bragg_calc_controller is None:
            self._bragg_calc_controller = self._build_bragg_calc_controller()
            if self.config.get("bragg_low_limit"):
                self._bragg_calc_controller._tagged["bragg"][
                    0
                ].low_limit = self.config.get("bragg_low_limit")
            if self.config.get("bragg_high_limit"):
                self._bragg_calc_controller._tagged["bragg"][
                    0
                ].high_limit = self.config.get("bragg_high_limit")
        return self._bragg_calc_controller

    @autocomplete_property
    def energy_calc_controller(self):
        if self._energy_calc_controller is None:
            self._energy_calc_controller = self._build_energy_calc_controller()
        return self._energy_calc_controller

    @autocomplete_property
    def bragg_axis(self):
        return self.bragg_calc_controller._tagged["bragg"][0]

    @autocomplete_property
    def energy_axis(self):
        return self.energy_calc_controller._tagged["energy"][0]

    @property
    def referential_origin(self):
        return numpy.array(self._get_setting("referential_origin"))

    @referential_origin.setter
    def referential_origin(self, ref_coords):
        """Define the position [x, y, z] of the origin of this item referential in the laboratory referential"""
        if len(ref_coords) != 3:
            raise ValueError(
                f"referential origin coordinates must be a vector [x, y, z] not {ref_coords}"
            )
        if numpy.any(ref_coords != self.referential_origin):
            self._set_setting("referential_origin", ref_coords)
            self._update()


class SpectrometerPositioner(SpectrometerItem):

    VALID_TRAJ_AXIS_CTRL_CLASS = (Icepap, Mockup)

    def __init__(self, config):
        self.__real_axes = None
        super().__init__(config)
        self._align_info = ""
        self.__trajectory_axis = None

    def close(self):
        super().close()

        if self.__trajectory_axis is not None:
            event.disconnect(
                self.__trajectory_axis,
                "move_done",
                self._update_bragg_solution_on_traj_move_done,
            )

    def _load_settings(self):
        """Get from redis the dict of persistent parameters (redis access)"""
        cached = super()._load_settings()
        cached["trajectory_mode"] = self._settings.get("trajectory_mode", False)
        cached["bragg_solution"] = self._settings.get("bragg_solution", None)
        cached["referential_origin"] = self._settings.get(
            "referential_origin", [0, 0, 0]
        )
        return cached

    def __info__(self):
        self._check_alignment()
        reals2pos = {
            ax.name: self._get_real_axis_pos(tag) for tag, ax in self.real_axes.items()
        }
        txt = self._format_info(
            self.energy_axis.position,
            self.bragg_axis.position,
            reals2pos,
            info=self._align_info,
        )
        return txt

    def _init(self):
        if self.trajectory_mode:
            self.load_trajectory()
        super()._init()

    def _format_info(self, energy, bragg, reals2pos, info=None):
        raise NotImplementedError

    def _get_pos(self, tag):
        """return one of the position coordinates [xpos, ypos, zpos] or
        orientation angles (pitch, yaw) expressed in the laboratory referential
        (i.e taking into account a possible spectrometer origin != [0,0,0]).
        The returned value must be computed from the actual real axes position to reflect
        actual positioner situation.
        args: tag is one of ["xpos", "ypos", "zpos", pitch, "yaw"]
        """
        # === !!! motor position is expressed in lab ref (so it already includes referential_origin offset) !!!
        raise NotImplementedError

    def _get_real_axis_pos(self, tag):
        if self.trajectory_mode:
            bragg = self.trajectory_axis.position
            if numpy.isnan(bragg):
                return bragg

            if not self._current_bragg_solution or (
                bragg != self._current_bragg_solution[0]
            ):
                self._update_bragg_solution(bragg)
            reals = self._current_bragg_solution[2]
            theo_pos = reals[tag]
            return theo_pos
        else:
            return self.real_axes[tag].position

    def _positioner_to_lab_matrix(self, analyser_position):
        """Positioner referential [ex,ey,ez] expressed in laboratory referential [X,Y,Z].
        Default geometry assumes that the positioner is rotated by 180 degree around +Z,
        so that ex is along -X.
        """
        # ex = numpy.array([-1, 0, 0])
        # ez = numpy.array([0, 0, 1])
        # ey = numpy.cross(ez, ex)
        # p = numpy.vstack((ex, ey, ez)).T
        return numpy.array([[-1, 0, 0], [0, -1, 0], [0, 0, 1]])

    def _lab_to_positioner_matrix(self, position):
        return numpy.linalg.inv(self._positioner_to_lab_matrix(position))

    def _update_bragg_solution(self, bragg):
        self._current_bragg_solution = self.compute_bragg_solution(bragg)

    def _update_bragg_solution_on_traj_move_done(self, is_move_done, sender=None):
        if is_move_done:
            bragg = self.trajectory_axis.position
            self._update_bragg_solution(bragg)

    def _reals2bragg(self, reals_positions):
        if self._current_bragg_solution is None:
            return numpy.nan

        bragg, _, theo_pos = self._current_bragg_solution
        for k, v in reals_positions.items():
            if abs(theo_pos[k] - v) > self.real_axes[k].tolerance:
                return numpy.nan

        return bragg

    def _bragg2reals(self, bragg):
        if isinstance(bragg, numpy.ndarray):
            reals = [self.compute_bragg_solution(ang)[2] for ang in bragg]
            pos = dict(zip(reals[0].keys(), zip(*[x.values() for x in reals])))
            return pos

        self._update_bragg_solution(bragg)
        return self._current_bragg_solution[2]

    def _update(self):
        """Recompute the solution (using current bragg value) and update the plot.
        To be used after a parameter has been changed.
        """
        if self._current_bragg_solution:
            self._update_bragg_solution(self._current_bragg_solution[0])
            self.bragg_calc_controller.sync_pseudos()
            self.energy_calc_controller.sync_pseudos()

    def _check_alignment(self):
        """compare current solution to current motors positions"""

        if not self._current_bragg_solution:
            self._align_info = "no current bragg solution"
            return False
        else:
            bragg = self._current_bragg_solution[0]
            if self.bragg_axis.position != bragg:
                self._align_info = "bragg axis != bragg theo"
                return False
            elif not self.trajectory_mode:
                theo_real_pos = self._current_bragg_solution[2]
                curr_real_pos = {tag: ax.position for tag, ax in self.real_axes.items()}
                for tag in curr_real_pos.keys():
                    delta = abs(curr_real_pos[tag] - theo_real_pos[tag])
                    if delta > self.real_axes[tag].tolerance:
                        self._align_info = f"{tag} not aligned: real - theo = {curr_real_pos[tag]:.2f} - {theo_real_pos[tag]:.2f} = {delta:.2e}"
                        return False

        self._align_info = "ALIGNED"
        return True

    @property
    def rpos(self):
        return self._get_pos("rpos")

    @property
    def xpos(self):
        return self._get_pos("xpos")

    @property
    def ypos(self):
        return self._get_pos("ypos")

    @property
    def zpos(self):
        return self._get_pos("zpos")

    @property
    def pitch(self):
        return self._get_pos("pitch")

    @property
    def yaw(self):
        return self._get_pos("yaw")

    @property
    def position(self):
        """Return the position (x, y, z) in laboratory referential as a numpy array"""
        return numpy.array([self.xpos, self.ypos, self.zpos])

    @property
    def direction(self):
        """Compute the direction vector (normalized) based on the current pitch and yaw angles.
        If pitch = yaw = 0 the direction vector is colinear to the x axis of this positioner referential.
        """

        # start with a default direction along ex and apply pitch and yaw rotations
        v = numpy.array([1, 0, 0])

        pit = numpy.deg2rad(self.pitch)
        yaw = numpy.deg2rad(self.yaw)

        cp = numpy.cos(pit)
        sp = numpy.sin(pit)
        cy = numpy.cos(yaw)
        sy = numpy.sin(yaw)

        # Rx = numpy. array([[1, 0, 0], [ 0, cr, -sr], [0, sr, cr]]) # rot mat around ex (roll)
        Ry = numpy.array(
            [[cp, 0, sp], [0, 1, 0], [-sp, 0, cp]]
        )  # rot mat around ey (pitch)
        Rz = numpy.array(
            [[cy, -sy, 0], [sy, cy, 0], [0, 0, 1]]
        )  # rot mat around ez (yaw)

        v = Rz @ Ry @ v  # Rz @ Ry @ Rx @ v  # apply rotations in the order:  Z->Y->X

        return v

    @property
    def normal(self):
        """Compute the normal vector of this item (normalized) in laboratory referential"""
        return (
            self._positioner_to_lab_matrix(self.position - self.referential_origin)
            @ self.direction
        )

    @property
    def real_axes(self):
        if self.__real_axes is None:
            self.__real_axes = {
                self.bragg_calc_controller._axis_tag(ax): ax
                for ax in self.bragg_calc_controller.reals
            }
        return self.__real_axes

    @property
    def is_aligned(self):
        return self._check_alignment()

    @autocomplete_property
    def bragg_axis(self):
        if self.trajectory_mode:
            return self.trajectory_axis
        else:
            return self.bragg_calc_controller._tagged["bragg"][0]

    @autocomplete_property
    def trajectory_axis(self):
        if self.__trajectory_axis is None:
            axis = self.config["trajectory_axis"]
            if not isinstance(axis.controller, self.VALID_TRAJ_AXIS_CTRL_CLASS):
                raise ValueError(
                    f"trajectory_axis controller's type {axis.controller.__class__} is not compatible, it should be one of {self.VALID_TRAJ_AXIS_CTRL_CLASS}"
                )
            self.__trajectory_axis = axis
            event.connect(
                self.__trajectory_axis,
                "move_done",
                self._update_bragg_solution_on_traj_move_done,
            )

        return self.__trajectory_axis

    @property
    def trajectory_mode(self):
        return self._get_setting("trajectory_mode")

    @trajectory_mode.setter
    def trajectory_mode(self, value):
        value = bool(value)

        if value:
            # load and check traj axis compatibility
            self.trajectory_axis

        if value != self._get_setting("trajectory_mode"):

            if value:
                bragg_pos = self.bragg_axis.position

            if self._energy_calc_controller is not None:
                self._energy_calc_controller.close()
                self._energy_calc_controller = None

            if self._bragg_calc_controller is not None:
                self._bragg_calc_controller.close()
                self._bragg_calc_controller = None

            if value:
                self.load_trajectory()
                if not numpy.isnan(bragg_pos):
                    self.trajectory_axis.move(bragg_pos)

            self._set_setting("trajectory_mode", value)

            self.energy_calc_controller.sync_pseudos()

    def load_trajectory(self):
        if isinstance(
            self.trajectory_axis.controller, Icepap
        ):  # filter mockup traj axis case
            trajectory_low_limit = self.config.get("bragg_low_limit", None)
            if trajectory_low_limit is None:
                raise RuntimeError("No low limit for detector trajectory")

            trajectory_high_limit = self.config.get("bragg_high_limit", None)
            if trajectory_high_limit is None:
                raise RuntimeError("No high limit for detector trajectory")

            if trajectory_low_limit < trajectory_high_limit:
                start_traj = trajectory_low_limit
                stop_traj = trajectory_high_limit
            else:
                start_traj = trajectory_high_limit
                stop_traj = trajectory_low_limit

            trajectory_resolution = self.config.get(
                "trajectory_resolution", 10
            )  # Nb points per bragg angle unit

            traj_npb = int((stop_traj - start_traj) * trajectory_resolution)
            traj_bragg_data = numpy.linspace(start_traj, stop_traj, traj_npb)

            traj_real_mot_data = {}
            for ax, positions in self.get_bragg_to_reals(traj_bragg_data).items():
                if not isinstance(
                    ax.controller, Icepap
                ):  # ensure real axes are icepap axes
                    raise RuntimeError(f"Axis '{ax.name}' is not an Icepap Axis")
                traj_real_mot_data[ax.name] = numpy.array(positions) - ax.offset
                self.trajectory_axis.set_positions(
                    traj_bragg_data, traj_real_mot_data, self.trajectory_axis.SPLINE
                )

    @property
    def _current_bragg_solution(self):
        return self._get_setting("bragg_solution")

    @_current_bragg_solution.setter
    def _current_bragg_solution(self, value):
        self._set_setting("bragg_solution", value)

    def compute_bragg_solution(self, bragg):
        """returns a tuple (bragg, bragg_solution, reals_positions):
        - bragg: the bragg angle associated to this solution.
        - bragg_solution: a dict with relevant data for the position and orientation of this positioner.
          Data expressed in the laboratory referential with an origin at (0,0,0) (i.e. ignoring self.referential_origin).
        - reals_positions: the theoritical positions of the real axes for the given bragg value.
          Reals positions expressed in laboratory referential (must take into account self.referential_origin!=(0,0,0))
        """
        raise NotImplementedError

    def set_energy(self, energy, interactive=True):
        if interactive:
            print("!!! Warning this will modify the offset of all real axes !!!\n")
            print("Type 'y' to confirm or anything else to abort: ")
            x = input()
            if x != "y":
                return

        bragg = self.energy_calc_controller.energy2bragg(energy)
        self.set_bragg(bragg, interactive=False)

    def set_bragg(self, bragg, interactive=True):
        if interactive:
            print("!!! Warning this will modify the offset of all real axes !!!\n")
            print("Type 'y' to confirm or anything else to abort: ")
            x = input()
            if x != "y":
                return

        reals = self.compute_bragg_solution(bragg)[2]
        for tag, theo_pos in reals.items():
            self.real_axes[tag].position = theo_pos

        if self.trajectory_mode:
            self.load_trajectory()

    def get_bragg_to_reals(self, bragg_values):
        """computes real axes positions corresponding to bragg values.
        args:
          - bragg_values: a list of bragg values
        return: {real_axis: positions, } with positions a list of positions
        """
        axes2positions = {}
        for bragg in bragg_values:
            theo_real_pos = self.compute_bragg_solution(bragg)[2]
            for tag, pos in theo_real_pos.items():
                ax = self.real_axes[tag]
                axes2positions.setdefault(ax, []).append(pos)
        return axes2positions


class Analyser(SpectrometerPositioner, HasMetadataForScan):
    def __init__(self, config):
        self.__xtals = None
        super().__init__(config)

    def _load_settings(self):
        """Get from redis the dict of persistent parameters (redis access)"""
        cached = super()._load_settings()
        cached["miscut"] = self._settings.get("miscut", self.config["miscut"])
        cached["radius"] = self._settings.get("radius", self.config["radius"])
        cached["offset_on_detector"] = self._settings.get(
            "offset_on_detector", self.config["offset_on_detector"]
        )
        return cached

    def _build_bragg_calc_controller(self):
        # add pseudo bragg
        if self.config.get("bragg_axis"):
            bragg_axis_name = self.config.get("bragg_axis")[0]["name"]
        else:
            bragg_axis_name = f"bragg_{self.name}"
        calc_cfg = [{"name": bragg_axis_name, "tags": "bragg"}]

        # add reals axes
        for ax_cfg in self.config["real_axes"]:
            tag, ax = list(ax_cfg.items())[0]
            calc_cfg.append({"name": ax, "tags": f"real {tag}"})

        cc = BraggCalcController(self, {"axes": calc_cfg})
        cc._initialize_config()
        return cc

    def _build_energy_calc_controller(self):

        # add pseudo energy
        if self.config.get("energy_axis"):
            energy_axis_name = self.config.get("energy_axis")[0]["name"]
        else:
            energy_axis_name = f"energy_{self.name}"
        calc_cfg = [{"name": energy_axis_name, "tags": "energy"}]

        # add real bragg
        calc_cfg.append({"name": self.bragg_axis, "tags": "real bragg"})

        cc = EnergyCalcController(self, {"axes": calc_cfg})
        cc._initialize_config()
        return cc

    def _format_info(self, energy, bragg, reals2pos, info=None):
        info_dict = {
            "name": self.name,
            f"{self.energy_axis.name}": energy,
            f"{self.bragg_axis.name}": bragg,
        }
        info_dict.update(reals2pos)
        info_dict.update(
            {
                "radius": self.radius,
                "miscut": self.miscut,
                "detoff": self.offset_on_detector,
                "crystal": self.xtal_sel,
            }
        )
        if info is not None:
            info_dict["info"] = info

        tab = IncrementalTable(
            [list(info_dict.keys())], col_sep="|", flag="", lmargin="  "
        )
        tab.add_line(info_dict.values())
        tab.resize(10, 60)
        tab.add_separator("-", line_index=1)
        txt = "\n" + str(tab)
        return txt

    @autocomplete_property
    def xtals(self):
        if self.__xtals is None:
            self.__xtals = XtalManager(
                {"name": f"xtals_{self.name}", "xtals": self.config["xtals"]}
            )
            if self.__xtals.xtal_sel is None:
                self.__xtals.xtal_sel = self.__xtals.xtal_names[0]
        return self.__xtals

    @property
    def xtal_sel(self):
        return self.xtals.xtal_sel

    @xtal_sel.setter
    def xtal_sel(self, value):
        self.xtals.xtal_sel = value
        self._update()

    @property
    def radius(self):
        return self._get_setting("radius")

    @radius.setter
    def radius(self, value):
        self._set_setting("radius", value)
        self._update()

    @property
    def miscut(self):
        return self._get_setting("miscut")

    @miscut.setter
    def miscut(self, value):
        self._set_setting("miscut", value)
        self._update()

    @property
    def offset_on_detector(self):
        return self._get_setting("offset_on_detector")

    @offset_on_detector.setter
    def offset_on_detector(self, value):
        self._set_setting("offset_on_detector", value)
        self._update()

    @property
    def incident(self):
        """Compute the normalized direction of the incident ray"""
        return normalize(self.position - self.referential_origin) * -1

    @property
    def reflected(self):
        """Compute the direction of the reflected ray toward surface normal (normalized).
        Takes into account the possible miscut between surface and crystal planes.
        """
        # rotation of 2*(beta-miscut) in meridional plane
        beta = numpy.rad2deg(numpy.arccos(numpy.dot(self.incident, self.normal)))
        if self.normal[2] >= self.incident[2]:
            sign = 1
        else:
            sign = -1

        beta = beta * sign
        ang = 2 * (beta - self.miscut)
        return numpy.dot(rotation_matrix(self.meridional * sign, ang), self.incident)

    @property
    def meridional(self):
        """Compute the normal to the meridional plan (normalized)."""
        return normalize(numpy.cross(self.incident, self.normal))

    @property
    def sagittal(self):
        """Compute the normal to the sagittal plan (normalized)."""
        return normalize(numpy.cross(self.normal, self.meridional))

    @property
    def geo_bragg(self):
        return round(
            bragg_from_vect(self.incident, self.normal, self.miscut), DIGITS_TOLERANCE
        )

    @property
    def geo_energy(self):
        return self.xtals.bragg2energy(self.geo_bragg)

    def scan_metadata(self):
        meta_dict = {"@NX_class": "NXcollection"}
        meta_dict["crystal"] = str(self.xtal_sel)
        meta_dict["miscut"] = self.miscut
        meta_dict["radius"] = self.radius
        meta_dict["referential_origin"] = self.referential_origin
        meta_dict["offset_on_detector"] = self.offset_on_detector
        return meta_dict

    def check_bragg(self, bragg):
        """compute and display the solution for a given bragg angle"""
        bragg, _, theo_pos = self.compute_bragg_solution(bragg)
        energy = self.xtals.bragg2energy(bragg)
        names2theo = {self.real_axes[tag].name: pos for tag, pos in theo_pos.items()}
        txt = self._format_info(energy, bragg, names2theo)
        print(txt + "\n")

    def check_energy(self, energy):
        """compute and display the solution for a given energy"""
        self.check_bragg(self.xtals.energy2bragg(energy))


class Detector(SpectrometerPositioner, HasMetadataForScan):
    def __init__(self, config):
        super().__init__(config)
        self._target = None

    def _format_info(self, energy, bragg, reals2pos, info=None):
        info_dict = {
            "name": self.name,
            f"{self.energy_axis.name}": energy,
            f"{self.bragg_axis.name}": bragg,
        }
        info_dict.update(reals2pos)
        info_dict.update({"target": self.target.name})
        if info is not None:
            info_dict["info"] = info
        tab = IncrementalTable(
            [list(info_dict.keys())], col_sep="|", flag="", lmargin="  "
        )
        tab.add_line(info_dict.values())
        tab.resize(10, 60)
        tab.add_separator("-", line_index=1)
        txt = "\n" + str(tab)
        return txt

    def _get_pos(self, tag):
        """return one of the position coordinates [xpos, ypos, zpos] or
        orientation angles (pitch, yaw) expressed in the laboratory referential
        (i.e taking into account a possible spectrometer origin != [0,0,0]).
        The returned value must be computed from the actual real axes position to reflect
        actual positioner situation.
        args: tag is one of ["xpos", "ypos", "zpos", pitch, "yaw"]
        """
        # === !!! motor position is expressed in lab ref (so it already includes referential_origin offset) !!!
        ypos = self.config.get("ypos", 0) + self.referential_origin[1]
        yaw = self.config.get("yaw", 0)
        if tag == "rpos":
            x = self._get_real_axis_pos("xpos")
            y = ypos
            return numpy.sqrt(x**2 + y**2)
        elif tag == "ypos":
            return ypos
        elif tag == "yaw":
            return yaw
        elif tag in ["xpos", "zpos", "pitch"]:
            return self._get_real_axis_pos(tag)
        raise RuntimeError(f"unknown tag {tag}")

    def _build_bragg_calc_controller(self):
        # add pseudo bragg
        if self.config.get("bragg_axis"):
            bragg_axis_name = self.config.get("bragg_axis")[0]["name"]
        else:
            bragg_axis_name = f"bragg_{self.name}"
        calc_cfg = [{"name": bragg_axis_name, "tags": "bragg"}]

        # add reals axes
        for ax_cfg in self.config["real_axes"]:
            tag, ax = list(ax_cfg.items())[0]
            calc_cfg.append({"name": ax, "tags": f"real {tag}"})

        cc = BraggCalcController(self, {"axes": calc_cfg})
        cc._initialize_config()
        return cc

    def _build_energy_calc_controller(self):

        # add pseudo energy
        if self.config.get("energy_axis"):
            energy_axis_name = self.config.get("energy_axis")[0]["name"]
        else:
            energy_axis_name = f"energy_{self.name}"
        calc_cfg = [{"name": energy_axis_name, "tags": "energy"}]

        # add real bragg
        calc_cfg.append({"name": self.bragg_axis, "tags": "real bragg"})

        cc = EnergyCalcController(self, {"axes": calc_cfg})
        cc._initialize_config()
        return cc

    def _check_alignment(self):
        if not numpy.isnan(self.bragg_axis.position):
            if self.bragg_axis.position != self.target.bragg_axis.position:
                self._align_info = "bragg != target_bragg"
                return False
        return super()._check_alignment()

    @autocomplete_property
    def xtals(self):
        return self.target.xtals

    @autocomplete_property
    def target(self):
        if self._target is None:
            self._target = self.config["target"]
        return self._target

    @target.setter
    def target(self, value):
        if not isinstance(value, Analyser):
            raise ValueError(f"target {value} is not an Analyser object")
        self._target = value
        self._update()

    @property
    def geo_bragg(self):
        return self.target.geo_bragg

    @property
    def geo_energy(self):
        return self.target.geo_energy

    def scan_metadata(self):
        meta_dict = {"@NX_class": "NXcollection"}
        meta_dict["target"] = self.target.name
        return meta_dict

    def compute_bragg_solution(self, bragg):
        """returns a tuple (bragg, bragg_solution, reals_positions):
        - bragg: the bragg angle associated to this solution.
        - bragg_solution: a dict with relevant data for the position and orientation of this positioner.
          Data expressed in the laboratory referential with an origin at (0,0,0) (i.e. ignoring self.referential_origin).
        - reals_positions: the theoritical positions of the real axes for the given bragg value.
          Reals positions expressed in laboratory referential (must take into account self.referential_origin!=(0,0,0))
        """
        bsolution = self.target.compute_bragg_solution(bragg)[1]
        pitch, yaw = direction_to_angles(bsolution["Ai"] - bsolution["Di"])
        lab_Di = bsolution["Di"] + self.referential_origin
        reals_pos = {}
        reals_pos["xpos"] = lab_Di[0]
        reals_pos["zpos"] = lab_Di[2]
        reals_pos["pitch"] = pitch
        return (
            bragg,
            {
                "Di": bsolution["Di"],
                "Nid": bsolution["Nid"],
                "D0": bsolution["D0"],
                "N0d": bsolution["N0d"],
                "pitch": pitch,
                "yaw": yaw,
            },
            reals_pos,
        )

    def check_bragg(self, bragg):
        """compute and display the solution for a given bragg angle"""
        bragg, _, theo_pos = self.compute_bragg_solution(bragg)
        energy = self.target.xtals.bragg2energy(bragg)
        names2theo = {self.real_axes[tag].name: pos for tag, pos in theo_pos.items()}
        txt = self._format_info(energy, bragg, names2theo)
        print(txt + "\n")

    def check_energy(self, energy):
        """compute and display the solution for a given energy"""
        self.check_bragg(self.target.xtals.energy2bragg(energy))


class Spectrometer(SpectrometerItem, HasMetadataForScan):
    def __init__(self, config):
        self._analysers = None
        self._detector = None
        super().__init__(config)

        self._plot = None
        self._plot_axes_connected = False
        self._plot_sagital_circles = False
        self._plot_axes_moving = set()

        global_map.register(self, parents_list=["controllers"])

    def __del__(self):
        self.__close__()

    def __close__(self):
        self.close()

    def close(self):
        super().close()

        # close analysers
        for ana in self.analysers:
            ana.close()

        # close detector
        self.detector.close()

        # disconnect axes from plot
        self._disconnect_axes()

    def __info__(self):
        estate, bstate = self.is_aligned
        state2txt = {True: "ALIGNED", False: "NOT ALIGNED !!!"}
        aligned = f"ENERGY {state2txt[estate]}"
        aligned += f" ({self.energy_axis.position:.4f} keV)"
        aligned += f" | BRAGG {state2txt[bstate]}"
        aligned += f" ({self.bragg_axis.position:.4f} deg)"

        title = f" Spectrometer ({self.name}): {aligned} "
        line = "=" * len(title)
        txt = "\n".join(["\n", line, title, line, "\n\n"])

        txt += f"* ITEMS POSITIONS IN LABORATORY REFERENTIAL (spectro origin = {self.referential_origin}):\n\n"
        head = [
            "name",
            "energy",
            "bragg",
            "rpos",
            "xpos",
            "ypos",
            "zpos",
            "pitch",
            "yaw",
        ]
        tab = IncrementalTable([head], col_sep="|", flag="", lmargin="  ")
        for ana in self._active_analysers:
            values = [
                ana.name,
                ana.geo_energy,
                ana.geo_bragg,
                ana.rpos,
                ana.xpos,
                ana.ypos,
                ana.zpos,
                ana.pitch,
                ana.yaw,
            ]
            tab.add_line(values)
        values = [
            self.detector.name,
            self.detector.energy_axis.position,
            self.detector.bragg_axis.position,
            self.detector.rpos,
            self.detector.xpos,
            self.detector.ypos,
            self.detector.zpos,
            self.detector.pitch,
            self.detector.yaw,
        ]
        tab.add_line(values)
        tab.resize(10, 16)
        tab.add_separator("-", line_index=1)
        txt += str(tab)

        txt += "\n\n\n* ANALYSERS AXES & PARAMETERS:\n"
        for ana in self._active_analysers:
            txt += ana.__info__() + "\n"

        txt += "\n\n\n* DETECTOR AXES & PARAMETERS:\n"
        txt += self.detector.__info__() + "\n"

        return txt

    def _init(self):
        # force all items to share same sample position
        self.referential_origin = (
            self.referential_origin
        )  # this already updates bragg solution of all positioners

    def _get_plot_data(self):
        """get positions and orientations of the various items"""

        blue_navy = [167, 189, 235]
        red_darker = [172, 74, 85]
        purple = [171, 147, 229]

        # col_det_pos = list(
        #     numpy.array(self.config.get("col_det_pos", blue_navy)) / 255.0
        # )
        col_det_dir = list(
            numpy.array(self.config.get("col_det_dir", blue_navy)) / 255.0
        )
        col_det_pln = list(
            numpy.array(self.config.get("col_det_pln", blue_navy)) / 255.0
        )

        col_beam = list(numpy.array(self.config.get("col_beam", red_darker)) / 255.0)
        col_ref = list(numpy.array(self.config.get("col_ref", red_darker)) / 255.0)

        col_frozen = list(numpy.array(self.config.get("col_frozen", purple)) / 255.0)

        trans_l1 = 0.8
        trans_l2 = 0.5
        trans_l3 = 0.3
        trans_l4 = 0.1

        quivers = []
        plots = []
        scatters = []
        polygons = []
        scale = self.detector.target.radius / 5

        sx, sy, sz = self.referential_origin

        # Spectrometer base vectors
        # vectors = []
        # vectors.append([sx, sy, sz, scale, 0, 0])  # ex
        # vectors.append([sx, sy, sz, 0, scale, 0])  # ey
        # vectors.append([sx, sy, sz, 0, 0, scale])  # ez
        # quivers.append((transpose(vectors), {"color": col_ref, "alpha": trans_l3}))
        scatters.append(
            (
                transpose(
                    [
                        [sx, sy, sz],
                    ]
                ),
                {"color": col_ref, "alpha": trans_l1},
            )
        )

        # Detector position
        # scatters.append((self.detector.position, {"color": col_det_pos, "alpha": trans_l1}))

        # Detector direction
        quivers.append(
            (
                list(self.detector.position) + list(self.detector.direction * scale),
                {"color": col_det_dir, "alpha": trans_l1},
            )
        )

        # Detector plan
        pts = get_normal_plane(
            self.detector.direction,
            self.detector.config.get("width", scale),
            self.detector.config.get("height", scale),
            self.detector.position,
        )
        polygons.append(
            (
                [[pts]],
                {
                    "color": col_det_pln,
                    "linewidth": 0,
                    "antialiased": False,
                    "alpha": trans_l3,
                },
            )
        )

        # Active analysers
        for ana in self._active_analysers:

            col_ana_pos = list(
                numpy.array(
                    ana.config.get(
                        "col_ana_pos", self.config.get("col_ana_pos", purple)
                    )
                )
                / 255.0
            )
            col_ana_dir = list(
                numpy.array(
                    ana.config.get(
                        "col_ana_dir", self.config.get("col_ana_dir", purple)
                    )
                )
                / 255.0
            )
            col_ana_rwl = list(
                numpy.array(
                    ana.config.get(
                        "col_ana_rwl", self.config.get("col_ana_rwl", purple)
                    )
                )
                / 255.0
            )

            pos = ana.position
            meri = ana.meridional
            mradius = ana.normal * ana.radius
            mcenter = pos + mradius
            dist = getnorm(pos - self.referential_origin)
            refl = ana.reflected * dist * 1.2

            # beam ray tracing: sample => analyser => reflection
            plots.append(
                (
                    transpose([[sx, sy, sz], list(pos), list(pos + refl)]),
                    {
                        "color": col_beam,
                        "linestyle": "solid",
                        "linewidth": 0.8,
                        "alpha": trans_l2,
                    },
                )
            )

            # rowland circles
            rowland_circles = []
            for theta in range(361):
                rowland_circles.append(
                    numpy.dot(rotation_matrix(meri, theta), mradius) + mcenter
                )
            plots.append(
                (
                    transpose(rowland_circles),
                    {
                        "color": col_ana_rwl,
                        "linestyle": "dotted",
                        "linewidth": 0.8,
                        "alpha": trans_l2,
                    },
                )
            )

            # meridional normal
            # quivers.append((list(mcenter) + list(meri * scale), {"color": col_ana_pos, "alpha": trans_l1}))

            # meridional circle radius
            # plots.append(
            #     (
            #         transpose([pos, mcenter, mcenter + mradius]),
            #         {"color": col_ana_pos, "linestyle": "dotted", "linewidth": 0.8, "alpha": trans_l1},
            #     )
            # )

            # Analyser current position
            scatters.append(
                (
                    transpose(
                        [
                            pos,
                        ]
                    ),
                    {
                        "color": col_ana_pos,
                        "alpha": trans_l1,
                    },
                )
            )

            # Analyser current direction
            x, y, z = pos
            u, v, w = ana.normal * scale * 1.2
            quivers.append(
                (
                    [x, y, z, u, v, w],
                    {
                        "color": col_ana_dir,
                        "alpha": trans_l3,
                    },
                )
            )

        # Frozen analysers
        for ana_name in self.frozen:
            ana = self.analysers[ana_name]

            # current position and direction
            pos = ana.position
            meri = ana.meridional
            mradius = ana.normal * ana.radius
            mcenter = pos + mradius
            dist = getnorm(pos - self.referential_origin)
            refl = ana.reflected * dist * 1.2

            # beam ray tracing: sample => analyser => reflection
            plots.append(
                (
                    transpose([[sx, sy, sz], list(pos), list(pos + refl)]),
                    {
                        "color": col_beam,
                        "linestyle": "dashed",
                        "linewidth": 0.8,
                        "alpha": trans_l4,
                    },
                )
            )

            # Frozen analysers position
            scatters.append(
                (
                    transpose(
                        [
                            pos,
                        ]
                    ),
                    {
                        "color": col_frozen,
                        "alpha": trans_l3,
                    },
                )
            )

        return {
            "quivers": quivers,
            "plots": plots,
            "scatters": scatters,
            "polygons": polygons,
        }

    def _load_settings(self):
        """Get from redis the dict of persistent spectrometer parameters (redis access)"""
        cached = super()._load_settings()
        cached["referential_origin"] = self._settings.get(
            "referential_origin", [0, 0, 0]
        )
        cached["frozen_analysers"] = self._settings.get("frozen_analysers", [])
        return cached

    def _get_all_reals(self):
        axes = []
        for ana in self.analysers:
            axes.extend(list(ana.real_axes.values()))
        axes.extend(list(self.detector.real_axes.values()))
        return axes

    def _build_bragg_calc_controller(self):

        # add pseudo bragg
        if self.config.get("bragg_axis"):
            bragg_axis_name = self.config.get("bragg_axis")[0]["name"]
        else:
            bragg_axis_name = f"bragg_{self.name}"
        calc_cfg = [{"name": bragg_axis_name, "tags": "bragg"}]

        # add real detector bragg
        calc_cfg.append(
            {
                "name": self.detector.bragg_axis,
                "tags": f"real bragg_{self.detector.name}",
            }
        )
        # add reals analyser bragg
        for ana in self.analysers:
            calc_cfg.append({"name": ana.bragg_axis, "tags": f"real bragg_{ana.name}"})

        cc = BraggIdentityCalcController(self, {"axes": calc_cfg})
        cc._initialize_config()

        return cc

    def _build_energy_calc_controller(self):

        # add pseudo energy
        if self.config.get("energy_axis"):
            energy_axis_name = self.config.get("energy_axis")[0]["name"]
        else:
            energy_axis_name = f"energy_{self.name}"
        calc_cfg = [{"name": energy_axis_name, "tags": "energy"}]

        # add real detector energy
        calc_cfg.append(
            {
                "name": self.detector.energy_axis,
                "tags": f"real energy_{self.detector.name}",
            }
        )
        # add reals analyser energy
        for ana in self.analysers:
            calc_cfg.append(
                {"name": ana.energy_axis, "tags": f"real energy_{ana.name}"}
            )

        # del ene_cfg['name']
        cc = EnergyIdentityCalcController(self, {"axes": calc_cfg})
        cc._initialize_config()
        return cc

    def _connect_to_axes_moves(self):
        if not self._plot_axes_connected:
            for ax in self._get_all_reals():
                event.connect(ax, "position", self._update_plot_on_position)
                event.connect(ax, "move_done", self._update_plot_on_move_done)
            self._plot_axes_connected = True

    def _disconnect_axes(self):
        if self._plot_axes_connected:
            for ax in self._get_all_reals():
                event.disconnect(ax, "position", self._update_plot_on_position)
                event.disconnect(ax, "move_done", self._update_plot_on_move_done)
            self._plot_axes_connected = False

    def _update_plot_on_move_done(self, is_move_done, sender=None):
        if self._plot:
            if not is_move_done:
                self._plot_axes_moving.add(sender.name)
            else:
                self._plot_axes_moving.remove(sender.name)
                if len(self._plot_axes_moving) == 0:
                    self._plot.update_plot(forced=True)

    def _update_plot_on_position(self, position, sender=None):
        self._update_plot(forced=False)

    def _update_plot(self, forced=False):
        if self._plot:
            self._plot.update_plot(forced)

    def _update(self):
        self.bragg_calc_controller.sync_pseudos()
        self.energy_calc_controller.sync_pseudos()
        self._update_plot(forced=True)

    def _check_alignment(self):
        """check if all items (analysers + detector) are aligned
        and if they are all at the same energy and bragg angle.
        returns a tuple (energies_aligned, bragg_angles_aligned)
        """

        bragg = energy = False
        items = self._active_positioners
        for it in items:
            if not it.is_aligned:
                return (False, False)

        if len(set([it.energy_axis.position for it in items])) == 1:
            energy = True
        if len(set([it.bragg_axis.position for it in items])) == 1:
            bragg = True

        return (energy, bragg)

    @autocomplete_property
    def detector(self):
        if self._detector is None:
            self._detector = self.config["detector"]
        return self._detector

    @autocomplete_property
    def analysers(self):
        if self._analysers is None:
            self._analysers = [cfg["name"] for cfg in self.config["analysers"]]
        return counter_namespace(self._analysers)

    @property
    def _active_analysers(self):
        """Return the active analysers"""
        actives = []
        frozen = self._get_setting("frozen_analysers")
        for ana in self.analysers:
            if ana.name not in frozen:
                actives.append(ana)
        return actives

    @property
    def _active_positioners(self):
        """Return the active analysers and detecor"""
        return self._active_analysers + [self.detector]

    @property
    def frozen(self):
        return self._get_setting("frozen_analysers")

    @property
    def referential_origin(self):
        return numpy.array(self._get_setting("referential_origin"))

    @referential_origin.setter
    def referential_origin(self, ref_coords):
        """Define the position [x, y, z] of the spectrometer origin in the laboratory referential"""
        if len(ref_coords) != 3:
            raise ValueError(
                f"origin coordinates must be a vector [x, y, z] not {ref_coords}"
            )
        self._set_setting("referential_origin", ref_coords)
        for ana in self.analysers:
            ana.referential_origin = ref_coords
        self.detector.referential_origin = ref_coords
        self._update()

    @property
    def is_aligned(self):
        return self._check_alignment()

    def set_trajectory_mode(self, value, *items):
        if items:
            if self._energy_calc_controller is not None:
                self._energy_calc_controller.close()
                self._energy_calc_controller = None

            if self._bragg_calc_controller is not None:
                self._bragg_calc_controller.close()
                self._bragg_calc_controller = None

            for item in items:
                item.trajectory_mode = value

            self._update()

    def freeze(self, *analysers):
        frozen = self._get_setting("frozen_analysers")
        target = self.detector.target.name
        all_ana_names = [x.name for x in self.analysers]

        # handle empty args as all analysers except target
        if not analysers:
            analysers = list(self.analysers)
            analysers.remove(self.detector.target)

        # remove actual target if still in frozen
        if self.detector.target.name in frozen:
            frozen.remove(self.detector.target.name)

        request_names = []
        for ana in analysers:
            aname = None
            if isinstance(ana, str):
                if ana in all_ana_names:
                    aname = ana
            elif ana in self.analysers:
                aname = ana.name

            # raise for unknown analysers
            if aname is None:
                raise ValueError(f"unknown analyser '{ana}'")

            request_names.append(aname)

        if target in request_names:
            raise ValueError("cannot freeze the target analyser of the detector")

        frozen.extend(request_names)
        frozen = list(set(frozen))

        self._set_setting("frozen_analysers", frozen)
        self._update()

    def unfreeze(self, *analysers):
        frozen = self._get_setting("frozen_analysers")
        all_ana_names = [x.name for x in self.analysers]

        # handle empty args as all analysers
        if not analysers:
            analysers = list(self.analysers)

        for ana in analysers:
            aname = None
            if isinstance(ana, str):
                if ana in all_ana_names:
                    aname = ana
            elif ana in self.analysers:
                aname = ana.name

            # raise for unknown analysers
            if aname is None:
                raise ValueError(f"unknown analyser '{ana}'")

            try:
                frozen.remove(aname)
            except ValueError:
                # ignore unfrozen analysers
                pass

        self._set_setting("frozen_analysers", frozen)
        self._update()

    def scan_metadata(self):
        meta_dict = {"@NX_class": "NXcollection"}
        meta_dict["referential_origin"] = self.referential_origin
        meta_dict["active_analysers"] = [ana.name for ana in self._active_analysers]
        return meta_dict

    def check_bragg(self, bragg):
        """compute and display the solution for a given bragg angle"""

        title = f"   Solution for bragg = {bragg} deg "
        line = "  " + "=" * len(title)
        txt = "\n".join(["", line, title, line, ""])
        print(txt)
        for item in self._active_positioners:
            item.check_bragg(bragg)
        print("\n")

    def check_energy(self, energy):
        """compute and display the solution for a given energy"""

        title = f"   Solution for energy = {energy} keV "
        line = "  " + "=" * len(title)
        txt = "\n".join(["", line, title, line, ""])
        print(txt)
        for item in self._active_positioners:
            item.check_energy(energy)
        print("\n")

    def set_crystal(self, crystal):
        """Set the given crystal (str) on all active analysers"""
        for ana in self._active_analysers:
            ana.xtal_sel = crystal
        self._update()

    def park(self, parkid=None, interactive=True):
        """Move all spectrometer items (analysers and detector) to a parking position defined in YAML configuration.
        If multiple parking positions exist 'parkid' argument must be provided.
        All items must share the same 'parkid' identifier.
        Parking cannot proceed if some analysers are frozen.
        """

        if self.frozen != []:
            raise RuntimeError(
                "Cannot park if some analysers are frozen, use unfreeze() first"
            )

        # check that parking option exist in config
        parknames = self.config.get("parking")
        if parknames is None:
            raise RuntimeError(f"No parking defined in {self.name} configuration")

        # if only one parking identifier (parkid) exist in config, take it as the default.
        if parkid is None:
            if len(parknames) == 1:
                parkid = parknames[0]
            else:
                raise ValueError(
                    f"Multiple parkid found ({parknames}), please specify one"
                )
        elif parkid not in parknames:
            raise ValueError(f"Cannot find parkid '{parkid}' in configuration")

        items = list(self.analysers) + [self.detector]
        # check that all spectrometer's items define such parking identifier
        # and gather parking positions
        ax2pos = {}
        tabs = []
        for item in items:
            match = False
            parkcfgs = item.config.get("parking")

            if parkcfgs is None:
                raise RuntimeError(f"No parking defined in {item.name} configuration")

            for cfg in parkcfgs:
                if cfg["parkid"] == parkid:
                    itemax2pos = {}
                    for tag, ax in item.real_axes.items():
                        if cfg[tag] != "fixed":
                            itemax2pos[ax] = cfg[tag]
                        else:
                            itemax2pos[ax] = ax.position
                    ax2pos.update(itemax2pos)
                    labels = ["item"] + [ax.name for ax in item.real_axes.values()]
                    values = [item.name] + [
                        ax2pos[ax] for ax in item.real_axes.values()
                    ]
                    tabs.append((labels, values))
                    match = True
                    break

            if not match:
                raise ValueError(
                    f"Cannot find parkid '{parkid}' in {item.name} parking configuration"
                )

        if interactive:
            print(
                "!!! Warning this will move all motors to the following positions: !!!\n\n"
            )

            for labels, values in tabs:
                tab = IncrementalTable([labels], col_sep="|", flag="", lmargin="  ")
                tab.add_line(values)
                tab.resize(10, 60)
                tab.add_separator("-", line_index=1)
                print(f"{tab}\n")

            print("Type 'y' to confirm or anything else to abort: ")
            x = input()
            if x != "y":
                return

        group = Group(*ax2pos.keys())
        group.move(ax2pos, wait=True, relative=False)

    def plot(self):
        if self._plot is None:
            self._plot = SpectroPlot(self)
            self._connect_to_axes_moves()
            self._plot_finalizer = weakref.finalize(self._plot, self._disconnect_axes)

        if not self._plot.is_active():
            self._plot.create_plot()

        self._plot.update_plot(forced=True)

        return self._plot


class CylindricalAnalyser(Analyser):
    def _load_config(self):
        super()._load_config()
        self._angular_offset = self.config["angular_offset"]

    def _positioner_to_lab_matrix(self, position):
        """Positioner referential [ex,ey,ez] expressed in laboratory referential [X,Y,Z].
        The cylindrical geometry assumes that ex is pointing to the laboratory origin.
        """
        incident = normalize(position) * -1
        ex = incident
        ez = numpy.array([0, 0, 1])
        ey = numpy.cross(ez, ex)
        ez = numpy.cross(ex, ey)
        p = numpy.vstack((ex, ey, ez)).T
        return p

    @property
    def angular_offset(self):
        return self._angular_offset

    @angular_offset.setter
    def angular_offset(self, value):
        self._angular_offset = value
        self._update()

    def _get_pos(self, tag):
        """return one of the position coordinates [xpos, ypos, zpos] or
        orientation angles (pitch, yaw) expressed in the laboratory referential
        (i.e taking into account a possible spectrometer origin != [0,0,0]).
        The returned value must be computed from the actual real axes position to reflect
        actual positioner situation.
        args: tag is one of ["xpos", "ypos", "zpos", pitch, "yaw"]
        """
        # === !!! motor position is expressed in lab ref (so it already includes referential_origin offset) !!!
        if tag == "xpos":
            ang = numpy.deg2rad(self.angular_offset)
            return self._get_real_axis_pos("rpos") * numpy.cos(ang)
        elif tag == "ypos":
            ang = numpy.deg2rad(self.angular_offset)
            return self._get_real_axis_pos("rpos") * numpy.sin(ang)
        elif tag in ["rpos", "zpos", "pitch", "yaw"]:
            return self._get_real_axis_pos(tag)
        raise RuntimeError(f"unknown tag {tag}")

    def _compute_central_analyser_solution(self, bragg):

        bragg = numpy.deg2rad(bragg)
        miscut = numpy.deg2rad(self.miscut)
        angm = bragg - miscut
        angp = bragg + miscut

        rad = 2 * self.radius  # Rowland circle

        A0x = rad * numpy.sin(angm)
        A0y = 0
        A0z = 0

        D0x = rad * numpy.cos(angp) * numpy.sin(2 * bragg)
        D0y = 0
        D0z = rad * numpy.sin(angp) * numpy.sin(2 * bragg)

        R0x = rad * numpy.sin(angm) / 2
        R0y = 0
        R0z = rad * numpy.cos(angm) / 2

        A0 = numpy.array([A0x, A0y, A0z])
        D0 = numpy.array([D0x, D0y, D0z])
        R0 = numpy.array([R0x, R0y, R0z])

        X0 = numpy.array([1, 0, 0])
        Y0 = numpy.array([0, 1, 0])

        D0norm = getnorm(D0)
        D0z = D0[2]
        D0x = D0[0]
        D0x2 = D0[0] ** 2

        # N0a = D0 - 2 * A0
        # N0a = N0a / getnorm(N0a)
        N0a = numpy.dot(
            rotation_matrix(Y0, 90 - numpy.rad2deg(bragg) + self.miscut), X0
        )

        N0d = R0 - D0
        N0d = N0d / getnorm(N0d)

        return [A0, D0, R0, X0, Y0, N0a, N0d, D0x, D0z, D0norm, D0x2]

    def compute_bragg_solution(self, bragg):
        """returns a tuple (bragg, bragg_solution, reals_positions):
        - bragg: the bragg angle associated to this solution.
        - bragg_solution: a dict with relevant data for the position and orientation of this positioner.
          Data expressed in the laboratory referential with an origin at (0,0,0) (i.e. ignoring self.referential_origin).
        - reals_positions: the theoritical positions of the real axes for the given bragg value.
          Reals positions expressed in laboratory referential (must take into account self.referential_origin!=(0,0,0))
        """

        reals_pos = {}

        beta = numpy.deg2rad(self.angular_offset)

        (
            A0,
            D0,
            R0,
            X0,
            Y0,
            N0a,
            N0d,
            D0x,
            D0z,
            D0norm,
            D0x2,
        ) = self._compute_central_analyser_solution(bragg)

        psi = numpy.arcsin(-self.offset_on_detector / D0z)

        Di = numpy.dot(rotation_matrix(X0, numpy.rad2deg(psi)), D0)

        # use a.cosx + b.sinx = c =>   sinx(x+p) = c/sqrt(a**2+b**2) and sin(p) = a/sqrt(a**2+b**2)
        a = D0z * (D0z * numpy.tan(beta) - D0x * numpy.sin(psi))
        b = -D0z * D0norm * numpy.cos(psi)
        c = -D0x2 * numpy.tan(beta) - D0x * D0z * numpy.sin(psi)

        nab = numpy.sqrt(a * a + b * b)
        d = numpy.arcsin(c / nab)
        e = numpy.arcsin(a / nab)
        eta = e - d

        Ai = numpy.dot(rotation_matrix(Di, numpy.rad2deg(eta)), A0)

        Ri = numpy.dot(rotation_matrix(X0, numpy.rad2deg(psi)), R0)
        Ri = numpy.dot(rotation_matrix(Di, numpy.rad2deg(eta)), Ri)

        Nia = numpy.dot(rotation_matrix(X0, numpy.rad2deg(psi)), N0a)
        Nia = -numpy.dot(rotation_matrix(Di, numpy.rad2deg(eta)), Nia)

        Nid = numpy.dot(rotation_matrix(X0, numpy.rad2deg(psi)), N0d)
        Nid = numpy.dot(rotation_matrix(Di, numpy.rad2deg(eta)), Nid)

        Nia = normalize(Nia)
        Nid = normalize(Nid)

        d = self._lab_to_positioner_matrix(Ai) @ Nia
        pitch, yaw = direction_to_angles(d)

        # === !!! real motors position expressed in lab ref !!!
        lab_pos = Ai + self.referential_origin
        lab_rpos = numpy.sqrt(lab_pos[0] ** 2 + lab_pos[1] ** 2)

        reals_pos = {
            "rpos": lab_rpos,
            "zpos": lab_pos[2],
            "pitch": pitch,
            "yaw": yaw,
        }

        bragg_solution = {
            "Ai": Ai,
            "Di": Di,
            "Ri": Ri,
            "Nia": Nia,
            "Nid": Nid,
            "D0": D0,
            "N0d": N0d,
            "pitch": pitch,
            "yaw": yaw,
        }

        return (bragg, bragg_solution, reals_pos)


class CartesianAnalyser(Analyser):
    def _get_pos(self, tag):
        """return one of the position coordinates [xpos, ypos, zpos] or
        orientation angles (pitch, yaw) expressed in the laboratory referential
        (i.e taking into account a possible spectrometer origin != [0,0,0]).
        The returned value must be computed from the actual real axes position to reflect
        actual positioner situation.
        args: tag is one of ["xpos", "ypos", "zpos", pitch, "yaw"]
        """
        # === !!! motor position is expressed in lab ref (so it already includes referential_origin offset) !!!
        ypos = self.config["ypos"] + self.referential_origin[1]
        if tag == "rpos":
            x = self._get_real_axis_pos("xpos")
            y = ypos
            return numpy.sqrt(x**2 + y**2)
        elif tag == "ypos":
            return ypos
        elif tag in ["xpos", "zpos", "pitch", "yaw"]:
            return self._get_real_axis_pos(tag)
        raise RuntimeError(f"unknown tag {tag}")

    def _find_beta_from_ypos(
        self, bragg, ypos, central_solution, xstart=0, xstep=10, tol=1e-6
    ):
        # ===== !!! ypos in spectro referential !!! ===============

        t0 = time.perf_counter()

        if ypos < 0:
            direction = -1
        else:
            direction = 1

        beta = xstart
        while True:

            if time.perf_counter() - t0 > 2:
                print("cant find solution in time")
                return None

            _, solution, _ = self._compute_analyser_solution(
                bragg, beta, central_solution
            )
            fypos = solution["Ai"][1]  # !!! in spectro ref !!!

            if numpy.isnan(fypos):
                return None

            dy = ypos - fypos
            if abs(dy) < tol:
                return beta

            elif dy * direction > 0:
                beta += xstep * direction
                continue
            else:
                beta -= xstep * direction
                xstep = xstep / 10

    def _compute_central_analyser_solution(self, bragg):

        bragg = numpy.deg2rad(bragg)
        miscut = numpy.deg2rad(self.miscut)
        angm = bragg - miscut
        angp = bragg + miscut

        rad = 2 * self.radius  # Rowland circle

        A0x = rad * numpy.sin(angm)
        A0y = 0
        A0z = 0

        D0x = rad * numpy.cos(angp) * numpy.sin(2 * bragg)
        D0y = 0
        D0z = rad * numpy.sin(angp) * numpy.sin(2 * bragg)

        R0x = rad * numpy.sin(angm) / 2
        R0y = 0
        R0z = rad * numpy.cos(angm) / 2

        A0 = numpy.array([A0x, A0y, A0z])
        D0 = numpy.array([D0x, D0y, D0z])
        R0 = numpy.array([R0x, R0y, R0z])

        X0 = numpy.array([1, 0, 0])
        Y0 = numpy.array([0, 1, 0])

        D0norm = getnorm(D0)
        D0z = D0[2]
        D0x = D0[0]
        D0x2 = D0[0] ** 2

        # N0a = D0 - 2 * A0
        # N0a = N0a / getnorm(N0a)
        N0a = numpy.dot(
            rotation_matrix(Y0, 90 - numpy.rad2deg(bragg) + self.miscut), X0
        )

        N0d = R0 - D0
        N0d = N0d / getnorm(N0d)

        return [A0, D0, R0, X0, Y0, N0a, N0d, D0x, D0z, D0norm, D0x2]

    def _compute_analyser_solution(self, bragg, beta, central_solution):
        beta = numpy.deg2rad(beta)
        reals_pos = {}
        (
            A0,
            D0,
            R0,
            X0,
            Y0,
            N0a,
            N0d,
            D0x,
            D0z,
            D0norm,
            D0x2,
        ) = central_solution

        psi = numpy.arcsin(-self.offset_on_detector / D0z)

        Di = numpy.dot(rotation_matrix(X0, numpy.rad2deg(psi)), D0)

        # use a.cosx + b.sinx = c =>   sinx(x+p) = c/sqrt(a**2+b**2) and sin(p) = a/sqrt(a**2+b**2)
        a = D0z * (D0z * numpy.tan(beta) - D0x * numpy.sin(psi))
        b = -D0z * D0norm * numpy.cos(psi)
        c = -D0x2 * numpy.tan(beta) - D0x * D0z * numpy.sin(psi)

        nab = numpy.sqrt(a * a + b * b)
        d = numpy.arcsin(c / nab)
        e = numpy.arcsin(a / nab)
        eta = e - d

        Ai = numpy.dot(rotation_matrix(Di, numpy.rad2deg(eta)), A0)

        Ri = numpy.dot(rotation_matrix(X0, numpy.rad2deg(psi)), R0)
        Ri = numpy.dot(rotation_matrix(Di, numpy.rad2deg(eta)), Ri)

        Nia = numpy.dot(rotation_matrix(X0, numpy.rad2deg(psi)), N0a)
        Nia = -numpy.dot(rotation_matrix(Di, numpy.rad2deg(eta)), Nia)

        Nid = numpy.dot(rotation_matrix(X0, numpy.rad2deg(psi)), N0d)
        Nid = numpy.dot(rotation_matrix(Di, numpy.rad2deg(eta)), Nid)

        Nia = normalize(Nia)
        Nid = normalize(Nid)

        d = self._lab_to_positioner_matrix(Ai) @ Nia
        pitch, yaw = direction_to_angles(d)

        # === !!! real motors position expressed in lab ref !!!
        lab_pos = Ai + self.referential_origin

        reals_pos = {
            "xpos": lab_pos[0],
            "zpos": lab_pos[2],
            "pitch": pitch,
            "yaw": yaw,
        }

        return (
            bragg,
            {
                "Ai": Ai,
                "Di": Di,
                "Ri": Ri,
                "Nia": Nia,
                "Nid": Nid,
                "D0": D0,
                "N0d": N0d,
                "pitch": pitch,
                "yaw": yaw,
            },
            reals_pos,
        )

    def compute_bragg_solution(self, bragg):
        """returns a tuple (bragg, bragg_solution, reals_positions):
        - bragg: the bragg angle associated to this solution.
        - bragg_solution: a dict with relevant data for the position and orientation of this positioner.
          Data expressed in the laboratory referential with an origin at (0,0,0) (i.e. ignoring self.referential_origin).
        - reals_positions: the theoritical positions of the real axes for the given bragg value.
          Reals positions expressed in laboratory referential (must take into account self.referential_origin!=(0,0,0))
        """
        central_solution = self._compute_central_analyser_solution(bragg)
        ypos = self.ypos - self.referential_origin[1]  # needs ypos in spectro ref !!!
        beta = self._find_beta_from_ypos(bragg, ypos, central_solution)
        if beta is None:
            raise RuntimeError(
                f"cannot find a solution for analyser {self.name} with ypos {ypos}"
            )

        return self._compute_analyser_solution(bragg, beta, central_solution)
