from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


class CallFunction(BaseModel):
    env_object: str | None = Field(
        None,
        description="A path to an object from the environment on which to call a function",
    )
    object: str | None = Field(
        None,
        description="A name of a registered object",
    )
    function: str = Field(description="The function to call")
    args: Optional[list[Any]] = Field(
        default_factory=list,
        description="A list of arguments, special types can be handled with jsonready",
    )
    kwargs: Optional[dict[str, Any]] = Field(
        default_factory=dict,
        description="A dictionary of kwargs, special types can be handled with jsonready",
    )
    has_scan_factory: bool | None = Field(
        False,
        description="Assume this function creates a `Scan`, call it with `run=False`, start the scan, and retrieve the scan in the `progress` response",
    )
    in_terminal: bool | None = Field(
        False,
        description="If true, the call is executed inside the BLISS terminal. If the terminal is busy an exception is raised.",
    )
    emit_stdout: bool | None = Field(
        False,
        description="If true, the call stdout are emitted as event in the websocket namespace `/call`.",
    )


class FunctionCallStatePath(BaseModel):
    call_id: str


class CallFunctionResponse(BaseModel):
    call_id: str


class CallFunctionAsyncState(BaseModel):
    state: Literal["running", "terminated", "killed"]
    return_value: Optional[Any] = None
    progress: Optional[dict[str, Any]] = None
