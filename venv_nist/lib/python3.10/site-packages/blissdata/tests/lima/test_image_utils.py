# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

import pytest
import numpy
from blissdata.lima import image_utils

try:
    import cv2
except ImportError:
    cv2 = None


RAW_IMAGE_V2 = b"YATD\x02\x00@\x00\x02\x00\x00\x00\x02\x00\x00\x00\x00\x00\x02\
\x00\x02\x00\x02\x00\x00\x00\x00\x00\x00\x00\x00\x00\x04\x00\x00\x00\x08\
\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\
\x00\x00\x00\x00\x00\x00\x00\x00\x00o'\x00\x00\xba\x1d\x00\x00\xdc$\x00\
\x00\x89\x1c\x00\x00"

RAW_IMAGE_V3 = b"YATD\x03\x00@\x00\x02\x00\x00\x00\x02\x00\x00\x00\x00\x00\x02\
\x00\x02\x00\x02\x00\x00\x00\x00\x00\x00\x00\x00\x00\x04\x00\x00\x00\x08\
\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\
\x00\x01\x00\x00\x00\x00\x00\x00\x00o'\x00\x00\xba\x1d\x00\x00\xdc$\x00\
\x00\x89\x1c\x00\x00"

RAW_IMAGE_V4 = b"YATD\x04\x00P\x00\x02\x00\x00\x00\x00\x00\x00\x00\x00\x00\x02\
\x00\x02\x00\x02\x00\x00\x00\x00\x00\x00\x00\x00\x00\x01\x00\x00\x00\x02\x00\
\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\
\x00\x00\x00\x00\x00\x00\x00\xff\xff\xff\xff\x00\x00\x00\x00\x00\x00\x00\x00\
\x00\x00\x00\x00\x00\x00\x00\x00"

OLD_RAW_IMAGE = b"YATD\x00\x00@\x00\x02\x00\x00\x00\x02\x00\x00\x00\x00\x00\x02\
\x00\x02\x00\x02\x00\x00\x00\x00\x00\x00\x00\x00\x00\x04\x00\x00\x00\x08\
\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\
\x00\x00\x00\x00\x00\x00\x00\x00\x00o'\x00\x00\xba\x1d\x00\x00\xdc$\x00\
\x00\x89\x1c\x00\x00"

FAKE_VALID_RAW_IMAGE = (
    b"YATD\x10\x00\x50\x00\x02\x00\x00\x00\x02\x00\x00\x00\x00\x00\x02\
\x00\x02\x00\x02\x00\x00\x00\x00\x00\x00\x00\x00\x00\x04\x00\x00\x00\x08\
\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\
\x00\x00\x00\x00\x00\x00\x00\x00\x00################"
    + b"o'\x00\x00\xba\x1d\x00\x00\xdc$\x00\x00\x89\x1c\x00\x00"
)

RAW_VIDEO = b"VDEO\x00\x01\x00\x02\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\
\x00\x02\x00\x00\x00\x02\x00\x00\x00 \x00\x00\x00\x00o'\x00\x00\xba\x1d\x00\
\x00\xdc$\x00\x00\x89\x1c\x00\x00"

NO_RAW_VIDEO = b"VDEO\x00\x01\x00\x00\xff\xff\xff\xff\xff\xff\xff\xff\xff\
\xff\xff\xff\xff\xff\xff\xff\x00\x00\x00 \x00\x00\x00\x00"

RAW_YUV422PACKED_VIDEO = (
    b"VDEO\x00\x01\x00\x13\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\
\x00\x02\x00\x00\x00\x04\x00\x00\x00 \x00\x00\x00\x00"
    + b"[L\xffL6\x96\x00\x96\xef\x1dg\x1d\x80\x00\x80\x00"
)

RAW_RGB24_VIDEO = (
    b"VDEO\x00\x01\x00\x06\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\
\x00\x02\x00\x00\x00\x02\x00\x00\x00 \x00\x00\x00\x00"
    + b"\xFF\x00\x00\x00\xFF\x00\x00\x00\xFF\x00\x00\x00"
)

RAW_RGB32_VIDEO = (
    b"VDEO\x00\x01\x00\x07\x00\x00\x00\x00\x00\x00\x00\x00\
\x00\x00\x00\x02\x00\x00\x00\x02\x00\x00\x00 \x00\x00\x00\x00"
    + b"\xFF\x00\x00\x00\x00\xFF\x00\x00\x00\x00\xFF\x00\x00\x00\x00\xFF"
)

RAW_BAYER_RG16 = (
    b"VDEO\x00\x01\x00\x0b\x00\x00\x00\x00\x00\x05\x00\x00\
\x00\x00\x00\x04\x00\x00\x00\x04\x00\x00\x00 \x00\x00\x00\x00"
    + b"\x8f\x00\x80\x00\x8b\x00E\x00"
    + b"\x81\x00L\x00u\x00R\x00"
    + b"w\x00I\x00~\x00S\x00"
    + b"n\x00I\x00n\x00G\x00"
)


def test_decode_image_data_v2():
    """Test data from the result of Lima image attr"""
    image = image_utils.decode_devencoded_image(RAW_IMAGE_V2)
    assert image.array.shape == (2, 2)
    assert image.frame_id is None
    assert image.acq_tag is None


def test_decode_image_data_v3():
    """Test data from the result of Lima image attr"""
    image = image_utils.decode_devencoded_image(RAW_IMAGE_V3)
    assert image.array.shape == (2, 2)
    assert image.frame_id == 1
    assert image.acq_tag is None


def test_decode_image_data_v4():
    """Test data from the result of Lima image attr"""
    image = image_utils.decode_devencoded_image(RAW_IMAGE_V4)
    assert image.array.shape == (2, 2)
    assert image.frame_id == 0
    assert image.acq_tag == 4294967295


def test_decode_oldest_format_image_data():
    """Test that LIMA DATA version 0 is not supported"""
    with pytest.raises(image_utils.ImageFormatNotSupported):
        image_utils.decode_devencoded_image(OLD_RAW_IMAGE)


def test_decode_fake_valid_format_image_data():
    """Create a fake LIMA data format assuming backward-compatible incremental
    versioning.

    Make sure it is still working"""
    image = image_utils.decode_devencoded_image(FAKE_VALID_RAW_IMAGE)
    assert image.array.shape == (2, 2)


def test_decode_image_result():
    """Test the result of Lima image attr"""
    image = image_utils.decode_devencoded_image(("DATA_ARRAY", RAW_IMAGE_V2))
    assert image.array.shape == (2, 2)


def test_decode_video_data():
    """Test data from the result of Lima video attr"""
    frame = image_utils.decode_devencoded_video(RAW_VIDEO)
    assert frame is not None
    assert frame[0].shape == (2, 2)
    assert frame[1] == 0


def test_decode_video_without_available_data():
    """Test data from the result of Lima video attr"""
    frame = image_utils.decode_devencoded_video(NO_RAW_VIDEO)
    assert frame is None


def test_decode_video_result():
    """Test result of Lima video attr"""
    frame = image_utils.decode_devencoded_video(("VIDEO_IMAGE", RAW_VIDEO))
    assert frame is not None
    assert frame[0].shape == (2, 2)
    assert frame[1] == 0


@pytest.mark.skipif(not cv2, reason="OpenCV not available")
def test_decode_data_yuv422packed():
    encoded_image = b"[L\xffL6\x96\x00\x96\xef\x1dg\x1d\x80\x00\x80\x00"
    image = image_utils.decode_rgb_data(
        encoded_image, 2, 4, image_utils.VIDEO_MODES.YUV422PACKED
    )
    assert image.dtype == numpy.uint8
    assert image.shape == (4, 2, 3)
    assert image[0, 0].tolist() == pytest.approx([255, 0, 0], abs=20)
    assert image[1, 0].tolist() == pytest.approx([0, 255, 0], abs=20)
    assert image[2, 0].tolist() == pytest.approx([0, 0, 255], abs=20)
    assert image[3, 0].tolist() == pytest.approx([0, 0, 0], abs=20)


@pytest.mark.skipif(not cv2, reason="OpenCV not available")
def test_decode_video_yuv422packed():
    frame = image_utils.decode_devencoded_video(("VIDEO_IMAGE", RAW_YUV422PACKED_VIDEO))
    assert frame is not None
    image = frame[0]
    assert image.dtype == numpy.uint8
    assert image.shape == (4, 2, 3)
    assert image[0, 0].tolist() == pytest.approx([255, 0, 0], abs=20)
    assert image[1, 0].tolist() == pytest.approx([0, 255, 0], abs=20)
    assert image[2, 0].tolist() == pytest.approx([0, 0, 255], abs=20)
    assert image[3, 0].tolist() == pytest.approx([0, 0, 0], abs=20)


@pytest.mark.skipif(not cv2, reason="OpenCV not available")
def test_decode_video_rgb24():
    frame = image_utils.decode_devencoded_video(("VIDEO_IMAGE", RAW_RGB24_VIDEO))
    assert frame is not None
    image = frame[0]
    assert image.dtype == numpy.uint8
    assert image.shape == (2, 2, 3)
    assert image[0, 0].tolist() == pytest.approx([255, 0, 0], abs=20)
    assert image[0, 1].tolist() == pytest.approx([0, 255, 0], abs=20)
    assert image[1, 0].tolist() == pytest.approx([0, 0, 255], abs=20)
    assert image[1, 1].tolist() == pytest.approx([0, 0, 0], abs=20)


@pytest.mark.skipif(not cv2, reason="OpenCV not available")
def test_decode_video_rgb32():
    frame = image_utils.decode_devencoded_video(("VIDEO_IMAGE", RAW_RGB32_VIDEO))
    assert frame is not None
    image = frame[0]
    assert image.dtype == numpy.uint8
    assert image.shape == (2, 2, 4)
    assert image[0, 0].tolist() == pytest.approx([255, 0, 0, 0], abs=20)
    assert image[0, 1].tolist() == pytest.approx([0, 255, 0, 0], abs=20)
    assert image[1, 0].tolist() == pytest.approx([0, 0, 255, 0], abs=20)
    assert image[1, 1].tolist() == pytest.approx([0, 0, 0, 255], abs=20)


@pytest.mark.skipif(not cv2, reason="OpenCV not available")
def test_decode_bayer_rg16():
    frame = image_utils.decode_devencoded_video(("VIDEO_IMAGE", RAW_BAYER_RG16))
    assert frame is not None
    image = frame[0]
    assert image.dtype == numpy.uint8
    assert image.shape == (4, 4, 3)
    assert image[0, 0].tolist() == [8, 7, 4]
    assert image[3, 3].tolist() == [7, 6, 4]
