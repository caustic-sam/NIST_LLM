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
import fisx
from .helpers import get_cs_kind


_elementsInstance = fisx.Elements()
_elementsInstance.initializeAsPyMca()


_logger = logging.getLogger(__name__)


def _cs_from_library(Z: int, energies: Sequence[int], kind: str) -> numpy.ndarray:
    symbol = element_atomicnumber_to_symbol(Z)
    csdict = _elementsInstance.getMassAttenuationCoefficients(symbol, energies)
    if kind == kind.TOTAL:
        return csdict["total"]
    elif kind == kind.PHOTO:
        return csdict["photoelectric"]
    elif kind == kind.COHERENT:
        return csdict["coherent"]
    elif kind == kind.INCOHERENT:
        return csdict["compton"]
    elif kind == kind.PAIR:
        return csdict["pair"]
    elif kind == kind.SCATTER:
        coh = csdict["coherent"]
        incoh = csdict["compton"]
        if isinstance(csdict["coherent"], list):
            coh = numpy.array(coh)
            incoh = numpy.array(incoh)
        return coh + incoh
    else:
        raise ValueError(f"{kind} not supported")


def element_atomicnumber_to_symbol(Z: int) -> str:
    return _elementsInstance.getElementNames()[Z - 1]


def element_symbol_to_atomicnumber(symbol: str) -> int:
    return _elementsInstance.getAtomicNumber(symbol)


def element_density(Z: int) -> float:
    return _elementsInstance.getDensity(element_atomicnumber_to_symbol(Z))


def atomic_weight(Z: int) -> float:
    return _elementsInstance.getAtomicMass(element_atomicnumber_to_symbol(Z))


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
    cs = numpy.asarray([_cs_from_library(Zi, energies, kind) for Zi in Z])
    if has_invalid:
        cs[:, invalid] = numpy.nan
    return cs


def compound_from_catalog(name: str) -> dict:
    raise ValueError(f"{repr(name)} was not found in the compound database")
