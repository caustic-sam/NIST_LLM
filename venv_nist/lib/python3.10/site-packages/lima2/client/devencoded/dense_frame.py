# This file is part of the Lima2 project
#
# Copyright (c) 2020-2024 Beamline Control Unit, ESRF
# Distributed under the MIT license. See LICENSE for more info.

"""Utility functions to decode Lima data from device server.
"""

import enum
import struct
from dataclasses import dataclass

import numpy
from typing_extensions import Union

from .exception import DevEncodedFormatNotSupported

DATA_HEADER_FORMAT = "<IHHIIHHHHHHHHIIIIIIQ"
DATA_MAGIC = struct.unpack(">I", b"DTAY")[0]
DATA_HEADER_SIZE = struct.calcsize(DATA_HEADER_FORMAT)

EncodedFrame = Union[bytes, tuple[str, bytes]]


class IMAGE_MODES(enum.Enum):
    DARRAY_UINT8 = 0
    DARRAY_UINT16 = 1
    DARRAY_UINT32 = 2
    DARRAY_UINT64 = 3
    DARRAY_INT8 = 4
    DARRAY_INT16 = 5
    DARRAY_INT32 = 6
    DARRAY_INT64 = 7
    DARRAY_FLOAT32 = 8
    DARRAY_FLOAT64 = 9


# Mapping used for direct conversion from raw data to numpy array
MODE_TO_NUMPY = {
    IMAGE_MODES.DARRAY_UINT8: numpy.uint8,
    IMAGE_MODES.DARRAY_UINT16: numpy.uint16,
    IMAGE_MODES.DARRAY_UINT32: numpy.uint32,
    IMAGE_MODES.DARRAY_UINT64: numpy.uint64,
    IMAGE_MODES.DARRAY_INT8: numpy.int8,
    IMAGE_MODES.DARRAY_INT16: numpy.int16,
    IMAGE_MODES.DARRAY_INT32: numpy.int32,
    IMAGE_MODES.DARRAY_INT64: numpy.int64,
    IMAGE_MODES.DARRAY_FLOAT32: numpy.float32,
    IMAGE_MODES.DARRAY_FLOAT64: numpy.float64,
}


@dataclass
class Frame:
    """
    Provide data frame from Lima including few metadata
    """

    data: numpy.ndarray
    """Data of the frame"""

    idx: int
    """Index of the frame where 0 is the first frame"""


def decode(raw_data: EncodedFrame) -> Frame:
    """Decode data provided by Lima device image attribute

    See https://lima1.readthedocs.io/en/latest/applications/tango/python/doc/#devencoded-data-array

    Argument:
        raw_data: Data returns by Lima image attribute

    Returns:
        A numpy array else None if there is not yet acquired image.

    Raises:
        DevEncodedFormatNotSupported: when the retrieved data is not supported
    """

    if isinstance(raw_data, tuple):
        # Support the direct output from proxy.readImage
        if raw_data[0] not in ["DATA_ARRAY", "DENSE_FRAME"]:
            raise DevEncodedFormatNotSupported(
                "Data type DENSE_FRAME expected (found %s)." % raw_data[0]
            )
        raw_data = raw_data[1]

    (
        magic,
        version,
        header_size,
        _category,
        data_type,
        endianness,
        nb_dim,
        dim1,
        dim2,
        dim3,
        _dim4,
        _dim5,
        _dim6,
        _dim_step1,
        _dim_step2,
        _dim_step3,
        _dim_step4,
        _dim_step5,
        _dim_step6,
        frame_idx,
    ) = struct.unpack_from(DATA_HEADER_FORMAT, raw_data)

    if magic != DATA_MAGIC:
        raise DevEncodedFormatNotSupported(
            "Magic header not supported (found 0x%x)." % magic
        )

    # Assume backward-compatible incremental versioning
    if version < 1:
        raise DevEncodedFormatNotSupported(
            "Image header version not supported (found %s)." % version
        )

    try:
        mode = IMAGE_MODES(data_type)
    except Exception:
        raise DevEncodedFormatNotSupported(
            "Image format from Lima Tango device not supported (found %s)." % data_type
        )
    if endianness != 0:
        raise DevEncodedFormatNotSupported(
            "Unsupported endianness (found %s)." % endianness
        )

    if nb_dim != 3:
        raise DevEncodedFormatNotSupported(
            "Image header nb_dim==3 expected (found %s)." % nb_dim
        )

    try:
        dtype = MODE_TO_NUMPY[mode]
    except Exception:
        raise DevEncodedFormatNotSupported("Data format %s is not supported" % mode)

    data = numpy.frombuffer(raw_data, offset=header_size, dtype=dtype)
    data.shape = dim3, dim2, dim1

    # Create a memory copy only if it is needed
    if not data.flags.writeable:
        data = numpy.array(data)

    return Frame(data, frame_idx)
