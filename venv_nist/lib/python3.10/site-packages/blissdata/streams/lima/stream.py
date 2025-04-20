# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.
import numpy as np
from numpy.typing import DTypeLike
from collections.abc import Sequence
from blissdata.streams.base import (
    Stream,
    BaseStream,
    BaseView,
    StreamDefinition,
    EventRange,
)
from blissdata.lima.client import LimaClient
from blissdata.redis_engine.encoding.json import JsonStreamEncoder
from blissdata.redis_engine.exceptions import (
    EndOfStream,
    IndexWontBeThereError,
    IndexNotYetThereError,
    IndexNoMoreThereError,
    EmptyViewException,
)


class LimaView(BaseView):
    def __init__(self, client, start, stop):
        self._client = client
        self._view_range = range(start, stop)

    @property
    def index(self):
        return self._view_range.start

    def __len__(self):
        return len(self._view_range)

    def get_data(self, start=None, stop=None):
        trimmed_range = self._view_range[start:stop]
        return self._client[trimmed_range.start : trimmed_range.stop]

    def get_references(self, start=None, stop=None):
        trimmed_range = self._view_range[start:stop]
        return self._client.get_references(
            slice(trimmed_range.start, trimmed_range.stop)
        )


class LimaStream(BaseStream):
    """Same API as a Stream but with a lima client inside to dereference events into images.
    Will support any Lima version as long as there is a client for it."""

    def __init__(self, event_stream):
        super().__init__(event_stream)
        self._client = LimaClient(
            event_stream._data_store, **event_stream.info["lima_info"]
        )
        self._cursor = Stream(event_stream).cursor()

    @staticmethod
    def make_definition(
        name: str,
        dtype: DTypeLike,
        shape: Sequence,
        server_url: str,
        buffer_max_number: int,
        frames_per_acquisition: int,
        acquisition_offset: int,
        saving: dict = {},
        info: dict = {},
    ) -> StreamDefinition:
        info = info.copy()

        # legacy format for blissdata<2.0 readers
        info["format"] = "lima_v1"
        # new format
        info["plugin"] = "lima"

        info["dtype"] = np.dtype(dtype).name
        info["shape"] = shape
        info["lima_info"] = {}
        info["lima_info"]["protocol_version"] = LimaClient.PROTOCOL_VERSION

        info["lima_info"]["server_url"] = server_url
        info["lima_info"]["buffer_max_number"] = buffer_max_number
        info["lima_info"]["frame_per_acquisition"] = frames_per_acquisition
        info["lima_info"]["acquisition_offset"] = acquisition_offset

        if saving:
            saving_keys = {
                "file_path",
                "data_path",
                "file_format",
                "file_offset",
                "frames_per_file",
            }
            missing_keys = saving_keys - saving.keys()
            extra_keys = saving.keys() - saving_keys
            if missing_keys:
                raise ValueError(
                    f"The following keys are missing from 'saving' dict: {missing_keys}"
                )
            if extra_keys:
                raise ValueError(
                    f"The following keys are not expected in 'saving' dict: {missing_keys}"
                )

            assert saving["file_path"] is not None

            info["lima_info"].update(saving)
            info["lima_info"]["frame_per_file"] = info["lima_info"].pop(
                "frames_per_file"
            )

        return StreamDefinition(name, info, JsonStreamEncoder())

    @property
    def kind(self):
        return "array"

    @property
    def plugin(self):
        return "lima"

    @property
    def dtype(self):
        return np.dtype(self.info["dtype"])

    @property
    def shape(self):
        return tuple(self.info["shape"])

    def __len__(self):
        self._update_client()
        return len(self._client)

    def _update_client(self):
        try:
            view = self._cursor.read(block=False, last_only=True)
        except EndOfStream:
            return
        if view is not None:
            last_status = view.get_data(-1, None)[0]
            self._client.update(**last_status)

    def __getitem__(self, key):
        if isinstance(key, slice):
            need_update = key.stop is None or not (0 <= key.stop < len(self._client))
        else:
            need_update = not (0 <= key < len(self._client))

        if need_update:
            self._update_client()

        try:
            return self._client[key]
        except IndexError:
            # TODO could be verified before asking the client
            index = key.start if isinstance(key, slice) else key
            if index is None:
                index = 0
            elif index < 0:
                index += len(self._client)

            if index >= len(self._client):
                if self._event_stream.is_sealed():
                    raise IndexWontBeThereError
                else:
                    raise IndexNotYetThereError
            else:
                raise IndexNoMoreThereError

    def _need_last_only(self, last_only):
        # lima use json stream as a status, last one is the only valuable status
        return True

    def _build_view_from_events(self, index: int, events: EventRange, last_only: bool):
        self._client.update(**events.data[-1])

        if len(self._client) <= index:
            # no new images despite client update
            raise EmptyViewException

        if last_only:
            start = len(self._client) - 1
        else:
            start = index
        return LimaView(self._client, start, len(self._client))

    def get_references(self, key):
        if isinstance(key, slice):
            need_update = key.stop is None or not (0 <= key.stop < len(self._client))
        else:
            need_update = not (0 <= key < len(self._client))

        if need_update:
            self._update_client()

        try:
            return self._client.get_references(key)
        except IndexError:
            # TODO could be verified before asking the client
            index = key.start if isinstance(key, slice) else key
            if index is None:
                index = 0
            elif index < 0:
                index += len(self._client)

            if index >= len(self._client):
                if self._event_stream.is_sealed():
                    raise IndexWontBeThereError
                else:
                    raise IndexNotYetThereError
            else:
                raise IndexNoMoreThereError

    def get_last_live_image(self):
        return self._client.get_last_live_image()
