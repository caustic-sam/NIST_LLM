from typing import Tuple, NewType
from numbers import Integral

import numpy
from numpy.typing import DTypeLike

PositiveIntegral = NewType("PositiveIntegral", Integral)  # >= 0
StrictPositiveIntegral = NewType("StrictPositiveIntegral", Integral)  # > 0
ShapeType = Tuple[StrictPositiveIntegral]
SizeType = PositiveIntegral


def dtype_nbytes(dtype: DTypeLike) -> int:
    return numpy.dtype(dtype).itemsize


def shape_to_size(shape: ShapeType) -> int:
    return numpy.prod(shape, dtype=int)


def shape_to_nbytes(shape: ShapeType, dtype: DTypeLike) -> int:
    return shape_to_size(shape) * dtype_nbytes(dtype)


def format_bytes(size: SizeType) -> str:
    power = 1 << 10
    n = 0
    power_labels = {0: "B", 1: "KB", 2: "MB", 3: "GB", 4: "TB"}
    while size >= power and n < 4:
        size /= power
        n += 1
    return "{:.01f}{}".format(size, power_labels[n])
