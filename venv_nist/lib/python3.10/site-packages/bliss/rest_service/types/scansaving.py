from __future__ import annotations
import logging
from typing import Optional

from bliss.rest_service.typedef import (
    ObjectType,
    HardwareSchema,
    Field,
    CallableSchema,
    EmptyCallable,
)

logger = logging.getLogger(__name__)


class _PropertiesSchema(HardwareSchema):
    base_path: str
    data_path: str
    root_path: str
    filename: str

    beamline: Optional[str] = Field(
        None, description="For bliss_basic, the beamline name"
    )
    template: Optional[str] = Field(
        None, description="For bliss_basic, the saving `template`"
    )
    data_filename: Optional[str] = Field(
        None, description="For bliss_basic, the saving `data_filename`"
    )

    proposal_name: Optional[str] = Field(
        None, description="For bliss_esrf, the proposal name"
    )
    proposal_session_name: Optional[str] = Field(
        None, description="For bliss_esrf, the proposal session name"
    )
    collection_name: Optional[str] = Field(
        None, description="For bliss_esrf, the collection name"
    )
    dataset_name: Optional[str] = Field(
        None, description="For bliss_esrf, the dataset name"
    )

    # Additional metadata
    dataset_definition: Optional[str] = Field(
        None,
        description="For bliss_esrf, the dataset definition (usually the technique)",
    )
    sample_notes: Optional[str] = Field(
        None,
        description="For bliss_esrf, sample notes (used to store tags and other metadata)",
    )
    sample_name: Optional[str] = Field(
        None, description="For bliss_esrf, the sample name"
    )
    sample_description: Optional[str] = Field(
        None, description="For bliss_esrf, the sample description"
    )


class _CallablesSchema(CallableSchema):
    create_root_path: EmptyCallable


class ScanSavingType(ObjectType):
    NAME = "scansaving"

    PROPERTIES = _PropertiesSchema
    CALLABLES = _CallablesSchema


Default = ScanSavingType
