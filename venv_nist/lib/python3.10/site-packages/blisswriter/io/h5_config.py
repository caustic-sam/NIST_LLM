"""HDF5 configuration of optimal data storage (IO speed, compression, ...)
"""

from typing import Optional, Tuple
from numbers import Integral
from collections.abc import Mapping

import numpy
from numpy.typing import DTypeLike

try:
    import hdf5plugin
except ImportError:
    hdf5plugin = None

from ..utils.array_size import ShapeType
from ..utils import array_size

VarShapeType = Tuple[array_size.Integral]  # <=0 marks a variable dimension
VarH5pyShapeType = Tuple[
    Optional[array_size.StrictPositiveIntegral]
]  # None marks a variable dimension

DEFAULT_CHUNK_NBYTES = 1 << 20
DEFAULT_COMPRESSION_LIMIT_NBYTES = 1 << 20
DEFAULT_CHUNK_SPLIT = 4
DEFAULT_COMPRESSION_SCHEME = "gzip-byteshuffle"

# Default data size is 2 MB for a 1D detector
# and 2 KB for a 0D detector:
DEFAULT_SCAN_DIM_SIZE = 512
DEFAULT_DETECTOR_DIM_SIZE = 1024
DEFAULT_DTYPE = numpy.int32


def guess_data_shape(
    scan_shape: VarShapeType,
    detector_shape: VarShapeType,
    max_shape: Optional[VarH5pyShapeType],
) -> ShapeType:
    scan_shape = tuple(n if n > 0 else DEFAULT_SCAN_DIM_SIZE for n in scan_shape)
    detector_shape = tuple(
        n if n > 0 else DEFAULT_SCAN_DIM_SIZE for n in detector_shape
    )
    data_shape = scan_shape + detector_shape
    if max_shape:
        assert len(max_shape) == len(
            data_shape
        ), "HDF5 dataset shape must have the same dimensions as maxshape"
        data_shape = tuple(
            n1 if not n2 else max(n1, n2) for n1, n2 in zip(data_shape, max_shape)
        )
    return data_shape


def guess_chunk_shape(
    data_shape: ShapeType,
    dtype: DTypeLike,
    chunk_split: Optional[Integral] = None,
    chunk_nbytes: Optional[Integral] = None,
) -> Optional[ShapeType]:
    """Try to guess the optimal chunk shape with these constraints:
    * Split any dimension for partial access
    * Below the maximal chunk size (1 MB by default, uncompressed)

    The inner-most dimensions are split in `chunk_split` parts
    until chunk_nbytes is reached. The chunk size in the outer
    dimensions will be 1, unless the data size is to small.
    """
    if chunk_nbytes is None:
        chunk_nbytes = DEFAULT_CHUNK_NBYTES
    if chunk_split is None:
        chunk_split = DEFAULT_CHUNK_SPLIT
    itemsize = array_size.dtype_nbytes(dtype)
    size = array_size.shape_to_size(data_shape)
    nbytes = size * itemsize
    if nbytes <= chunk_nbytes:
        return None

    max_size = chunk_nbytes // itemsize
    current_size = 1
    chunk_shape = []
    for n_i in data_shape[-1::-1]:
        if current_size >= max_size:
            c_i = 1
        else:
            a = int(numpy.ceil(n_i / chunk_split))
            b = int(numpy.ceil(max_size / current_size))
            c_i = min(a, b)
        chunk_shape.append(c_i)
        current_size *= c_i
    chunk_shape = tuple(chunk_shape[::-1])
    if chunk_shape == data_shape:
        return None
    return chunk_shape


def guess_compression(
    data_shape: ShapeType,
    dtype: DTypeLike,
    compression_limit_nbytes: Optional[Integral] = None,
) -> bool:
    """Compression is needed when the total data size exceeds the limits (1 MB by default)."""
    if compression_limit_nbytes is None:
        compression_limit_nbytes = DEFAULT_COMPRESSION_LIMIT_NBYTES
    nbytes = array_size.shape_to_nbytes(data_shape, dtype)
    return nbytes > compression_limit_nbytes


def get_compression_arguments(compression_scheme: Optional[str] = None) -> Mapping:
    if compression_scheme:
        compression_scheme = compression_scheme.lower()
    if compression_scheme is None:
        compression_scheme = DEFAULT_COMPRESSION_SCHEME
    if compression_scheme == "none":
        return dict()
    elif compression_scheme == "gzip":
        return {"compression": "gzip"}
    elif compression_scheme == "byteshuffle":
        return {"shuffle": True}
    elif compression_scheme == "gzip-byteshuffle":
        return {"compression": "gzip", "shuffle": True}
    elif compression_scheme == "bitshuffle":
        if hdf5plugin is None:
            raise RuntimeError(
                "Writer does not support HDF5 'bitshuffle' compression. Install the hdf5plugin library"
            )
        return hdf5plugin.Bitshuffle(nelems=0, lz4=False)
    elif compression_scheme == "lz4-bitshuffle":
        if hdf5plugin is None:
            raise RuntimeError(
                "Writer does not support HDF5 'bitshuffle' compression. Install the hdf5plugin library"
            )
        return hdf5plugin.Bitshuffle(nelems=0, lz4=True)
    else:
        raise ValueError(f"Unknown HDF5 compression '{compression_scheme}'")


def guess_dataset_config(
    scan_shape: VarShapeType,
    detector_shape: VarShapeType,
    dtype: Optional[DTypeLike] = None,
    chunk_split: Optional[Integral] = None,
    chunk_nbytes: Optional[Integral] = None,
    compression_limit_nbytes: Optional[Integral] = None,
    compression_scheme: Optional[str] = None,
    max_shape: Optional[VarH5pyShapeType] = None,
) -> dict:
    """Dataset configuration passed the `h5py.Group.create_dataset` for optimal
    storage (IO speed, compression, ...)
    """
    data_shape = guess_data_shape(
        scan_shape=scan_shape, detector_shape=detector_shape, max_shape=max_shape
    )
    if dtype is None:
        dtype = DEFAULT_DTYPE
    chunk_shape = guess_chunk_shape(
        data_shape=data_shape,
        dtype=dtype,
        chunk_split=chunk_split,
        chunk_nbytes=chunk_nbytes,
    )
    config = {"chunks": chunk_shape}
    compression = guess_compression(
        data_shape=data_shape,
        dtype=dtype,
        compression_limit_nbytes=compression_limit_nbytes,
    )
    if compression:
        config.update(get_compression_arguments(compression_scheme=compression_scheme))
    chunking_required = compression or max_shape is not None
    if chunking_required and chunk_shape is None:
        # Do not let h5py guess the chunk size
        config["chunks"] = data_shape
    return config
