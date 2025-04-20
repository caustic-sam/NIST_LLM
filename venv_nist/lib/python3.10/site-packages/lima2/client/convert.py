# This file is part of the Lima2 project
#
# Copyright (c) 2020-2024 Beamline Control Unit, ESRF
# Distributed under the MIT license. See LICENSE for more info.

"""Convertion routines.

Frame info is serialized as {'dimensions': {'x': 2068, 'y': 512}, 'nb_channels': 2, 'pixel_type': 'gray16'}
"""

import numpy as np

# convert from pixel enum type to numpy type
pixel_type_to_np_dtype = {
    "gray8s": np.int8,
    "gray8": np.uint8,
    "gray16s": np.int16,
    "gray16": np.uint16,
    "gray32s": np.int32,
    "gray32": np.uint32,
    "gray32f": np.float32,
    "gray64f": np.float64,
}


def frame_info_to_shape_dtype(frame_info):
    return dict(
        shape=(
            frame_info["nb_channels"],
            frame_info["dimensions"]["y"],
            frame_info["dimensions"]["x"],
        ),
        dtype=pixel_type_to_np_dtype[frame_info["pixel_type"]],
    )
