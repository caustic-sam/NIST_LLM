from collections.abc import Sequence
from numbers import Number, Integral

try:
    from types import EllipsisType

    SingleDataIndexType = Integral | slice | Sequence | EllipsisType
except ImportError:
    SingleDataIndexType = Integral | slice | Sequence
from numpy.typing import ArrayLike

DataType = bytes | Number | ArrayLike
DataIndexType = SingleDataIndexType | tuple[SingleDataIndexType]
