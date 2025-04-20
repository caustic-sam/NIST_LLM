# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.
"""
Get/Set transmission factor as function of the filters, mounted on a
ESRF standard monochromatic attenuator and the energy (fixed or tunable).
It may not be possible to set the exact factor required.

yml configuration example:

.. code-block::

    name: transmission
    class: Transmission
    matt: $matt
    energy: $energy (or energy: 12.7)
    datafile: "/users/blissadm/local/beamline_control/configuration/misc/transmission.dat"
    plugin: generic

Datafile example for tunable energy:
First column is the energy, next columns are the corresponding transmission
factors for each attenuator blade;
There should be attenuators indexes as comments like this:

.. code-block::

    #MIN_ATT_INDEX = 1
    #MAX_ATT_INDEX = 13
    #
    20.0 100 100 100 94.443 94.44 77.778 66.66 94.441 77.778 55.558 38.882 11.11 11.109
    19.5 100 100 100 100 86.363 77.272 63.636 86.3638 77.27 50 31.817 9.0896 9.0896
    ...

Datafile example for fixed energy:

.. code-block::

    #MIN_ATT_INDEX = 1
    #MAX_ATT_INDEX = 9
    #
    12.812 100.00 72.00 92.00 3.50 18.00 30.00 42.70 58.18 68.0

"""
import sys
from functools import reduce


class Energy:
    """Class to read the energy."""

    def __init__(self, energy):
        self.__energy = energy
        self.tunable = True

        if isinstance(energy, float):
            self.tunable = False

    @property
    def read(self):
        """Read the energy.
        Returns:
            (float): Energy value [keV].
        """
        if self.tunable:
            return self.__energy.position
        return self.__energy


class Transmission:
    """Handle transmission for standard ESRF monochromatic attenuators"""

    def __init__(self, config):
        energy = config.get("energy")
        if energy:
            self.energy = Energy(energy)
        self.datafile = config.get("datafile")

        self.__matt = config.get("matt")
        self.transmission_factor = None

        self.calc_transmission = CalcTransmission(self.datafile)

    def set(self, value):
        """Set the transmission factor.
        Args:
            value (float): transmission factor (0-100) [%].
        Raises:
            RuntimeError: wrong energy or impossible attenuators combination
        """
        egy = self.energy.read
        if egy <= 0:
            raise RuntimeError(f"Wrong energy value {egy:0.3f}")

        try:
            transmission, vals = self.calc_transmission.get_attenuation(
                egy, value, self.datafile
            )
        except RuntimeError as err:
            raise err

        if not vals:
            raise RuntimeError(
                f"No attenuators combination found for transmission {value} % at {egy:0.3f} keV"
            )
        value = 0
        if -1 not in vals:
            for i in vals:
                value += 1 << i
        self.__matt.mattstatus_set(value)
        self.transmission_factor = transmission

    def get(self):
        """Read the current transmission factor.
        Returns:
            transmission_factor (float): current transmission factor [%].
        """
        egy = self.energy.read
        if egy <= 0:
            raise RuntimeError(f"Wrong energy value {egy:0.3f}")

        self.transmission_factor = 100.0
        _matt = self.__matt._status_read()

        if not _matt:
            return self.transmission_factor
        self.transmission_factor = self.calc_transmission.get_transmission_factor(
            egy, _matt, self.datafile
        )
        return self.transmission_factor


class CalcTransmission:
    """Calculate transmission or attenuators sequence for a given energy"""

    def __init__(self, filename=None):
        self.filename = filename
        self.min_att_index = None
        self.max_att_index = None
        self.attenuation_table = []
        self.all_attenuation = {}

    def load_attenuation_table(self, filename=None):
        """Get the min/max attenuation numbers and the energy table.

        Args:
            filename (str): File name (full path) containing the
                            energy/transmission table.
        """
        filename = filename or self.filename

        with open(filename, encoding="utf-8") as _f:
            egy_array = []
            for line in _f:
                if line.startswith("#"):
                    if "MIN" in line:
                        self.min_att_index = int(line[line.index("=") + 1 :])
                    if "MAX" in line:
                        self.max_att_index = int(line[line.index("=") + 1 :])
                else:
                    egy_array.append(list(map(float, line.split())))
        self.attenuation_table = egy_array

    def _get_combinations(self, items, nb_elem):
        """Return an iterator for lazy evaluation of all the possible unique
        combinations of 'nb_elem' elements in 'items'.
        """
        if nb_elem == 0:
            yield []
        else:
            for i, j in enumerate(items):
                for k in self._get_combinations(items[i + 1 :], nb_elem - 1):
                    yield [j] + k

    def select_energy(self, egy_value, att_array, precision=0.25):
        """Select energy in the energy array.

        Args:
            egy_value (float): Energy [keV].
            att_array (list): List of floats (transmission factors).
            precision (float): Perecision for the calculation of the factor.

        Returns:
            (list): List of floats - transmission factors for the chosen energy.
        """
        for egy_array in att_array:
            if abs(egy_array[0] - egy_value) <= precision:
                return egy_array
        return []

    def get_exact_attenuation(self, transmission_factor, egy_array):
        """Get if there is an exact attenuation.

        Args:
            transmission_factor (float): Transmission factor [%].
            egy_array (list): List of floats - transmission factors.

        Returns:
            (tupple): Tupple (transmission_factor, [attenuator indexes]).
        """
        if egy_array:
            for tm_ in egy_array[1:]:
                if abs(tm_ - transmission_factor) < 0.002:
                    return (tm_, [egy_array.index(tm_)])
        return None, None

    def get_attenuator_combinations(self, egy_array):
        """Get the attenuators combumation.

        Args:
            egy_array (list): List of floats - transmission factors.

        Returns:
            (list): List of ints - attenuator indexes.
        """
        if (
            len(egy_array) == 0
            or len(egy_array) < self.min_att_index
            or len(egy_array) < self.max_att_index
        ):
            return []
        if egy_array[0] in self.all_attenuation:
            return self.all_attenuation[egy_array[0]]

        all_attenuator_combinations = []
        all_indexes = list(range(self.min_att_index, self.max_att_index + 1))
        for i in range(self.max_att_index - self.min_att_index + 1):
            for _combination in self._get_combinations(all_indexes, i + 1):
                all_attenuator_combinations.append(
                    (
                        reduce(
                            lambda x, y: x * y / 100,
                            [egy_array[j] for j in _combination],
                        ),
                        _combination,
                    )
                )
        # store list
        self.all_attenuation[egy_array[0]] = all_attenuator_combinations
        return all_attenuator_combinations

    def get_attenuation(self, egy_value, transmission_factor, fname=None):
        """Get the attenuation.

        Args:
            egy_value (float): Energy [keV].
            transmission_factor (float): Transmission factor [%].
            fname (str): File (full path) to get the transmission table.
        """
        if transmission_factor < 0 or transmission_factor > 100:
            raise RuntimeError("Transmission factor should be between 0 and 100")

        if len(self.attenuation_table) == 0:
            self.load_attenuation_table(fname)

        egy_array = self.select_energy(egy_value, self.attenuation_table)

        # check if there is exact attenuation in the table
        etf, earr = self.get_exact_attenuation(transmission_factor, egy_array)
        if etf:
            return [etf, [earr[0] - self.min_att_index]]

        all_att_combinations = [
            (abs((x[0]) - transmission_factor), x[1])
            for x in self.get_attenuator_combinations(egy_array)
        ]

        try:
            att_combination = min(all_att_combinations)[1]
            # print(att_combination)
        except ValueError:
            att_combination = []
            att_factor = 0
        else:
            att_factor = reduce(
                lambda x, y: x * y / 100, [egy_array[i] for i in att_combination]
            )

        result_list = [att_factor]
        result_list.append([x - self.min_att_index for x in att_combination])
        return result_list

    def get_transmission_factor(self, egy_value, att_combination, fname=None):
        """Calculate the attenuation factor

        Args:
            egy_value (float): Energy [keV].
            att_combination (list or str): dictionary of attenuator indexes
                                           (as returned by get_attenuation)
                                           or string with attenuator indexes
                                           separated by spaces
            fname (str): file name (full path) with the transmission factors

        Returns:
            (float): calculater transmission factor (0-100)

        Raises:
            RuntimeError: Wrong combination input.
        """
        if not self.attenuation_table:
            self.load_attenuation_table(fname)

        egy_array = self.select_energy(egy_value, self.attenuation_table)

        if not egy_array:
            # there is no corresponding energy !
            raise RuntimeError(f"No configuration for {egy_value:0.3f} keV")

        if isinstance(att_combination, str):
            att_combination = att_combination.split()

        try:
            return reduce(
                lambda x, y: x * y / 100,
                [egy_array[int(i) + self.min_att_index] for i in att_combination],
            )
        except (IndexError, TypeError, ValueError) as err:
            raise RuntimeError("Wrong attenuators combination input") from err


if __name__ == "__main__":

    def print_usage():
        """Helper"""
        print(f"Usage: {sys.argv[0]}\n\t -t energy transmission datafile")
        print("\tor\n\t -a energy attenuator_position(s)_string datafile")
        sys.exit(0)

    if len(sys.argv) < 5:
        print_usage()

    EGY = float(sys.argv[2])
    try:
        DATAFILE = sys.argv[4]
    except IndexError:
        DATAFILE = None

    ct = CalcTransmission(DATAFILE)

    ct.select_energy(EGY, ct.attenuation_table)

    if sys.argv[1] == "-t":
        abb = ct.get_attenuation(EGY, float(sys.argv[3]), DATAFILE)
        print(f" Table: {abb}")
        print(f" Result: transmission {abb[0]}, attenuators {abb[1:]}")
    elif sys.argv[1] == "-a":
        attstr = sys.argv[3]
        print(f"Transmission: {ct.get_transmission_factor(EGY, attstr, DATAFILE)} %")
    else:
        print_usage()

    sys.exit(0)
