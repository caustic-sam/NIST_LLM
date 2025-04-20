# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

from .diff_base import Diffractometer


class DiffK6C(Diffractometer):

    PSEUDOS_FMT = """\
H K L = {pos[hkl_h]:f} {pos[hkl_k]:f} {pos[hkl_l]:f}
TWO THETA = {pos[tth2_tth]:f}
Alpha = {pos[incidence_incidence]:.5g}  Beta = {pos[emergence_emergence]:.5g}
"""

    def show(self):  ### TODO ### making a nice, "pa" spec-like output
        print(f"\nK6C Geometry, HKL mode : {self.hklmode}")
        pars_dict = self.geometry.get_mode_pars("hkl", self.hklmode)
        if len(pars_dict):
            str = "Parameters : "
            for name, value in pars_dict.items():
                if (name == "phi") and (self.eulerian_par == 0):
                    value -= 180
                str += f"\n   {name} = {value:.4f}"
            print(str)
        if len(self.frozen_angles_names):
            if len(self.frozen_angles):
                str = "Frozen angles : "
                for name, value in self.frozen_angles.items():
                    str += f"\n   {self._motor_names[name]} = {value:.4f}"
            else:
                str = f"No angles {[self._motor_names[name] for name in self.frozen_angles_names]} frozen yet."
            print(str)

        if self.sample.get_n_reflections() < 1:
            print("\nPrimary reflection not yet defined.")
        else:
            (hkl, pos, wl) = self.sample.get_ref0()
            hstr = ["{0:s}".format(self._motor_names[name]) for name in self.axis_names]
            pstr = ["{0:.4f}".format(pos[name]) for name in self.axis_names]
            print(("\nPrimary Reflection (at lambda {0:.4f}):".format(wl)))
            print(("{0:>26s} = {1}".format(" ".join(hstr), " ".join(pstr))))
            print(("{0:>26s} = {1} {2} {3}".format("H K L", *hkl)))

        if self.sample.get_n_reflections() < 2:
            print("\nSecondary reflection not yet defined.")
        else:
            (hkl, pos, wl) = self.sample.get_ref1()
            hstr = ["{0:s}".format(self._motor_names[name]) for name in self.axis_names]
            pstr = ["{0:.4f}".format(pos[name]) for name in self.axis_names]
            print(("\nSecondary Reflection (at lambda {0:.4f}):".format(wl)))
            print(("{0:>26s} = {1}".format(" ".join(hstr), " ".join(pstr))))
            print(("{0:>26s} = {1} {2} {3}".format("H K L", *hkl)))

        print("\nLattice Constants (lengths / angles):")
        print(
            (
                "{0:>26s} = {1:.3f} {2:.3f} {3:.3f} / {4:.3f} {5:.3f} {6:.3f}".format(
                    "real space", *self.sample.get_lattice()
                )
            )
        )
        print(
            (
                "{0:>26s} = {1:.3f} {2:.3f} {3:.3f} / {4:.3f} {5:.3f} {6:.3f}".format(
                    "reciprocal space", *self.sample.get_reciprocal_lattice()
                )
            )
        )

        print(
            "\nLambda = {0:.5f}  Energy = {1:.3f} keV".format(
                self.wavelength, self.energy
            )
        )

    @property
    def eulerian_par(self):
        vals = self._geometry.get_mode_pars("eulerians", "eulerians")
        return vals["solutions"]

    @eulerian_par.setter
    def eulerian_par(self, solutions):
        if solutions in (0, 1):
            self._geometry.set_mode_pars(
                "eulerians", "eulerians", {"solutions": solutions}
            )
        else:
            raise ValueError(
                "solutions must be 0 or 1 to select the first or second solution"
            )
        self._calc_geo()

    @property
    def hklpars(self):
        current_mode = self.hklmode
        pars_dict = self.geometry.get_mode_pars("hkl", current_mode)
        print(f"HKL mode {current_mode}\n")
        for key in pars_dict.keys():
            print(f"{key:10s} : {pars_dict[key]}")
            if (key == "phi") and (self.eulerian_par == 0):
                print("### Using eulerian #0 solution. Adding 180° to parameter psi")
                print(f"### 'real' psi value is {pars_dict[key]-180}")

    def set_hklpars(self):
        current_mode = self.hklmode
        pars_dict = self.geometry.get_mode_pars("hkl", current_mode)
        for key in pars_dict.keys():
            oldval = pars_dict[key]
            newval = input(f" {key} ({oldval}) ? ")
            if newval:
                if (key == "phi") and (self.eulerian_par == 0):
                    pars_dict[key] = float(newval) + 180
                else:
                    pars_dict[key] = float(newval)
        self.geometry.set_mode_pars("hkl", current_mode, pars_dict)
