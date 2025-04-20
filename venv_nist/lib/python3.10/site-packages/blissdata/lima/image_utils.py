# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

"""Utility functions to read Lima images from file or device server.
"""
import struct
import numpy
import typing
import enum
import fabio
import h5py
from collections import abc

try:
    import cv2
except ImportError:
    cv2 = None

DATA_HEADER_FORMAT = "<IHH"
DATA_MAGIC = struct.unpack(">I", b"DTAY")[0]
DATA_HEADER_SIZE = struct.calcsize(DATA_HEADER_FORMAT)

DATA_HEADER_FORMAT_V1 = "<IIHHHHHHHHIIIIIIII"
DATA_HEADER_FORMAT_V3 = "<IIHHHHHHHHIIIIIIQ"
DATA_HEADER_FORMAT_V4 = "<IIHHHHHHHHIIIIIIQQII"

VIDEO_HEADER_FORMAT = "!IHHqiiHHHH"
VIDEO_MAGIC = struct.unpack(">I", b"VDEO")[0]
VIDEO_HEADER_SIZE = struct.calcsize(VIDEO_HEADER_FORMAT)


class ImageData(typing.NamedTuple):
    """Decoded frame from Lima.

    This object is returned by both decoding of image and video API.
    """

    array: numpy.ndarray
    """Numpy array containing decoded data of this frame"""

    frame_id: int | None
    """Number of the frame (0=first frame) in a sequence of frames.

    For oldest version of Lima, this information is not part of the frame.
    In this case it is set to `None`.

    From the video API, this information is always available.

    This is a frame in Lima acquisition point of view.
    For some configuration BLISS can reconfigure Lima during a scan, which result in a reset of this frame id.
    """

    acq_tag: int | None
    """Tag stored by the control system to the Lima detector.

    For old Lima version, this information is not part of the frame.
    In this case it is set to `None`.

    From the video API, this information is never available (always `None`).
    """


class VIDEO_MODES(enum.Enum):
    # From https://github.com/esrf-bliss/Lima/blob/master/common/include/lima/Constants.h#L118
    Y8 = 0
    Y16 = 1
    Y32 = 2
    Y64 = 3
    RGB555 = 4
    RGB565 = 5
    RGB24 = 6
    RGB32 = 7
    BGR24 = 8
    BGR32 = 9
    BAYER_RG8 = 10
    BAYER_RG16 = 11
    BAYER_BG8 = 12
    BAYER_BG16 = 13
    I420 = 14
    YUV411 = 15
    YUV422 = 16
    YUV444 = 17
    YUV411PACKED = 18
    YUV422PACKED = 19
    YUV444PACKED = 20


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
    VIDEO_MODES.Y8: numpy.uint8,
    VIDEO_MODES.Y16: numpy.uint16,
    VIDEO_MODES.Y32: numpy.int32,
    VIDEO_MODES.Y64: numpy.int64,
}


class RgbCodec(typing.NamedTuple):
    opencv_code: int | None
    """OpenCV identifier for the transformation"""
    compute_input_shape: abc.Callable
    """Callable to convert from output image shape to input raw shape"""
    input_dtype: type
    """dtype for the input data"""
    post_scale: int
    """bit scale to normalize the output to 8 bits"""


_RGB_CODECS: dict[VIDEO_MODES | IMAGE_MODES, RgbCodec] = {}
_RGB_CODECS[VIDEO_MODES.RGB24] = RgbCodec(None, lambda w, h: (h, w, 3), numpy.uint8, 0)
_RGB_CODECS[VIDEO_MODES.RGB32] = RgbCodec(None, lambda w, h: (h, w, 4), numpy.uint8, 0)

if cv2:
    _RGB_CODECS[VIDEO_MODES.YUV422PACKED] = RgbCodec(
        cv2.COLOR_YUV2RGB_Y422, lambda w, h: (h, w, 2), numpy.uint8, 0
    )
    _RGB_CODECS[VIDEO_MODES.I420] = RgbCodec(
        cv2.COLOR_YUV2RGB_I420, lambda w, h: (h + h // 2, w), numpy.uint8, 0
    )
    _RGB_CODECS[VIDEO_MODES.BGR24] = RgbCodec(
        cv2.COLOR_BGR2RGB, lambda w, h: (h, w, 3), numpy.uint8, 0
    )
    _RGB_CODECS[VIDEO_MODES.BAYER_BG16] = RgbCodec(
        cv2.COLOR_BayerRG2RGB, lambda w, h: (h, w), numpy.uint16, 12
    )
    _RGB_CODECS[VIDEO_MODES.BAYER_BG8] = RgbCodec(
        cv2.COLOR_BayerRG2RGB, lambda w, h: (h, w), numpy.uint8, 0
    )
    _RGB_CODECS[VIDEO_MODES.BAYER_RG16] = RgbCodec(
        cv2.COLOR_BayerRG2BGR, lambda w, h: (h, w), numpy.uint16, 12
    )
    _RGB_CODECS[VIDEO_MODES.BAYER_RG8] = RgbCodec(
        cv2.COLOR_BayerRG2BGR, lambda w, h: (h, w), numpy.uint8, 0
    )


class ImageFormatNotSupported(Exception):
    """Raised when the RAW data from a Lima device can't be decoded as a grey
    scale or RGB numpy array."""


class NoImageAvailable(RuntimeError):
    """Raised when an image but the server dont yet have any frame

    This can happen when a frame is requested after `prepareAcq` and before the
    first acquisition.
    """


class Frame(typing.NamedTuple):
    """
    Provide data frame from Lima including few metadata
    """

    data: numpy.ndarray
    """Data of the frame"""

    frame_number: int | None
    """Number of the frame. Can be None. 0 is the first frame"""

    source: str
    """Source of the data. Can be "video", "file", or "memory"
    """

    def __bool__(self) -> bool:
        """Return true is this frame is not None

        Helper for compatibility. This have to be removed. The API should return
        `None` when there is nothing, and not return an empty tuple.

        ..note:: 2020-02-27: This have to be removed at one point
        """
        return self.data is not None

    def __iter__(self):
        """Mimick a 2-tuple, for compatibility with the previous version.

        ..note:: 2020-02-27: This have to be removed at one point
        """
        yield self[0]
        yield self[1]


def decode_devencoded_video(raw_data: bytes | tuple[str, bytes]) -> ImageData | None:
    """Decode data provided by Lima device video attribute.

    See https://lima1.readthedocs.io/en/latest/applications/tango/python/doc/#devencoded-video-image

    Argument:
        raw_data: Data returns by Lima video attribute

    Returns:
        A tuple with the frame data (as a numpy array), and the frame number
        if an image is available. None if there is not yet acquired image.

    Raises:
        ImageFormatNotSupported: when the retrieved data is not supported
    """
    if isinstance(raw_data, tuple):
        # Support the direct output from proxy.video_last_image
        if raw_data[0] != "VIDEO_IMAGE":
            raise ImageFormatNotSupported(
                "Data type VIDEO_IMAGE expected (found %s)." % raw_data[0]
            )
        raw_data = raw_data[1]

    if len(raw_data) < VIDEO_HEADER_SIZE:
        raise ImageFormatNotSupported("Image header smaller than the expected size")

    (
        magic,
        header_version,
        image_mode,
        image_frame_number,
        image_width,
        image_height,
        endian,
        header_size,
        _pad0,
        _pad1,
    ) = struct.unpack_from(VIDEO_HEADER_FORMAT, raw_data)

    if magic != VIDEO_MAGIC:
        raise ImageFormatNotSupported(
            "Magic header not supported (found 0x%x)." % magic
        )

    if header_version != 1:
        raise ImageFormatNotSupported(
            "Image header version not supported (found %s)." % header_version
        )
    if image_frame_number == -1:
        return None

    if endian != 0:
        raise ImageFormatNotSupported(
            "Decoding video frame from this Lima device is "
            "not supported by bliss cause of the endianness (found %s)." % endian
        )

    try:
        mode = VIDEO_MODES(image_mode)
    except Exception:
        raise ImageFormatNotSupported(
            "Video format unsupported (found %s)." % image_mode
        )

    if mode in MODE_TO_NUMPY:
        dtype = MODE_TO_NUMPY[mode]
        data = numpy.frombuffer(raw_data, offset=header_size, dtype=dtype).copy()
        data.shape = image_height, image_width
    elif mode in _RGB_CODECS:
        data = decode_rgb_data(
            raw_data, image_width, image_height, mode, offset=header_size
        )
    else:
        raise ImageFormatNotSupported(f"Video format {mode} is not supported")

    return ImageData(data, frame_id=image_frame_number, acq_tag=None)


def decode_devencoded_image(raw_data: bytes | tuple[str, bytes]) -> ImageData:
    """Decode data provided by Lima device image attribute

    See https://lima1.readthedocs.io/en/latest/applications/tango/python/doc/#devencoded-data-array

    Argument:
        raw_data: Data returns by Lima image attribute

    Returns:
        An ImageData

    Raises:
        ImageFormatNotSupported: when the retrieved data is not supported
    """
    if isinstance(raw_data, tuple):
        # Support the direct output from proxy.readImage
        if raw_data[0] != "DATA_ARRAY":
            raise ImageFormatNotSupported(
                "Data type DATA_ARRAY expected (found %s)." % raw_data[0]
            )
        raw_data = raw_data[1]

    (
        magic,
        version,
        header_size,
    ) = struct.unpack_from(DATA_HEADER_FORMAT, raw_data)

    if magic != DATA_MAGIC:
        raise ImageFormatNotSupported(
            "Magic header not supported (found 0x%x)." % magic
        )

    # Assume backward-compatible incremental versioning
    if version < 1:
        raise ImageFormatNotSupported(
            "Image header version not supported (found %s)." % version
        )

    if version < 3:
        (
            _category,
            data_type,
            endianness,
            nb_dim,
            dim1,
            dim2,
            _dim3,
            _dim4,
            _dim5,
            _dim6,
            _dim_step1,
            _dim_step2,
            _dim_step3,
            _dim_step4,
            _dim_step5,
            _dim_step6,
            _dim_step7,
            _dim_step8,
        ) = struct.unpack_from(DATA_HEADER_FORMAT_V1, raw_data, DATA_HEADER_SIZE)
        frame_id = None
        acq_tag = None
    elif version == 3:
        (
            _category,
            data_type,
            endianness,
            nb_dim,
            dim1,
            dim2,
            _dim3,
            _dim4,
            _dim5,
            _dim6,
            _dim_step1,
            _dim_step2,
            _dim_step3,
            _dim_step4,
            _dim_step5,
            _dim_step6,
            frame_id,
        ) = struct.unpack_from(DATA_HEADER_FORMAT_V3, raw_data, DATA_HEADER_SIZE)
        acq_tag = None
    elif version >= 4:
        (
            _category,
            data_type,
            endianness,
            nb_dim,
            dim1,
            dim2,
            _dim3,
            _dim4,
            _dim5,
            _dim6,
            _dim_step1,
            _dim_step2,
            _dim_step3,
            _dim_step4,
            _dim_step5,
            _dim_step6,
            frame_id,
            acq_tag,
            _unused1,
            _unused2,
        ) = struct.unpack_from(DATA_HEADER_FORMAT_V4, raw_data, DATA_HEADER_SIZE)

    try:
        mode = IMAGE_MODES(data_type)
    except Exception:
        raise ImageFormatNotSupported(
            "Image format from Lima Tango device not supported (found %s)." % data_type
        )
    if endianness != 0:
        raise ImageFormatNotSupported("Unsupported endianness (found %s)." % endianness)

    if nb_dim != 2:
        raise ImageFormatNotSupported(
            "Image header nb_dim==2 expected (found %s)." % nb_dim
        )

    try:
        dtype = MODE_TO_NUMPY[mode]
    except Exception:
        raise ImageFormatNotSupported("Data format %s is not supported" % mode)

    data = numpy.frombuffer(raw_data, offset=header_size, dtype=dtype)
    data.shape = dim2, dim1

    # Create a memory copy only if it is needed
    if not data.flags.writeable:
        data = numpy.array(data)

    return ImageData(data, frame_id=frame_id, acq_tag=acq_tag)


def decode_rgb_data(
    raw_data: bytes,
    width: int,
    height: int,
    mode: VIDEO_MODES | IMAGE_MODES,
    offset: int = 0,
) -> numpy.ndarray:
    """
    Decode an encoded raw data into numpy array.

    Arguments:
        raw_data: Encoded raw data
        offset: Location of the data in the buffer
        width: width of the output image
        height: height of the output image
        mode: LimaCDD video mode
    """
    codec = _RGB_CODECS.get(mode, None)
    if codec is None:
        raise ValueError(f"Video mode {mode} not supported yet.")

    if codec.compute_input_shape is not None:
        shape = codec.compute_input_shape(width, height)
    else:
        shape = height, width

    if len(raw_data) - offset <= 0:
        raise ValueError("Inconsistancy between offset and raw_data size.")

    # Create a view, without memory copy if possible
    npbuf: numpy.ndarray = numpy.frombuffer(
        raw_data, offset=offset, dtype=codec.input_dtype
    )
    npbuf.shape = shape

    if codec.opencv_code is not None:
        npbuf = cv2.cvtColor(npbuf, codec.opencv_code)
    if npbuf.ndim == 3 and npbuf.itemsize > 1 and codec.post_scale != 0:
        in_bits = codec.post_scale
        if in_bits is None:
            in_bits = 8 * npbuf.dtype.itemsize
        shift = in_bits - 8
        if shift > 0:
            npbuf = numpy.right_shift(npbuf, shift).astype(numpy.uint8)

    # Create a memory copy only if it is needed
    if not npbuf.flags.writeable:
        npbuf = numpy.array(npbuf)

    return npbuf


def read_video_last_image(proxy) -> ImageData | None:
    """Read and decode video last image from a Lima detector

    Argument:
        proxy: A Tango Lima proxy

    Returns:
        A tuple with the frame data (as a numpy array), and the frame number
        if an image is available. None if there is not yet acquired image.

    Raises:
        ImageFormatNotSupported: when the retrieved data is not supported
    """
    raw_msg = proxy.video_last_image
    decoded = decode_devencoded_video(raw_msg)
    return decoded


def image_from_server(proxy, image_index: int) -> ImageData:
    """Read and decode image (or last image ready) from a Lima detector.

    Argument:
        proxy: A Tango Lima proxy
        image_index: The image index related to Lima acquisition to decode,
            or -1 to use the last index (last_image_ready).

    Returns:
        The frame data (as an ImageData)

    Raises:
        NoImageAvailable: when the lima server buffer does not yet contain any frame
        ImageFormatNotSupported: when the retrieved data is not supported
    """
    try:
        raw_msg = proxy.readImage(image_index)
    except Exception as e:
        try:
            # NOTE: Dont trust this parsing, it's a tango exception
            no_data_available = "not available yet" in e.args[0].desc
        except Exception:
            pass
        else:
            if no_data_available:
                raise NoImageAvailable()
        raise RuntimeError("Error while reading image")
    data = decode_devencoded_image(raw_msg)
    return data


def image_from_file(filename, path_in_file, image_index, file_format):
    """
    :param str filename:
    :param str path_in_file:
    :param int image_index: for multi-frame formats
    :param str file_format: HDF5, HDF5BS, EDFLZ4, ...
                            This is not the file extension!
    """
    file_format = file_format.lower()
    if file_format.startswith("edf"):
        if file_format == "edfconcat":
            raise RuntimeError("EDFConcat format is no longer supported")
        with fabio.open(filename) as f:
            return f.get_frame(image_index).data
    elif file_format.startswith("hdf5"):
        with h5py.File(filename, mode="r") as f:
            dataset = f[path_in_file]
            return dataset[image_index]
    else:
        raise RuntimeError(f"{file_format} format not supported")
