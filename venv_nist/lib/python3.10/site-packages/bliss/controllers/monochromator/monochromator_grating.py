# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

"""
???
"""

import numpy
import tabulate
import xcalibu

from bliss.shell.standard import umv
from bliss.physics.diffraction import hc
from bliss.physics.units import ur
from bliss.config.settings import SimpleSetting
from bliss.controllers.monochromator.monochromator import MonochromatorBase
from bliss.controllers.monochromator.tracker import EnergyTrackingObject
from bliss.controllers.monochromator.utils import EmptyObject
from bliss.common.utils import BOLD, BLUE, ORANGE


class MonochromatorGrating(MonochromatorBase):
    def __init__(self, config):
        super().__init__(config)

    def _load_config(self):
        super()._load_config()

        gratings_conf = self.config.get("gratings")
        if gratings_conf is None:
            raise RuntimeError("MonochromatorGrating: No gratings specified")
        self._load_gratings_config(gratings_conf)

        polarization_conf = self.config.get("polarizations")
        if polarization_conf is None:
            raise RuntimeError("MonochromatorGrating: No polarizations specified")
        self._load_polarizations_config(polarization_conf)

    def _load_gratings_config(self, gratings_conf):
        self.gratings = EmptyObject()
        self._gratings = {}

        for grating_conf in gratings_conf:
            grating_name = grating_conf.get("grating_name")
            default_grating = grating_name
            mode = grating_conf.get("mode")
            if mode == "RIXS":
                grating = GratingRIXS(self, grating_name, mode)
            elif mode == "XMCD":
                grating = GratingXMCD(self, grating_name, mode)
            else:
                raise RuntimeError(
                    f'MonochromatorGrating: Grating "{grating_name}" wrong mode'
                )
            grating._load_reference(grating_conf.get("reference", None))
            self._gratings[grating_name] = grating
            setattr(self.gratings, grating_name, grating)

        self._grating_sel = None
        self._grating_sel_name = SimpleSetting(
            f"MonochromatorGrating_{self.name}_grating_sel",
            default_value=default_grating,
        )
        self._grating_sel = self._gratings[self._grating_sel_name.get()]

    def _load_polarizations_config(self, polarizations_conf):
        self.polarizations = EmptyObject()
        self._polarizations = {}

        for polarization_conf in polarizations_conf:
            polarization_name = polarization_conf.get("polarization_name")
            default_polarization = polarization_name
            polarization = Polarization(self, polarization_name, polarization_conf)
            self._polarizations[polarization_name] = polarization
            setattr(self.polarizations, polarization_name, polarization)

        self._polarization_sel = None
        self._polarization_sel_name = SimpleSetting(
            f"MonochromatorGrating_{self.name}_polarization_sel",
            default_value=default_polarization,
        )
        self._polarization_sel = self._polarizations[self._polarization_sel_name.get()]
        for pol_name, pol in self._polarizations.items():
            pol._create_calibration()
        self._polarization_sel._update_selected_undulator()

    #
    # User Info

    def __info__(self):
        print(f"\n{self._get_info_mono()}")
        print(f"\n{self._get_info_grating()}")
        print(f"\n{self._get_info_polarization()}")
        print(BOLD("    Motors:"))
        print(f"{self._get_info_motor_energy()}")
        print(f"\n{self._get_info_motor_tracker()}")
        return ""

    def _get_info_mono(self):
        """Get the monochromator information."""
        mono = BOLD("Monochromator")
        return f"    {mono}: {self._name}"

    def _get_info_grating(self):
        mystr = BOLD("    Selected Grating:\n")
        mystr += self._grating_sel._get_info()
        return mystr

    def _get_info_polarization(self):
        mystr = BOLD("    Selected Polarization: ")
        mystr += f"{BLUE(self._polarization_sel._name)}\n"
        mystr += self._polarization_sel._get_info_selected()
        mystr += "\n"
        return mystr

    def _get_info_motor_energy(self):
        # TITLE
        title = [
            "    ",
            "",
            BLUE(self._motors["energy"].name),
            ORANGE(self._motors["nu"].name),
            ORANGE(self._motors["psi"].name),
        ]

        # CALCULATED POSITION ROW
        nu_pos = self._motors["nu"].position
        nu_unit = self._motors["nu"].unit
        psi_pos = self._motors["psi"].position
        psi_unit = self._motors["psi"].unit
        energy_pos = self.angles2energy(nu_pos, psi_pos)
        energy_unit = self._motors["energy"].unit
        calculated = [
            "    ",
            "Calculated",
            f"{energy_pos:.3f} {energy_unit}",
            f"{nu_pos:.3f} {nu_unit}",
            f"{psi_pos:.3f} {psi_unit}",
        ]

        #
        # CURRENT POSITION ROW
        #
        energy_pos = self._motors["energy"].position
        current = [
            "    ",
            "   Current",
            f"{energy_pos:.3f} {energy_unit}",
            f"{nu_pos:.3f} {nu_unit}",
            f"{psi_pos:.3f} {psi_unit}",
        ]
        info_str = tabulate.tabulate(
            [calculated, current], headers=title, tablefmt="plain", stralign="right"
        )
        return info_str

    def _get_info_motor_tracker(self):
        info_str = ""
        if self._has_tracking:
            controller = self._motors["energy_tracker"].controller
            # TITLE
            title = ["    ", ""]
            for axis in controller.pseudos:
                title.append(BLUE(axis.name))
            for axis in controller.reals:
                title.append(ORANGE(axis.name))
            # CALCULATED POSITION ROW
            nu_pos = self._motors["nu"].position
            psi_pos = self._motors["psi"].position
            energy_pos = self.angles2energy(nu_pos, psi_pos)
            energy_unit = self._motors["energy"].unit
            calculated = ["    ", "Calculated"]
            calculated.append(f"{energy_pos:.3f} {energy_unit}")
            for axis in controller.reals:
                if controller._axis_tag(axis) == "energy":
                    calculated.append(f"{energy_pos:.3f} {energy_unit}")
                else:
                    calculated.append(
                        f"{axis.tracking.energy2tracker(energy_pos):.3f} {axis.unit}"
                    )
            # CURRENT POSITION ROW
            current = ["    ", "   Current"]
            current.append(
                f"{controller.pseudos[0].position:.3f} {controller.pseudos[0].unit}"
            )
            for axis in controller.reals:
                current.append(f"{axis.position:.3f} {axis.unit}")
            # TRACKING STATE ROW
            tracking = ["    ", "Tracking", "", ""]
            for axis in controller.reals:
                if controller._axis_tag(axis) != "energy":
                    if axis.tracking.state:
                        tracking.append("ON")
                    else:
                        tracking.append("OFF")

            # SCANNING MODE ROW
            mode = ["    ", "Scanning Mode", "", ""]
            for axis in controller.reals:
                if controller._axis_tag(axis) != "energy":
                    mode.append(axis.tracking.scanning_mode)

            info_str = tabulate.tabulate(
                [calculated, current, tracking, mode],
                headers=title,
                tablefmt="plain",
                stralign="right",
            )
        return info_str

    #
    # Energy related methods

    def energy2angles(self, energy):
        """
        - mirror offset unit mrad
        - epsilon unit mrad
        - in mode XMCD, nu does not move
        - energy is given in the unit of the Energy/Energy tracker motors
        - angles are returned in the unit of the nu/psi motors unit
        """

        if self._grating_sel is None:
            raise RuntimeError("No grating selected")

        energy_unit = energy * ur.Unit(self._motors["energy"].unit)
        energy_J = energy_unit.to("J").magnitude
        lamb = hc.magnitude / energy_J

        mirror_offset = self._grating_sel.mirror_offset / 1000000.0
        epsilon = self._grating_sel.epsilon / 1000000.0
        dspacing = self._grating_sel.dspacing * 1e-10

        eta = 0.5 * (epsilon + mirror_offset)

        if self._grating_sel.mode == "XMCD":
            nupos = (
                (self._motors["nu"].position * ur.Unit(self._motors["nu"].unit))
                .to("rad")
                .magnitude
            )
            nuval = nupos + mirror_offset
            nucorr = nuval - eta
        else:
            auxA = -lamb / dspacing
            auxB = numpy.square(self._grating_sel.cff * lamb / dspacing)
            auxC = numpy.square(self._grating_sel.cff) - 1

            alpha = numpy.arcsin(
                auxA / auxC + numpy.sqrt(1 + auxB / numpy.square(auxC))
            )
            beta = -numpy.arccos(self._grating_sel.cff * numpy.cos(alpha))
            nucorr = numpy.pi / 2.0 - 0.5 * (alpha - beta)
            nuval = nucorr + eta
        psival = numpy.arcsin(lamb / (2 * dspacing * numpy.sin(nucorr))) + nucorr

        nu_calc = nuval - mirror_offset
        psi_calc = psival + epsilon

        nu_calc = (nu_calc * ur.Unit("rad")).to(self._motors["nu"].unit).magnitude
        psi_calc = (psi_calc * ur.Unit("rad")).to(self._motors["psi"].unit).magnitude

        return (nu_calc, psi_calc)

    def angles2energy(self, nu, psi):
        """
        - mirror offset unit mrad
        - epsilon unit mrad
        - in mode XMCD, nu does not move
        - energy is returned in the unit of the Energy/Energy_tracker motors
        - angles are given in the unit of the nu/psi motors unit
        """

        if self._grating_sel is None:
            raise RuntimeError("No grating selected")

        nu_pos = (nu * ur.Unit(self._motors["nu"].unit)).to("rad").magnitude
        psi_pos = (psi * ur.Unit(self._motors["psi"].unit)).to("rad").magnitude
        mirror_offset = self._grating_sel.mirror_offset / 1000000.0
        epsilon = self._grating_sel.epsilon / 1000000.0
        dspacing = self._grating_sel.dspacing * 1e-10

        eta = 0.5 * (epsilon + mirror_offset)
        nuval = nu_pos + mirror_offset
        psival = psi_pos - epsilon
        nucorr = nuval - eta

        lamb = 2.0 * dspacing * numpy.sin(nucorr) * numpy.sin(psival - nucorr)

        return (
            ((hc.magnitude / lamb) * ur.Unit("J"))
            .to(self._motors["energy"].unit)
            .magnitude
        )

    def _cff(self, nu, psi):

        nu_pos = (nu * ur.Unit(self._motors["nu"].unit)).to("rad").magnitude
        psi_pos = (psi * ur.Unit(self._motors["psi"].unit)).to("rad").magnitude
        mirror_offset = self._grating_sel.mirror_offset / 1000000.0
        epsilon = self._grating_sel.epsilon / 1000000.0

        nuval = nu_pos + mirror_offset
        psival = psi_pos - epsilon

        eta = 0.5 * (epsilon + mirror_offset)
        gamma = 0.5 * numpy.pi - nuval

        theta = gamma + eta
        phi = psival - nuval + eta

        alpha = phi + theta
        beta = phi - theta

        cff = numpy.cos(beta) / numpy.cos(alpha)

        return cff

    def _get_mono_real_axis_trajectory(self, ene_data):
        (nu_data, psi_data) = self.energy2angles(ene_data)
        if self._grating_sel.mode == "RIXS":
            mono_dict = {
                self._motors["nu"].name: nu_data,
                self._motors["psi"].name: psi_data,
            }
        elif self._grating_sel.mode == "XMCD":
            mono_dict = {self._motors["psi"].name: psi_data}
        else:
            raise RuntimeError(f"Wrong Grating Mode: {self._grating_sel.mode}")

        return mono_dict

    def _get_current_virtual_energy(self):
        nu_pos = self._motors["nu"].position
        psi_pos = self._motors["psi"].position
        energy_pos = self.angles2energy(nu_pos, psi_pos)
        return energy_pos

    def _move_on_trajectory(self, position):
        if self._cst_speed_mode == "ENERGY":
            start_pos = position
        elif self.cst_speed_mode._value == "UNDULATOR":
            start_pos = self._undulator_master.tracking.energy2tracker(position)
        else:
            raise RuntimeError("Escan: Bad cst_speed_mode")

        # set virtual energy motor to start position to avoid a long and useless movement
        self._motors["virtual_energy"].dial = start_pos
        self._motors["virtual_energy"].offset = 0

        # Move trajectory motor on the trajectory
        if self._has_tracking:
            umv(self._motors["energy_tracker"], start_pos)
        else:
            umv(self._motors["energy"], start_pos)
        umv(self._motors["trajectory"], start_pos)


class GratingXMCD:
    def __init__(self, mono, name, mode):
        self._mono = mono
        self._name = name
        self._mode = mode

        self._ref_names = ["lines", "epsilon", "mirror_offset"]
        self.reference = EmptyObject()

        self._epsilon = SimpleSetting(
            f"{self.__class__}_{self._name}_epsilon", default_value=1.0
        )
        self._lines = SimpleSetting(
            f"{self.__class__}_{self._name}_lines", default_value=100
        )
        self._mirror_offset = SimpleSetting(
            f"{self.__class__}_{self._name}_mirror_offset", default_value=165
        )

    def __info__(self):
        print(self._get_info())
        return ""

    def _get_info(self):
        title = ["    ", "Name", "Mode", "Mirror Offset", "Lines", "Epsilon"]
        value = [
            "    ",
            BLUE(self._name),
            self._mode,
            self.mirror_offset,
            self.lines,
            self.epsilon,
        ]
        info_str = tabulate.tabulate(
            [
                value,
            ],
            headers=title,
            tablefmt="plain",
            stralign="right",
        )
        return info_str

    def _load_reference(self, ref_conf):
        if ref_conf is not None:
            for name in self._ref_names:
                ref_value = ref_conf[0].get(name, None)
                if ref_value == "none":
                    ref_value = None
                setattr(self.reference, name, ref_value)

    def select(self):
        self._mono._grating_sel = self
        self._mono._grating_sel_name.set(self._name)

    @property
    def mode(self):
        return self._mode

    @property
    def dspacing(self):
        return 1e10 / (1000.0 * self.lines)

    @property
    def epsilon(self):
        return self._epsilon.get()

    @epsilon.setter
    def epsilon(self, value):
        self._epsilon.set(value)

    @property
    def lines(self):
        return self._lines.get()

    @lines.setter
    def lines(self, value):
        self._lines.set(value)

    @property
    def mirror_offset(self):
        return self._mirror_offset.get()

    @mirror_offset.setter
    def mirror_offset(self, value):
        self._mirror_offset.set(value)


class GratingRIXS(GratingXMCD):
    def __init__(self, mono, name, mode):
        super().__init__(mono, name, mode)
        self._ref_names = ["lines", "epsilon", "mirror_offset", "cff"]
        self.reference = lambda: None
        self._cff = SimpleSetting(
            f"{self.__class__}_{self._name}_cff", default_value=1.0
        )

    def _get_info(self):
        title = ["    ", "Name", "Mode", "Mirror Offset", "Lines", "Epsilon", "cff"]
        value = [
            "    ",
            BLUE(self._name),
            self._mode,
            self.mirror_offset,
            self.lines,
            self.epsilon,
            self.cff,
        ]
        info_str = tabulate.tabulate(
            [
                value,
            ],
            headers=title,
            tablefmt="plain",
            stralign="right",
        )
        return info_str

    @property
    def cff(self):
        return self._cff.get()

    @cff.setter
    def cff(self, value):
        self._cff.set(value)


class Polarization:
    def __init__(self, mono, name, config):
        self._mono = mono
        self._name = name

        self._undulators = {}
        undus_conf = config.get("undulators", None)
        if undus_conf is None:
            raise RuntimeError(f'Polarization: No undulators configured for "{name}"')
        for undu_conf in undus_conf:
            undu_name = undu_conf.get("undulator_name")
            self._undulators[undu_name] = PolarizationUndulator(
                self, undu_name, undu_conf
            )
            setattr(self, undu_name, self._undulators[undu_name])
        self._undulator_list_sel_str = SimpleSetting(
            f"Polarization_{self._name}_selected_undulator_list",
            default_value=undu_name,
        )

    def __info__(self):
        print(self._get_info())
        return ""

    def _get_info(self):
        print("\n")
        selected = self._mono._polarization_sel == self
        title = ["    ", "Polarization", "Selected"]
        value = ["    ", BLUE(self._name), selected]
        info_str = tabulate.tabulate(
            [
                value,
            ],
            headers=title,
            tablefmt="plain",
            stralign="right",
        )
        info_str += "\n\n"

        undu_values = []
        for undu_name, undu in self._undulators.items():
            title = undu._get_info_undulator_title()
            undu_values.append(undu._get_info_undulator_values())
        info_str += tabulate.tabulate(
            undu_values,
            headers=title,
            tablefmt="plain",
            stralign="right",
        )

        return info_str

    def _get_info_selected(self):
        info_str = ""
        undu_values = []
        for undu in self._undulator_list_sel:
            title = undu._get_info_undulator_title()
            undu_values.append(undu._get_info_undulator_values())
            info_str += tabulate.tabulate(
                undu_values,
                headers=title,
                tablefmt="plain",
                stralign="right",
            )
        return info_str

    def _create_calibration(self):
        for undu_name, undu in self._undulators.items():
            self._mono.tracking._create_calibration(undu._motmode["gap"]["motor"], self)

    def _update_selected_undulator(self):
        sel_undu_list = self._undulator_list_sel_str.get().split()
        self._undulator_list_sel = []
        for undu_name, undu in self._undulators.items():
            if undu_name in sel_undu_list:
                self._undulator_list_sel.append(undu)
                undu._set_tracking_mode()
                undu._set_tracking_on()
            else:
                undu._set_tracking_off()

    def _select(self, undulator):
        self._mono._polarization_sel = self
        self._mono._polarization_sel_name.set(self._name)
        for undu_name, undu in self._undulators.items():
            undu._set_tracking_off()
        self._undulator_list_sel = [undulator]
        self._undulator_list_sel_str.set(f"{undulator._name}")
        undulator._set_tracking_mode()
        undulator._set_tracking_on()

    def select_all(self):
        self._mono._polarization_sel = self
        self._mono._polarization_sel_name.set(self._name)
        self._undulator_list_sel = []
        undulator_list_sel_str = ""
        for undu_name, undu in self._undulators.items():
            self._undulator_list_sel.append(undu)
            undulator_list_sel_str += f"{undu_name} "
            undu._set_tracking_mode()
            undu._set_tracking_on()
        if undulator_list_sel_str[-1] == " ":
            undulator_list_sel_str = undulator_list_sel_str[:-1]
        self._undulator_list_sel_str.set(undulator_list_sel_str)


class PolarizationUndulator:
    def __init__(self, polarization, name, conf):
        self._name = name
        self._polarization = polarization

        self._motmode = {}

        for name in ["gap", "phase", "negoff", "posoff"]:
            mot = conf.get(f"{name}_mot")
            mode = conf.get(f"{name}_mode")
            self._motmode[name] = {"motor": mot, "mode": mode}

        self._angle = SimpleSetting(
            f"Polarization_{self._polarization._name}_{self._name}_angle",
            default_value=0.0,
        )

    def __info__(self):
        print("\n")
        print(self._get_info())
        print("\n")
        return ""

    def _get_info(self):
        title = self._get_info_undulator_title()
        value = self._get_info_undulator_values()
        info_str = tabulate.tabulate(
            [
                value,
            ],
            headers=title,
            tablefmt="plain",
            stralign="right",
        )
        return info_str

    def _get_info_undulator_title(self):
        title = [
            "    ",
            "Undulator",
            "Vert. Phase",
            "Hor. Phase",
            "Phase Shift",
            "Neg. Offset",
            "Neg. Offset Shift",
            "Pos. Offset",
            "Pos. Offset Shift",
        ]
        if self._polarization._name == "linear":
            title.append("Angle")
        return title

    def _get_info_undulator_values(self):
        ret = self._get_values()
        value = [
            "    ",
            BLUE(self._name),
            ret["phvert"],
            ret["phhor"],
            ret["phshift"],
            ret["negoff"],
            ret["negoffshift"],
            ret["posoff"],
            ret["posoffshift"],
        ]
        if self._polarization._name == "linear":
            value.append(self.angle)
        return value

    def select(self):
        self._polarization._select(self)

    def _set_tracking_mode(self):
        for name, obj in self._motmode.items():
            mot = obj["motor"]
            mode = obj["mode"]
            if mode == "MIXED_NEG" or mode == "MIXED_POS":
                if mode == "MIXED_NEG":
                    if self.angle < 0:
                        mot.tracking.scanning_mode = "MOVING"
                    else:
                        mot.tracking.scanning_mode = "FIXED"
                else:
                    if self.angle > 0:
                        mot.tracking.scanning_mode = "MOVING"
                    else:
                        mot.tracking.scanning_mode = "FIXED"
            else:
                mot.tracking.scanning_mode = mode

    def _set_tracking_on(self):
        for name, obj in self._motmode.items():
            obj["motor"].tracking.on()

    def _set_tracking_off(self):
        for name, obj in self._motmode.items():
            obj["motor"].tracking.off()

    def _get_values(self):
        ret = {}

        motor = self._motmode["gap"]["motor"]
        params = motor.tracking._controller._parameters[motor.name]["constant"]

        ret["phvert"] = params["phase_vertical"]
        ret["phhor"] = params["phase_horizontal"]
        ret["phshift"] = params["phase_shift"]
        ret["negoff"] = params["offset_neg"]
        ret["negoffshift"] = params["offset_neg_shift"]
        ret["posoff"] = params["offset_pos"]
        ret["posoffshift"] = params["offset_pos_shift"]

        return ret

    @property
    def angle(self):
        return self._angle.get()

    @angle.setter
    def angle(self, angle):
        if self._polarization._mono._polarization_sel._name == "linear":
            self._angle.set(angle)
            self._set_tracking_mode()
            self._polarization._mono.tracking._create_calibration(
                self._motmode["gap"]["motor"], self._polarization
            )
        else:
            return


class GratingEnergyTrackingObject(EnergyTrackingObject):
    def __init__(self, config):

        # source parameter
        self._gamma = 11742.0

        super().__init__(config)

    def _load_config(self):
        super()._load_config()

        for axis_name in self._parameters.keys():
            if "constant" in self._parameters[axis_name].keys():
                self._parameters[axis_name]["constant"]["CST"] = (
                    self._parameters[axis_name]["constant"]["Uperiod"]
                    / 2.0
                    / numpy.square(self._gamma)
                    / 1e-7
                )
                self._parameters[axis_name]["constant"]["calib"] = {}

    """
    Calibration Table Methods
    """

    def _create_calibration(self, gap_mot, polarization):
        gap_from = float(self._parameters[gap_mot.name]["constant"]["gap_from"])
        gap_to = float(self._parameters[gap_mot.name]["constant"]["gap_to"])
        resolution = float(self._parameters[gap_mot.name]["constant"]["gap_resolution"])
        if gap_from < gap_to:
            start = gap_from
            stop = gap_to
        else:
            start = gap_to
            stop = gap_from

        gap_values = numpy.arange(start, stop + resolution, resolution)

        pol_name = polarization._name
        self._parameters[gap_mot.name]["constant"]["calib"][pol_name] = {}

        phase_values = self._get_phase_from_polarization(
            gap_mot, polarization, gap_values
        )
        energy_values = self._gap_to_energy(gap_mot, gap_values, phase_values, pol_name)

        calib_name = f"{gap_mot.name}_{pol_name}_ene2gap"
        calib = self._calibration_fill(calib_name, energy_values, gap_values)
        self._parameters[gap_mot.name]["constant"]["calib"][pol_name]["ene2gap"] = calib

    def _calibration_fill(self, name, value_x, value_y):
        calib = xcalibu.Xcalibu()
        calib.set_calib_name(name)
        calib.set_calib_time(0)
        calib.set_calib_type("TABLE")
        calib.set_reconstruction_method("INTERPOLATION", "linear")
        calib.set_raw_x(value_x)
        calib.set_raw_y(value_y)
        calib.check_monotonic()
        calib.compute_interpolation()
        return calib

    def _get_phase_from_polarization(self, gap_mot, polarization, gap_values):
        if polarization._name == "linear":
            angle = polarization._undulators[gap_mot.name].angle
            phase_values = (
                numpy.fabs(self._gap_to_offset(gap_mot, gap_values, angle)) / 2.0
            )
        elif (
            polarization._name == "circular_minus"
            or polarization._name == "circular_plus"
        ):
            phase_values = self._gap_to_phase(gap_mot, gap_values)
        else:
            phase_values = numpy.copy(gap_values)
            if polarization._name == "vertical":
                phase_values[:] = self._parameters[gap_mot.name]["constant"][
                    "phase_vertical"
                ]
            else:
                phase_values[:] = self._parameters[gap_mot.name]["constant"][
                    "phase_horizontal"
                ]
            phase_values[:] = (
                phase_values[:]
                + self._parameters[gap_mot.name]["constant"]["phase_shift"]
            )
        return phase_values

    def _get_calib(self, gap_mot):
        pol_name = self._mono._polarization_sel._name
        return self._parameters[gap_mot.name]["constant"]["calib"][pol_name]["ene2gap"]

    """
    YML Conversion Method
    """

    def _ene2gap(self, energy, config):
        gap_mot = config["axis"]
        energy_arr = numpy.atleast_1d(energy)
        gap = self._get_calib(gap_mot).get_y(energy_arr)
        if gap.shape[0] == 1:
            return gap[0]
        return gap

    def _gap2ene(self, gap, config):
        gap_mot = config["axis"]
        phase_values = self._get_phase_from_polarization(
            gap_mot, self._mono._polarization_sel, gap
        )
        pol_name = self._mono._polarization_sel._name
        return self._gap_to_energy(gap_mot, gap, phase_values, pol_name)

    def _ene2phase(self, energy, config):
        gap_mot = config["gap_motor"]
        pol_name = self._mono._polarization_sel._name

        energy_arr = numpy.atleast_1d(energy)
        gap = self._get_calib(gap_mot).get_y(energy_arr)

        if pol_name in ["vertical", "horizontal", "linear"]:
            phase_val = numpy.copy(energy_arr)
            if pol_name == "vertical":
                fix_phase = self._parameters[gap_mot.name]["constant"]["phase_vertical"]
            else:
                fix_phase = self._parameters[gap_mot.name]["constant"][
                    "phase_horizontal"
                ]
            phase_val[:] = fix_phase
        else:
            phase_val = self._gap_to_phase(gap_mot, gap)
            if pol_name == "circular_minus":
                phase_val = -phase_val

        phase_val = (
            phase_val + self._parameters[gap_mot.name]["constant"]["phase_shift"]
        )

        if phase_val.shape[0] == 1:
            return phase_val[0]
        return phase_val

    def _phase2ene(self, phase, config):
        return numpy.nan

    def _ene2negoffset(self, energy, config):
        gap_mot = config["gap_motor"]
        pol_name = self._mono._polarization_sel._name
        angle = self._mono._polarizations[pol_name]._undulators[gap_mot.name].angle

        energy_arr = numpy.atleast_1d(energy)
        gap = self._get_calib(gap_mot).get_y(energy_arr)

        if pol_name != "linear" or (pol_name == "linear" and angle > 0):
            offset_val = numpy.copy(energy_arr)
            offset_val[:] = self._parameters[gap_mot.name]["constant"]["offset_neg"]
        else:
            offset_val = self._gap_to_offset(gap_mot, gap, angle)

        offset_val = (
            offset_val + self._parameters[gap_mot.name]["constant"]["offset_neg_shift"]
        )

        if offset_val.shape[0] == 1:
            return offset_val[0]
        return offset_val

    def _ene2posoffset(self, energy, config):
        gap_mot = config["gap_motor"]
        pol_name = self._mono._polarization_sel._name
        angle = self._mono._polarizations[pol_name]._undulators[gap_mot.name].angle

        energy_arr = numpy.atleast_1d(energy)
        gap = self._get_calib(gap_mot).get_y(energy_arr)

        if pol_name != "linear" or (pol_name == "linear" and angle < 0):
            offset_val = numpy.copy(energy_arr)
            fix_offset = self._parameters[gap_mot.name]["constant"]["offset_pos"]
            offset_val[:] = fix_offset
        else:
            offset_val = self._gap_to_offset(gap_mot, gap, angle)

        offset_val = (
            offset_val + self._parameters[gap_mot.name]["constant"]["offset_neg_shift"]
        )

        if offset_val.shape[0] == 1:
            return offset_val[0]
        return offset_val

    def _offset2ene(self, offset, config):
        return numpy.nan

    """
    Calculation mothods
    """

    def _get_Bval(self, gap_mot, gap_val):
        params = self._parameters[gap_mot.name]["constant"]
        self._Uperiod = float(params["Uperiod"])
        self._B1z = float(params["B1z"])
        self._B1x = float(params["B1x"])
        self._B2x = float(params["B2x"])
        self._k1z = float(params["k1z"])
        self._k2z = float(params["k2z"])
        self._k1x = float(params["k1x"])
        self._k2x = float(params["k2x"])
        self._CST = float(params["CST"])

        self._Bpeak_z = self._B1z * numpy.exp(
            -self._k1z * gap_val - self._k2z * numpy.square(gap_val)
        )
        self._Bpeak_x = self._B1x * numpy.exp(
            -self._k1x * gap_val
        ) + self._B2x * numpy.exp(-self._k2x * gap_val)
        self._Bratio = self._Bpeak_z / self._Bpeak_x

    def _gap_to_energy(self, gap_mot, gap_val, pol_val, pol_name):
        self._get_Bval(gap_mot, gap_val)
        if pol_name == "linear":
            maxfield_z = self._Bpeak_z * numpy.square(
                numpy.cos(numpy.pi / self._Uperiod * pol_val)
            )
            maxfield_x = self._Bpeak_x * numpy.square(
                numpy.sin(numpy.pi / self._Uperiod * pol_val)
            )
        else:
            maxfield_z = self._Bpeak_z * numpy.cos(numpy.pi / self._Uperiod * pol_val)
            maxfield_x = self._Bpeak_x * numpy.sin(numpy.pi / self._Uperiod * pol_val)
        defz = 0.0934 * self._Uperiod * maxfield_z
        defx = 0.0934 * self._Uperiod * maxfield_x
        energy_val = self._CST * (1 + (numpy.square(defz) + numpy.square(defx)) / 2.0)
        energy_val = 12398.4187 / energy_val
        return energy_val

    def _gap_to_phase(self, gap_mot, gap_val):
        self._get_Bval(gap_mot, gap_val)
        phase_val = self._Uperiod / numpy.pi * numpy.arctan(self._Bratio)
        return phase_val

    def _gap_to_offset(self, gap_mot, gap_val, angle_val):
        alpha = numpy.radians(angle_val)
        self._get_Bval(gap_mot, gap_val)
        offset_val = 2.0 * self._Uperiod / numpy.pi
        offset_val = offset_val * numpy.arctan(
            numpy.sqrt(numpy.tan(numpy.fabs(alpha)) * self._Bratio)
        )
        if angle_val < 0:
            offset_val = -offset_val
        return offset_val
