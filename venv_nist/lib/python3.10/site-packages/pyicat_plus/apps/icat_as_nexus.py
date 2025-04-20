import sys
import logging
import argparse
import datetime
from typing import Mapping, Union, Optional

import h5py
import numpy

from ..utils.log_utils import basic_config
from ..metadata.nexus import create_nxtreedict
from ..metadata.definitions import load_icat_fields

logger = logging.getLogger(__name__)


def save_icat_as_nexus(filename: str, url: Optional[str] = None) -> None:
    icat_fields = load_icat_fields(url=url)

    metadict = _example_icat_metadata_as_dict(icat_fields)
    nxtreedict = create_nxtreedict(
        metadict, icat_fields=icat_fields, add_icat_attrs=True
    )

    with h5py.File(filename, "w") as nxroot:
        nxroot.attrs["NX_class"] = "NXroot"
        _dicttonx("entryname", nxtreedict, nxroot)


def _example_icat_metadata_as_dict(icat_fields) -> dict:
    """Example ICAT field values are generated based on the data type."""
    now = datetime.datetime.now()

    values = {
        "NX_CHAR": lambda name: name,
        "NX_DATE_TIME": lambda name: (
            now + datetime.timedelta(minutes=len(name))
        ).isoformat(),
        "NX_FLOAT64": lambda name: numpy.float64(len(name) + 0.001),
        "NX_FLOAT": lambda name: numpy.float32(len(name) + 0.1),
        "NX_BOOLEAN": lambda name: True if len(name) % 2 == 0 else False,
        "NX_INT": lambda name: numpy.int32(len(name) + 1),
    }

    metadict = dict()
    for field in icat_fields.iter_fields():
        value_gen = values[field.nxtype]
        field_name = field.field_name
        metadict[field_name] = value_gen(field_name)
    return metadict


def _dicttonx(
    name: str, value: Union[Mapping, str, float], h5group: h5py.Group
) -> None:
    if "@" in name:
        assert isinstance(value, str), f"Attribute {name} must be a string"
        dsetname, _, attrname = name.rpartition("@")
        if dsetname:
            h5item = h5group[dsetname]
        else:
            h5item = h5group
        h5item.attrs[attrname] = value
        return
    assert "@" not in name, f"Item {name} cannot have an '@' character"

    if isinstance(value, Mapping):
        h5group = h5group.create_group(name)
        for name, value in value.items():
            _dicttonx(name, value, h5group)
    else:
        h5group[name] = value


def main(argv=None):
    basic_config(logger=logger, level=logging.DEBUG, format="%(message)s")

    if argv is None:
        argv = sys.argv

    parser = argparse.ArgumentParser(
        description="Save ICAT definitions in a NeXus compliant HDF5 file"
    )
    parser.add_argument(
        "--filename",
        type=str,
        required=False,
        default="icat.h5",
        help="File name to store the ICAT definitions",
    )
    parser.add_argument(
        "--url", type=str, required=False, help="URL of the ICAT definitions"
    )

    args = parser.parse_args(argv[1:])

    save_icat_as_nexus(args.filename, args.url)
    logger.info(f"NeXus compliant HDF5 file created: '{args.filename}'")


if __name__ == "__main__":
    sys.exit(main())
