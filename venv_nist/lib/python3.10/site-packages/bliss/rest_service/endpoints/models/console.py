from pydantic import BaseModel, Field


class TerminalSizeBody(BaseModel):
    session_name: str
    w: int = Field(description="Console width in columns")
    h: int = Field(description="Console height in rows")
