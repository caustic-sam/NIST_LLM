"""
Nexus structure from ICAT metadata
"""

from typing import Optional
from collections.abc import Iterable
import logging

from .definitions import load_icat_fields
from .definitions import IcatFieldGroup


logger = logging.getLogger(__name__)


def as_nxtype(value, nxtype):
    """Nexus convert to the Nexus type as defined in the ICAT definitions"""
    if nxtype is None:
        return value
    elif nxtype == "NX_CHAR":
        return value  # TODO: the ICAT definitions abuse NX_CHAR
        if isinstance(value, str):
            return value
        elif isinstance(value, bytes):
            return str(value)
        elif isinstance(value, Iterable):
            return [str(s) for s in value]
        else:
            return str(value)
    elif nxtype == "NX_DATE_TIME":
        if not isinstance(value, str):
            value = value.isoformat()
        return str(value)
    elif nxtype == "NX_INT":
        return int(value)
    elif nxtype == "NX_FLOAT":
        return float(value)
    else:
        return value


def create_nxtreedict(
    metadata: dict,
    icat_fields: Optional[IcatFieldGroup] = None,
    add_icat_attrs: bool = False,
):
    if icat_fields is None:
        icat_fields = load_icat_fields()
    nxtreedict = dict()
    for field_name, field_value in metadata.items():
        field = icat_fields.get_field_with_field_name(field_name)
        if field is None:
            logger.warning(f"{field_name} not a valid ICAT field")
            continue

        # Prepare parent groups
        group = icat_fields
        adict = nxtreedict
        if group.info.NX_class:
            adict["@NX_class"] = group.info.NX_class
        for name in field.parent:
            group = group[name]
            adict = adict.setdefault(name, dict())
            if group.info.NX_class:
                adict["@NX_class"] = group.info.NX_class

        # Add value
        adict[field.name] = as_nxtype(field_value, field.nxtype)
        if add_icat_attrs:
            adict[f"{field.name}@icat_field"] = field.field_name
        if field.units:
            adict[f"{field.name}@units"] = field.units

    return nxtreedict
