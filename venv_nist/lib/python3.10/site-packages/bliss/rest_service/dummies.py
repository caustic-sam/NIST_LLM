# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.


class DummyObject:
    """Dummy object to replace BLISS objects which can't be loaded from the
    BLISS configuration.
    """

    def __init__(self, name: str):
        self._name = name

    @property
    def name(self):
        return self._name


class DummyNotLoaded(DummyObject):
    """Dummy object for BLISS object which raises exception during loading."""

    pass


class DummyScanSaving(DummyObject):
    """
    Dummy object for special `SCAN_SAVING` object, which is not a
    real BLISS object.
    """


class DummyActiveMg(DummyObject):
    """
    Dummy object for special `ACTIVE_MG` object, which is not a
    real BLISS object.
    """


class DummyActiveTomo(DummyObject):
    """
    Dummy object for special `ACTIVE_TOMOCONFIG` object, which is not a
    real BLISS object.
    """
