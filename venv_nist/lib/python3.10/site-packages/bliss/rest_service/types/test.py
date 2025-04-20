from __future__ import annotations
import logging
from typing import Literal
from typing_extensions import TypedDict

from bliss.rest_service.typedef import (
    ObjectType,
    HardwareSchema,
    Field,
    Callable,
    CallableSchema,
    Callable1Arg,
    EmptyCallable,
)

logger = logging.getLogger(__name__)

TestStates = ["ON", "OFF", "UNKNOWN", "ERROR"]


class _PropertiesSchema(HardwareSchema):
    state: str = Field(read_only=True)
    number: float
    number_positive: float
    string: str
    option: Literal["one", "two", "three"]
    read_only: int = Field(read_only=True)


class NamedArgs1(TypedDict):
    info: bool


class _CallablesSchema(CallableSchema):
    func0: EmptyCallable
    func1: Callable1Arg[str]
    func2: Callable[tuple[str, int], dict[str, str], bool]
    func_named_args1: Callable[tuple[str, int], NamedArgs1, bool]
    func_mul: Callable[tuple[float, float], dict[None, None], float]
    long_process: EmptyCallable


class TestType(ObjectType):
    NAME = "test"
    STATE_OK = [TestStates[0], TestStates[1]]

    PROPERTIES = _PropertiesSchema
    CALLABLES = _CallablesSchema


Default = TestType
