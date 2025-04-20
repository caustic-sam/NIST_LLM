from __future__ import annotations
from typing import Any
from pydantic import BaseModel, Field


class MessageResponse(BaseModel):
    message: str = Field(description="The response message")


class ErrorResponse(BaseModel):
    error: str = Field(description="The error message")


class ExceptionResponse(BaseModel):
    exception: str = Field(description="Raised exception")
    traceback: str = Field(description="Exception traceback")


def custom_description(model: type[BaseModel], description: str) -> dict[str, Any]:
    """Customise the description of a response"""
    return {
        "description": description,
        "content": {
            "application/json": {
                "schema": model.model_json_schema(),
            }
        },
    }
