# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

import logging
from typing import Union
from collections.abc import Sequence

import numpy
import xraylib
import xraylib_np
from .helpers import get_cs_kind

_logger = logging.getLogger(__name__)

NIST_NAME_MAPPING = {
    "water": "Water, Liquid",
    "air": "Air, Dry (near sea level)",
    "kapton": "Kapton Polyimide Film",
}
for name in xraylib.GetCompoundDataNISTList():
    NIST_NAME_MAPPING[name] = name
    NIST_NAME_MAPPING[name.replace(" ", "")] = name
    name2 = name.lower()
    NIST_NAME_MAPPING[name2] = name
    NIST_NAME_MAPPING[name2.replace(" ", "")] = name


element_atomicnumber_to_symbol = xraylib.AtomicNumberToSymbol

element_symbol_to_atomicnumber = xraylib.SymbolToAtomicNumber

element_density = xraylib.ElementDensity

atomic_weight = xraylib.AtomicWeight


def cross_section(
    Z: Union[Sequence[int], int], energies: Union[Sequence[float], float], kind: str
) -> numpy.ndarray:
    """Cross section functions are cached.

    :param Z: atomic number
    :param energies: primary beam energies
    :param kind: kind of cross section
    :returns numpy.ndarray: nZ x nE
    """
    kind = get_cs_kind(kind)
    energies = numpy.atleast_1d(energies).astype(float)
    invalid = (~numpy.isfinite(energies)) | (energies <= 0)
    has_invalid = invalid.any()
    if has_invalid:
        _logger.warning(
            "Cross-section of invalid energies will be set to NaN: %s",
            energies[invalid],
        )
        energies = energies.copy()
        energies[invalid] = 10
    Z = numpy.atleast_1d(Z).astype(int)
    if kind == kind.TOTAL:
        cs = xraylib_np.CS_Total_Kissel(Z, energies)
    elif kind == kind.PHOTO:
        cs = xraylib_np.CS_Photo_Total(Z, energies)
    elif kind == kind.COHERENT:
        cs = xraylib_np.CS_Rayl(Z, energies)
    elif kind == kind.INCOHERENT:
        cs = xraylib_np.CS_Compt(Z, energies)
    elif kind == kind.SCATTER:
        cs = xraylib_np.CS_Rayl(Z, energies) + xraylib_np.CS_Compt(Z, energies)
    else:
        raise ValueError(f"{kind} not supported")
    cs = numpy.asarray(cs)
    if has_invalid:
        cs[:, invalid] = numpy.nan
    return cs


def compound_from_catalog(name: str) -> dict:
    try:
        name2 = NIST_NAME_MAPPING[name]
    except KeyError:
        raise ValueError(f"{repr(name)} was not found in the NIST compound database")
    result = xraylib.GetCompoundDataNISTByName(name2)
    mass_fractions = {
        element_atomicnumber_to_symbol(Z): wfrac
        for Z, wfrac in zip(result["Elements"], result["massFractions"])
    }
    return {
        "name": result["name"],
        "density": result["density"],
        "elemental_fractions": mass_fractions,
        "kind": "mass",
    }
