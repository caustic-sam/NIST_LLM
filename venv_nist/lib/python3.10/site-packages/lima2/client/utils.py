# This file is part of the Lima2 project
#
# Copyright (c) 2020-2024 Beamline Control Unit, ESRF
# Distributed under the MIT license. See LICENSE for more info.

"""Utility functions"""

import jsonschema
import numpy as np
from jsonschema import validators
from jsonschema.protocols import Validator


def docstring_from(parent):
    """Inherit the docstring from `parent`, overwriting the current one"""

    def decorated(child):
        child.__doc__ = parent.__doc__
        return child

    return decorated


class classproperty:
    """A decorator to create a class property from a class method.

    Example:
    ```
    class Rose:
        @classproperty
        def color(cls):
            return "RED"

    assert Rose.color == "RED"
    ```
    """

    def __init__(self, method):
        self.getter = method

    def __get__(self, instance, cls):
        """Getter called when the property is accessed. Calls the class method."""
        return self.getter(cls)


def find_first_gap(array: np.ndarray) -> int:
    """Find the index of the first element after a gap in a sorted array."""
    gap_idx = np.where(np.diff(array) > 1)[0]

    return gap_idx[0] + 1 if gap_idx.size > 0 else array.size


ValidationError = jsonschema.ValidationError
"""Type alias for `jsonschema.ValidationError`."""


def validate(instance: dict, schema: dict) -> None:
    """Lima2 param validation.

    Raises a ValidationError if `instance` fails the schema validation.

    Since JSON schema draft 6, a value is considered an "integer" if its
    fractional part is zero [1]. This means for example that 2.0 is considered
    an integer. Since we don't want floats to pass the validation where ints are
    expected, this function overrides this flexibility with a stricter type check.

    [1] https://json-schema.org/draft-06/json-schema-release-notes
    """

    def is_strict_int(validator, value):
        return type(value) is int

    base_validator: type[Validator] = validators.validator_for(schema)
    strict_checker = base_validator.TYPE_CHECKER.redefine("integer", is_strict_int)
    strict_validator = validators.extend(base_validator, type_checker=strict_checker)

    jsonschema.validate(instance, schema, cls=strict_validator)
