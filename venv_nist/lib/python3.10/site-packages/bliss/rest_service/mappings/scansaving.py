#!/usr/bin/env python
# -*- coding: utf-8 -*-

from __future__ import annotations

import logging
import typing

from bliss import _get_current_session

from bliss.rest_service.mappingdef import (
    ObjectMapping,
    HardwareProperty,
    CouldRaiseException,
)
from ..types.scansaving import ScanSavingType

logger = logging.getLogger(__name__)


class NoneIfAttributeIsMissingProperty(CouldRaiseException):
    """Acceleration and velocity can not be exposed in case of a
    CalcController.
    """

    def handleExceptionAsValue(self, exception: Exception) -> typing.Any:
        """Return the value to use, else raise the exception."""
        if isinstance(exception, AttributeError):
            return None
        raise exception


class Scansaving(ObjectMapping):
    TYPE = ScanSavingType

    PROPERTY_MAP = {
        "base_path": HardwareProperty("base_path"),
        "beamline": NoneIfAttributeIsMissingProperty("beamline"),
        "data_path": HardwareProperty("data_path"),
        "root_path": HardwareProperty("root_path"),
        "filename": HardwareProperty("filename"),
        "template": HardwareProperty("template"),
        "data_filename": HardwareProperty("data_filename"),
        "proposal_name": NoneIfAttributeIsMissingProperty("proposal_name"),
        "proposal_session_name": NoneIfAttributeIsMissingProperty(
            "proposal_session_name"
        ),
        "collection_name": NoneIfAttributeIsMissingProperty("collection_name"),
        "dataset_name": NoneIfAttributeIsMissingProperty("dataset_name"),
        "dataset_definition": NoneIfAttributeIsMissingProperty(
            "dataset.all.definition"
        ),
        "sample_notes": NoneIfAttributeIsMissingProperty("dataset.all.Sample_notes"),
        "sample_name": NoneIfAttributeIsMissingProperty("dataset.sample_name"),
        "sample_description": NoneIfAttributeIsMissingProperty(
            "dataset.sample_description"
        ),
    }
    CALLABLE_MAP = {"create_root_path": "create_root_path"}

    def __init__(self, obj, name: str):
        ObjectMapping.__init__(self, obj, name)
        # Replace the dummy by the actual BLISS object
        # FIXME: Actually that's a cheat, the scan saving can dynamically change in BLISS
        session = _get_current_session()
        self._object = session.scan_saving

    def _call_create_root_path(self):
        self._object.create_root_path()


Default = Scansaving
