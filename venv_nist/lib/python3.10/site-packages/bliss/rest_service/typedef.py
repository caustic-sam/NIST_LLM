# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

from __future__ import annotations

import typing
from pydantic import BaseModel, TypeAdapter
from pydantic_core import PydanticUndefined
from pydantic.fields import _Unset, Field as PydanticField
from pydantic._internal._model_construction import ModelMetaclass

import logging

logger = logging.getLogger(__name__)


ARGS = typing.TypeVar("ARGS")
KWARGS = typing.TypeVar("KWARGS")
RETURN_TYPE = typing.TypeVar("RETURN_TYPE")


def Field(
    default: typing.Any = PydanticUndefined,
    description: str = _Unset,
    read_only: bool = _Unset,
    length: int = _Unset,
) -> typing.Any:
    """Add extra metadata to a property of an abstract hardware definition

    Attributes:
        read_only: If the property is read-only
        length: If type list have a fixed length
    """
    json_schema_extra: dict[str, typing.Any] = {}
    if read_only is not _Unset:
        json_schema_extra["readOnly"] = read_only
    if length is not _Unset:
        json_schema_extra["length"] = length

    return PydanticField(
        default,
        description=description,
        json_schema_extra=_Unset if json_schema_extra == {} else json_schema_extra,
    )


def HardwareRefField(
    default: typing.Any = PydanticUndefined, read_only: bool = _Unset
) -> typing.Any:
    """Define a HardwareRef property to an abstract hardware definition"""
    json_schema_extra: dict[str, typing.Any] = {"blissTpe": "hardware_ref"}
    if read_only is not _Unset:
        json_schema_extra["readOnly"] = read_only
    return PydanticField(
        default,
        json_schema_extra=_Unset if json_schema_extra == {} else json_schema_extra,
    )


def HardwareRefListField(
    default: typing.Any = PydanticUndefined, read_only: bool = _Unset
) -> typing.Any:
    """Define a HardwareRef property to an abstract hardware definition"""
    json_schema_extra: dict[str, typing.Any] = {"blissTpe": "hardware_ref"}
    if read_only is not _Unset:
        json_schema_extra["readOnly"] = read_only
    return PydanticField(
        default,
        json_schema_extra=_Unset if json_schema_extra == {} else json_schema_extra,
    )


class CallableParams(BaseModel, typing.Generic[ARGS, KWARGS]):
    args: ARGS
    kwargs: KWARGS


class Callable(BaseModel, typing.Generic[ARGS, KWARGS, RETURN_TYPE]):
    params: CallableParams[ARGS, KWARGS]
    return_type: RETURN_TYPE


class EmptyCallable(Callable[tuple[()], dict[None, None], None]):
    pass


ARG1 = typing.TypeVar("ARG1")


class Callable1Arg(Callable[tuple[ARG1], dict[None, None], None]):
    pass


class CallableSchema(dict[str, Callable]):
    pass


class IterableModelMetaClass(ModelMetaclass):
    def __contains__(self, val):
        return val in self.model_fields

    def __iter__(self):
        self.__iter = iter(self.model_fields.keys())
        return self.__iter

    def __next__(self):
        return next(self.__iter)


class HardwareSchema(BaseModel, metaclass=IterableModelMetaClass):
    @classmethod
    def read_only_prop(self, prop: str) -> bool:
        extra = self.model_fields[prop].json_schema_extra
        if extra:
            return extra.get("readOnly", False)
        return False

    @classmethod
    def validate_prop(self, prop: str, value) -> typing.Any:
        PropValidator = TypeAdapter(self.model_fields[prop].annotation)
        return PropValidator.validate_python(value)


class ObjectType:
    """Describe an abstract hardware object"""

    NAME: str
    """Name used to reference this type"""

    STATE_OK: list[str] = []
    """List of states considered as normal (usually displayed in green)"""

    PROPERTIES: type[HardwareSchema]
    """Define the set properties exposed by such object"""

    CALLABLES: type[CallableSchema]
    """Define the set functions exposed by such object"""
