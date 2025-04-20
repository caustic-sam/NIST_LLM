# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.
import numpy as np
from numpy.typing import DTypeLike
from collections.abc import Sequence
from blissdata.streams.base import Stream, BaseStream, StreamDefinition
from blissdata.lima.client import Lima2Client
from blissdata.streams.lima import LimaView, LimaStream
from blissdata.redis_engine.encoding.json import JsonStreamEncoder


class Lima2View(LimaView):
    """Lima2View is currently the same as LimaView, but later it may have extra
    features like sparse image getter"""

    pass


class Lima2Stream(LimaStream):
    def __init__(self, event_stream):
        # Lima2Stream is not exactly a daugther of LimaStream class, but a twin
        # sister that will take a different path soon with sparse images.
        # For ease of writing it inherit LimaStream to copy all of its methods
        # until it differs from it.
        BaseStream.__init__(self, event_stream)
        self._client = Lima2Client(**event_stream.info["lima_info"])
        self._cursor = Stream(event_stream).cursor()

    @staticmethod
    def make_definition(
        name: str,
        dtype: DTypeLike,
        shape: Sequence,
        lima_info: dict = {},
        info: dict = {},
    ) -> StreamDefinition:
        info = info.copy()

        # legacy format for blissdata<2.0 readers
        info["format"] = "lima_v2"
        # new format
        info["plugin"] = "lima2"

        info["dtype"] = np.dtype(dtype).name
        info["shape"] = shape

        # TODO should later be added to make_definition args instead of using asserts
        assert "server_urls" in lima_info
        assert "name" in lima_info
        if "file_format" in lima_info:
            assert "frame_per_acquisition" in lima_info
            assert "frame_per_file" in lima_info
            assert "path_template" in lima_info
            assert "data_path" in lima_info
            assert "directory" in lima_info
            assert "rank" in lima_info
            assert "suffix" in lima_info
            assert "prefix" in lima_info

        info["lima_info"] = lima_info.copy()
        info["lima_info"]["protocol_version"] = Lima2Client.PROTOCOL_VERSION

        return StreamDefinition(name, info, JsonStreamEncoder())

    @property
    def plugin(self):
        return "lima2"
