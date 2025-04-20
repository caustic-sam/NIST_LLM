from __future__ import annotations
from typing import Any, Optional

from typing_extensions import TypedDict
from pydantic import BaseModel, Field


class RegisterHardwareSchema(BaseModel):
    names: list[str]


class ObjectError(BaseModel):
    name: str
    error: str


class CouldNotRegisterErrorResponse(BaseModel):
    error: str = Field(description="The error message")
    objects: list[ObjectError] = Field(
        [], description="List of objects that could not be registered"
    )


class HardwaresResourceQuery(BaseModel):
    type: Optional[str] = Field(None, description="Filter by a specific type")


class HardwareNamePath(BaseModel):
    name: str = Field(description="The unique bliss object name")


class LockedDict(TypedDict):
    reason: str


class ObjectSchema(BaseModel):
    name: str = Field(description="The object name, which is it's unique identifier")
    type: str = Field(description="The object type: motor, multiposition, ...")
    online: bool = Field(description="Whether the object is available")
    errors: list[dict[str, Any]] = Field(
        description="Any errors accessing specific properties"
    )
    alias: Optional[str] = Field(None, description="The objects alias (if any)")
    properties: dict[str, Any] = Field(
        description="A list of available properties on the object and their values"
    )
    user_tags: list[str] = Field(
        description="List of user tags which categorize this object"
    )
    locked: LockedDict | None = Field(
        description="If not None, the device is locked, and no actions can be requested."
    )


class SetObjectProperty(BaseModel):
    property: str = Field(description="The property to set")
    value: Any = Field(description="Its value")
