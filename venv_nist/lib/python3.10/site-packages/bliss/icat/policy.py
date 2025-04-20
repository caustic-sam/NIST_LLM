# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

from collections.abc import Iterable

from pyicat_plus.metadata.definitions import load_icat_fields

from bliss.common.logtools import log_debug, log_warning
from bliss.common.utils import autocomplete_property
from bliss.common.namespace_wrapper import NamespaceWrapper
from bliss import current_session
from bliss.icat.metadata import ICATmetadata
from bliss.icat.json_policy import RedisJsonNode


class DataPolicyObject:
    """A data policy object with a Redis representation that
    allows for storing ICAT metadata fields
    """

    def __init__(self, node: RedisJsonNode):
        self._node = node
        content = self._node.get()
        for key in ("__name__", "__path__", "__metadata__", "__frozen__"):
            assert key in content
        self.__icat_fields = None
        self.__metadata_namespace = None
        self.__definitions = None
        self._expected_field = set()

    def __str__(self):
        return self.name

    def __setitem__(self, key, value):
        """Set metadata field in Redis"""
        self.write_metadata_field(key, value)

    def __getitem__(self, key):
        """Get metadata field from Redis"""
        return self.read_metadata_field(key)

    def __contains__(self, key):
        """Check metadata field in Redis"""
        return self.has_metadata_field(key)

    @property
    def name(self):
        return self._node.get()["__name__"]

    @property
    def path(self):
        return self._node.get()["__path__"]

    def update_metadata(self, value: dict):
        content = self._node.get()
        content["__metadata__"].update(value)
        self._node.set(content)

    @property
    def _local_metadata(self):
        return self._node.get()["__metadata__"]

    @property
    def _recursive_metadata(self):
        if not self.is_frozen and self.parent:
            metadata = self.parent._recursive_metadata.copy()
            metadata.update(self._local_metadata)
        else:
            metadata = self._local_metadata.copy()
        return metadata

    @property
    def children(self):
        # derived classes can use a predefined child class or infer it from node content
        raise NotImplementedError

    @property
    def is_frozen(self):
        """Frozen means it does take metadata fields from its parent"""
        return self._node.get()["__frozen__"]

    def _log_debug(self, msg):
        log_debug(self, f"{type(self).__name__}({self}): {msg}")

    def _log_warning(self, msg):
        log_warning(self, f"{type(self).__name__}({self}): {msg}")

    @property
    def parent(self):
        raise NotImplementedError

    def get_current_icat_metadata(self):
        """Get all metadata key-value pairs from Redis (self and parents)"""
        return self._recursive_metadata

    def get_current_icat_metadata_fields(self):
        """Get all metadata field names from Redis (self and parents)."""
        return set(self._recursive_metadata.keys())

    def freeze_inherited_icat_metadata(self):
        """After this, changes in the parent metadata no longer affect
        the current metadata.
        """
        if self.is_frozen or not self.parent:
            return
        content = self._node.get()
        content["__metadata__"] = self._recursive_metadata
        content["__frozen__"] = True
        self._node.set(content)

    def unfreeze_inherited_icat_metadata(self):
        """After this, the parent metadata affect the current metadata."""
        content = self._node.get()
        content["__frozen__"] = False
        self._node.set(content)

    def has_metadata_field(self, key):
        """Check metadata field exists in Redis (self and parents)."""
        return key in self.get_current_icat_metadata_fields()

    def read_metadata_field(self, key):
        """Get the value of one metadata field from Redis (self and parents).
        Raises `KeyError` when field is missing.
        """
        try:
            return self._local_metadata[key]
        except KeyError:
            if self.parent:
                return self.parent.read_metadata_field(key)
            else:
                raise

    def get_metadata_field(self, key, default=None):
        """Get the value of one metadata field from Redis (self and parents).
        Returns `default` when field is missing.
        """
        try:
            return self.read_metadata_field(key)
        except KeyError:
            return default

    def write_metadata_field(self, key, value):
        """Store metadata key-value pair in Redis. Does not affect the parent.
        Remove key when the value is `None`.
        Raises `KeyError` when the key is not valid.
        Raises `ValueError` when the value is not a string.
        """
        if value is None:
            self.remove_metadata_field(key)
            return
        if not self.validate_field_name(key):
            raise KeyError(f"{repr(key)} is not a valid ICAT field")

        content = self._node.get()
        content["__metadata__"][key] = value
        self._node.set(content)

    def remove_metadata_field(self, key):
        """Remove a metadata field from Redis if it exists.
        Does not affect the parents.
        """
        content = self._node.get()
        content["__metadata__"].pop(key, None)
        self._node.set(content)

    def remove_all_metadata_fields(self):
        """Remove a metadata field from Redis if it exists.
        Does not affect the parents.
        """
        content = self._node.get()
        content["__metadata__"] = {}
        self._node.set(content)

    @property
    def _icat_fields(self) -> ICATmetadata:
        if self.__icat_fields is None:
            try:
                self.__icat_fields = current_session.icat_metadata._icat_fields
            except AttributeError:
                self.__icat_fields = load_icat_fields()
        return self.__icat_fields

    @autocomplete_property
    def definitions(self) -> NamespaceWrapper:
        if self.__definitions is None:
            self.__definitions = self._icat_fields.namespace()
        return self.__definitions

    @autocomplete_property
    def metadata(self) -> NamespaceWrapper:
        if self.__metadata_namespace is None:
            self.__metadata_namespace = self._icat_fields.namespace(
                getter=self.get_metadata_field, setter=self.write_metadata_field
            )
        return self.__metadata_namespace

    def validate_field_name(self, field_name: str) -> bool:
        return self._icat_fields.valid_field_name(field_name)

    @autocomplete_property
    def all(self) -> NamespaceWrapper:
        """namespace to access all possible keys"""
        names = [field.field_name for field in self._icat_fields.iter_fields()]
        return NamespaceWrapper(
            names, self.get_metadata_field, self.write_metadata_field
        )

    @property
    def expected_fields(self):
        """all required metadata fields"""
        if self.parent:
            return self._expected_field | self.parent.expected_fields
        else:
            return self._expected_field

    @autocomplete_property
    def expected(self):
        """namespace to read/write expected metadata fields"""
        return NamespaceWrapper(
            self.expected_fields, self.get_metadata_field, self.write_metadata_field
        )

    @property
    def existing_fields(self):
        """all existing metadata fields"""
        return self.get_current_icat_metadata_fields()

    @autocomplete_property
    def existing(self) -> NamespaceWrapper:
        """namespace to read/write existing metadata fields"""
        return NamespaceWrapper(
            self.existing_fields, self.get_metadata_field, self.write_metadata_field
        )

    @property
    def missing_fields(self):
        """returns a list of required metadata fields that are not yet filled"""
        return self.expected_fields.difference(self.existing_fields)

    @autocomplete_property
    def missing(self):
        """namespace to read/write mising metadata fields"""
        return NamespaceWrapper(
            self.missing_fields, self.get_metadata_field, self.write_metadata_field
        )

    def check_metadata_consistency(self):
        """returns True when all required metadata fields are filled"""
        mtf = self.missing_fields
        if mtf:
            self._log_warning(
                f"The following metadata fields are expected by a given technique but not provided: {mtf}"
            )
        return not mtf

    @property
    def metadata_is_complete(self):
        return not self.missing_fields

    @autocomplete_property
    def techniques(self) -> set[str]:
        definition = self.get_metadata_field("definition")
        if definition:
            return set(definition.split(" ")) - {"UNKNOWN"}
        else:
            return set()

    def add_techniques(self, *techniques: Iterable[str]):
        existing = self.techniques
        existing.update(s.upper() for s in techniques)
        definition = " ".join(sorted(existing))
        self.write_metadata_field("definition", definition)

    def remove_techniques(self, *techniques: Iterable[str]) -> None:
        remove = set(s.upper() for s in techniques)
        techniques = self.techniques - remove
        definition = " ".join(sorted(techniques))
        self.write_metadata_field("definition", definition)
