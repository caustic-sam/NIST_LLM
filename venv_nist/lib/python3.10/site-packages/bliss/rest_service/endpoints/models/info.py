from pydantic import BaseModel, Field, ConfigDict


class InfoSchema(BaseModel):
    """
    Information related to this session.

    This could contain extra keys.
    """

    model_config = ConfigDict(extra="allow")

    synchrotron: str = Field(description="Name of the synchrotron. Can be empty.")
    beamline: str = Field(description="Name of the beamline. Can be empty.")
    instrument: str = Field(description="Name of the instrument. Can be empty.")
    session: str = Field(description="Name of the BLISS session")
    bliss_version: str = Field(description="Version of BLISS")
    blissdata_version: str = Field(description="Version of bliss-data")
    flint_version: str = Field(description="Version of flint")
    blisstomo_version: str = Field(description="Version of bliss-tomo")
    fscan_version: str = Field(description="Version of fscan")
    blisswebui_version: str = Field(description="Version of blisswebui")
