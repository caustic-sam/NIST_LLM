import typing

from pydantic import BaseModel, Field


class ObjectTypeIDPath(BaseModel):
    id: str = Field(description="The id of the object type")


class ObjectTypeSchema(BaseModel):
    type: str = Field(description="The absract object type: motor, multiposition, ...")
    state_ok: list[str] = Field(description="Whether the object is available")
    properties: typing.Any = Field(description="Schema of the available properties")
    callables: dict[str, typing.Any] = Field(
        default_factory=dict, description="Schema of the available callables"
    )
