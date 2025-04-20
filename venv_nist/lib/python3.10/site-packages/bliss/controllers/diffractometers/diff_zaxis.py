# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

from .diff_base import Diffractometer


class DiffZAXIS(Diffractometer):

    PSEUDOS_FMT = """\
H K L = {pos[hkl_h]:f} {pos[hkl_k]:f} {pos[hkl_l]:f}
TWO THETA = {pos[tth2_tth]:f}
Alpha = {pos[incidence_incidence]:.5g}  Beta = {pos[emergence_emergence]:.5g}
"""

    def show(self):

        print(("\nZAXIS Geometry, HKL mode : {0}".format(self.hklmode)))
        if len(self.frozen_angles_names):
            if len(self.frozen_angles):
                for name, value in self.frozen_angles.items():
                    print("Frozen {0:s} = {1:.4f}".format(name, value))
            else:
                print("No angles frozen yet.")
        if self.hklmode == "psi_constant":
            print("Constant psi = {0:.4f}".format(self.psi_constant))

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
