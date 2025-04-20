# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.
import sys
import numpy as np
from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from blissdata.redis_engine.event_stream import EventStream
from blissdata.redis_engine.exceptions import (
    EndOfStream,
    IndexNoMoreThereError,
    UnknownEncodingError,
    MissingPluginException,
    EmptyViewException,
)
from blissdata.redis_engine.encoding import StreamEncoder
from blissdata.redis_engine.encoding.numeric import NumericStreamEncoder
from blissdata.redis_engine.encoding.json import JsonStreamEncoder


# Enabling dataclass slots when python version allows
if sys.version_info >= (3, 10):
    dataclass_opt = {"slots": True}
else:
    dataclass_opt = {}


@dataclass(**dataclass_opt)
class StreamDefinition:
    name: str
    info: dict
    encoder: StreamEncoder


@dataclass(**dataclass_opt)
class EventRange:
    """
        |       length:6        |
        |  nb_expired:4 | data  |
    ----| 3 - 4 - 5 - 6 | 7 - 8 |->
          ^                       ^
        index               end of stream ?
    """

    index: int
    nb_expired: int
    data: Sequence
    end_of_stream: bool

    def __len__(self):
        return self.nb_expired + len(self.data)


class BaseStream(ABC):
    def __init__(self, event_stream):
        self._event_stream = event_stream

    @property
    @abstractmethod
    def kind(self):
        pass

    @staticmethod
    @abstractmethod
    def make_definition(*args, **kwargs) -> StreamDefinition:
        pass

    @property
    @abstractmethod
    def plugin(self):
        pass

    @property
    @abstractmethod
    def dtype(self):
        pass

    @property
    @abstractmethod
    def shape(self):
        pass

    @abstractmethod
    def __len__(self):
        pass

    @abstractmethod
    def __getitem__(self, key):
        pass

    def __iter__(self):
        cursor = self.cursor()
        while True:
            try:
                view = cursor.read()
            except EndOfStream:
                return
            else:
                yield from view.get_data()

    @abstractmethod
    def _need_last_only(self, last_only):
        pass

    @abstractmethod
    def _build_view_from_events(
        self, hl_index: int, events: EventRange, last_only: bool
    ):
        pass

    @property
    def name(self):
        return self._event_stream._name

    @property
    def key(self):
        return self._event_stream.key

    @property
    def info(self):
        return self._event_stream.info

    @property
    def event_stream(self):
        return self._event_stream

    def cursor(self):
        return Cursor(self)

    def send(self, data):
        """Can be overriden for format checking"""
        return self._event_stream.send(data)

    def join(self):
        return self._event_stream.join()

    def seal(self):
        return self._event_stream.seal()

    def is_sealed(self):
        return self._event_stream.is_sealed()

    def wait_seal(self, timeout: float = 0.0) -> bool:
        return self._event_stream.wait_seal(timeout)


class BaseView(ABC):
    """A view corresponds to an available portion of a stream. A Cursor produces
    a view to tell new content is available for its stream.
    The data of the corresponding portion can be retrieve entirely or partially
    with .get_data() method."""

    @property
    @abstractmethod
    def index(self) -> int:
        pass

    @property
    def last_index(self) -> int:
        return self.index + len(self) - 1

    @abstractmethod
    def __len__(self) -> int:
        pass

    @abstractmethod
    def get_data(self, start: int | None = None, stop: int | None = None):
        pass


class View(BaseView):
    def __init__(self, event_range: EventRange):
        self._events = event_range

    @property
    def index(self) -> int:
        return self._events.index

    def __len__(self) -> int:
        return len(self._events)

    def get_data(self, start=None, stop=None):
        trimmed_range = range(len(self))[start:stop]
        offset = self._events.nb_expired
        data_start = trimmed_range.start - offset
        data_stop = trimmed_range.stop - offset
        if data_start < 0:
            raise IndexNoMoreThereError
        else:
            return self._events.data[data_start:data_stop]


class Stream(BaseStream):
    def __init__(self, event_stream):
        super().__init__(event_stream)
        if event_stream.encoding["type"] == "numeric":
            self._kind = "array"
        elif event_stream.encoding["type"] == "json":
            self._kind = "json"
        else:
            raise UnknownEncodingError(
                f"Unknow stream encoding {event_stream.encoding}"
            )

    @staticmethod
    def make_definition(name, dtype, shape=None, info={}) -> StreamDefinition:
        if dtype == "json":
            if shape is None:
                return StreamDefinition(name, info, JsonStreamEncoder())
            else:
                raise ValueError("JSON stream cannot have shape")
        else:
            if shape is None:
                shape = ()
            info = info.copy()
            info["dtype"] = np.dtype(dtype).name
            info["shape"] = shape
            return StreamDefinition(name, info, NumericStreamEncoder(dtype, shape))

    @property
    def kind(self):
        return self._kind

    @property
    def plugin(self):
        None

    @property
    def dtype(self):
        return self.event_stream.dtype

    @property
    def shape(self):
        return self.event_stream.shape

    def __len__(self):
        return len(self._event_stream)

    def __getitem__(self, key):
        return self._event_stream[key]

    def _need_last_only(self, last_only):
        return last_only

    def _build_view_from_events(self, index, events: EventRange, last_only):
        # NOTE event_range is never empty
        return View(events)


class MissingPluginStream(BaseStream):
    def __init__(self, event_stream, plugin_name):
        self._event_stream = event_stream
        self._plugin_name = plugin_name

    @property
    def kind(self):
        "missing_plugin"

    @staticmethod
    def make_definition(*args, **kwargs) -> StreamDefinition:
        raise NotImplementedError

    @property
    def plugin(self):
        return self._plugin_name

    def __len__(self):
        raise MissingPluginException(self.event_stream.name, self._plugin_name)

    def __getitem__(self, key):
        raise MissingPluginException(self.event_stream.name, self._plugin_name)

    def _need_last_only(self, last_only):
        raise MissingPluginException(self.event_stream.name, self._plugin_name)

    def _build_view_from_events(
        self, hl_index: int, events: EventRange, last_only: bool
    ):
        raise MissingPluginException(self.event_stream.name, self._plugin_name)


class Cursor:
    def __init__(self, stream):
        # low level stream (multiplexable events from redis)
        self._ll_stream: EventStream = stream.event_stream
        self._ll_index = 0
        self._ll_eos = False

        # high level stream (interpreting events into data)
        self._hl_stream = stream
        self._hl_index = 0
        self._hl_eos = False

        self._data_store = stream.event_stream._data_store

    @property
    def position(self):
        return self._hl_index

    def read(
        self, block: bool = True, timeout: float = 0, last_only: bool = False
    ) -> BaseView | None:
        assert timeout >= 0

        if self._hl_eos:
            raise EndOfStream

        if self._hl_stream._need_last_only(last_only):
            event_range = self._read_last_event()
            view = self._create_data_view(event_range, last_only)

            if view is not None:
                return view
            elif not block:
                return None

        if not block:
            timeout = None
        else:
            timeout = int(timeout * 1000)

        while True:
            # use arbitrary count number of chunks to prevent huge requests
            # (Redis has to allocate them to build a response !)
            event_range = self._read_next_events(timeout, 100, last_only)
            view = self._create_data_view(event_range, last_only)

            # avoid returning None on ever blocking call:
            #   if no view was produced by the events we received, then wait
            #   next events until a view is created or EOS reached
            if view is not None or timeout != 0 or self._ll_eos:
                break

        if view is None and self._ll_eos:
            raise EndOfStream
        else:
            return view

    def _read_last_event(self):
        if self._ll_eos:
            return EventRange(self._ll_index, 0, [], True)

        data_index, data, eos = self._ll_stream._read_last(self._ll_index)
        # truncate data as it can be a chunk
        data_index += max(0, len(data) - 1)
        data = data[-1:]

        if eos and not len(data) and data_index != self._ll_index:
            expected_index = data_index - 1
            nb_expired = 1
        else:
            expected_index = data_index
            nb_expired = 0

        self._ll_index = data_index + len(data)
        self._ll_eos = eos

        return EventRange(expected_index, nb_expired, data, eos)

    def _read_next_events(self, timeout, count, last_only):
        if self._ll_eos:
            return EventRange(self._ll_index, 0, [], True)

        data_index, data, eos = self._ll_stream._read_next(
            self._ll_index, timeout, count
        )

        if last_only:
            # truncate data as it can be a chunk
            data_index += max(0, len(data) - 1)
            data = data[-1:]
            if eos and not len(data) and data_index != self._ll_index:
                # got only seal, but found discrepancy -> last index is missing
                expected_index = data_index - 1
                nb_expired = 1
            else:
                # got seal, but data comes along with the last point or there is no discrepancy
                expected_index = data_index
                nb_expired = 0
        else:
            expected_index = self._ll_index
            nb_expired = data_index - expected_index

        self._ll_index = data_index + len(data)
        self._ll_eos = eos

        return EventRange(expected_index, nb_expired, data, eos)

    def _create_data_view(self, events: EventRange, last_only: bool) -> BaseView | None:
        if len(events):
            try:
                view = self._hl_stream._build_view_from_events(
                    self._hl_index, events, last_only
                )
                self._hl_index = view.index + len(view)
                return view
            except EmptyViewException:
                return None
        elif events.end_of_stream:
            raise EndOfStream
        else:
            return None


class CursorGroup:
    """Synchronous client to read multiple streams at once.
        A CursorGroup is created from a list of streams and keeps an index for each of them.
        Indexes are initialized to the origin of each stream.
    cursor
        Calls to .read() will get data from the beginning of each stream, but you can skip past
        data with:
            _ = client.read(block=False, count=-1)
        This will read only the last available entry of each stream, updating the indexes
        accordingly.
    """

    def __init__(self, streams):
        if isinstance(streams, Mapping):
            streams = streams.values()

        data_store_set = {stream.event_stream._data_store for stream in streams}
        if len(data_store_set) > 1:
            raise NotImplementedError(
                "CursorGroup cannot read streams from different data stores."
            )
        try:
            self._data_store = data_store_set.pop()
        except KeyError:
            self._data_store = None

        # cursors are active as long as the hl_streams are not EndOfStream
        # (ll_streams may already have reached EOS)
        self._active_cursors = {stream.key: Cursor(stream) for stream in streams}
        self._dead_cursors = {}

    @property
    def position(self):
        active = {
            cursor._hl_stream: cursor.position
            for cursor in self._active_cursors.values()
        }
        inactive = {
            cursor._hl_stream: cursor.position for cursor in self._dead_cursors.values()
        }
        return active | inactive

    def read(
        self, block: bool = True, timeout: float = 0, last_only: bool = False
    ) -> dict[BaseStream, BaseView]:
        assert timeout >= 0

        if not self._active_cursors:
            raise EndOfStream("All streams have been read until the end")

        cursors_read_last = {}
        cursors_read_next = {}
        last_only_keys = set()
        for key, cursor in self._active_cursors.items():
            if cursor._hl_stream._need_last_only(last_only):
                last_only_keys.add(key)
                cursors_read_last[key] = cursor
            else:
                cursors_read_next[key] = cursor

        event_ranges = self._read_last_event_multi(cursors_read_last)
        views = self._create_data_views(event_ranges, last_only)

        if not views and block:
            cursors_read_next = self._active_cursors
        else:
            # we already have something to return, we can't wait now
            block = False

        if cursors_read_next:
            if not block:
                timeout = None
            else:
                timeout = int(timeout * 1000)

            # arbitrary count number of chunks to prevent huge requests (Redis has to allocate them !)
            event_ranges = self._read_next_events_multi(
                cursors_read_next, timeout, 100, last_only_keys
            )
            views.update(self._create_data_views(event_ranges, last_only))

        if not views and not self._active_cursors:
            raise EndOfStream("All streams have been read until the end")
        else:
            return views

    def _create_data_views(
        self, event_ranges: dict[str, EventRange], last_only: bool
    ) -> dict[BaseStream, BaseView]:
        views = {}
        for key, event_range in event_ranges.items():
            cursor = self._active_cursors[key]
            if len(event_range):
                try:
                    view = cursor._hl_stream._build_view_from_events(
                        cursor._hl_index, event_range, last_only
                    )
                except EmptyViewException:
                    view = None
                else:
                    views[cursor._hl_stream] = view
                    cursor._hl_index = view.index + len(view)
            else:
                view = None

            if view is None and event_range.end_of_stream:
                self._dead_cursors[key] = cursor
                del self._active_cursors[key]
        return views

    def _prepare_stream_ids(self, cursors):
        output = {}
        stream_ids = {}
        for key, cursor in cursors.items():
            if not cursor._ll_eos:
                stream_ids[cursor._ll_stream] = cursor._ll_index
            else:
                output[key] = EventRange(cursor._ll_index, 0, [], True)
        return stream_ids, output

    def _read_last_event_multi(self, cursors):
        """Read last value from each low-level stream.
        When the last value is a seal, then read the value just before. If this
        value itself is expired, then return no data but tell that one was expected
        (thanks to EventRange)."""
        stream_ids, output = self._prepare_stream_ids(cursors)

        # output[stream] = data, index, eos
        readout = EventStream._read_last_multi(stream_ids)

        for ll_stream, (data_index, data, eos) in readout.items():
            key = ll_stream.key
            cursor = cursors[key]  # TODO store cursors per stream not per key ?
            # truncate data as it can be a chunk
            data_index += max(0, len(data) - 1)
            data = data[-1:]
            if eos and not len(data) and data_index != cursor._ll_index:
                expected_index = data_index - 1
                nb_expired = 1
            else:
                expected_index = data_index
                nb_expired = 0

            cursor._ll_index = data_index + len(data)
            cursor._ll_eos = eos

            output[key] = EventRange(expected_index, nb_expired, data, eos)

        return output

    def _read_next_events_multi(self, cursors, timeout, count, last_only_keys):
        stream_ids, output = self._prepare_stream_ids(cursors)

        # output[stream] = data, index, eos
        readout = EventStream._read_next_multi(stream_ids, timeout, count)

        for ll_stream, (data_index, data, eos) in readout.items():
            key = ll_stream.key
            cursor = cursors[key]  # TODO store cursors per stream not per key ?
            if key in last_only_keys:
                # truncate data as it can be a chunk
                data_index += max(0, len(data) - 1)
                data = data[-1:]
                if eos and not len(data) and data_index != cursor._ll_index:
                    # got only seal, but found discrepancy -> last index is missing
                    expected_index = data_index - 1
                    nb_expired = 1
                else:
                    # got seal, but data comes along with the last point or there is no discrepancy
                    expected_index = data_index
                    nb_expired = 0
            else:
                expected_index = cursor._ll_index
                nb_expired = data_index - expected_index

            cursor._ll_index = data_index + len(data)
            cursor._ll_eos = eos

            output[key] = EventRange(expected_index, nb_expired, data, eos)

        return output
