from typing import Any

from pydantic import BaseModel, Field


class YamlContentSchema(BaseModel):
    content: Any = Field(description="Content of the yaml file")


class FilePath(BaseModel):
    path: str = Field(description="Name of the file to retrieve")
