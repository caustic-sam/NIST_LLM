# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.
import logging
import sys
import redis
from functools import wraps
from dataclasses import dataclass

from .encoding import EncodedBatch
from .encoding.json import JsonStreamEncoder
from .encoding.numeric import NumericStreamEncoder
from .exceptions import (
    IndexNoMoreThereError,
    IndexNotYetThereError,
    IndexWontBeThereError,
    NoWritePermission,
    UnknownEncodingError,
)

_logger = logging.getLogger(__name__)

try:
    from gevent.monkey import is_anything_patched
except ImportError:
    use_gevent = False
else:
    use_gevent = is_anything_patched()

if use_gevent:
    from .sink import DualStageGeventSink as RedisSink

    # from .sink import SingleStageGeventSink as RedisSink
else:
    from .sink import DualStageThreadSink as RedisSink

    # from .sink import SingleStageThreadSink as RedisSink

_MAX_STREAM_ID = 2**64 - 1

# Enabling dataclass slots when python version allows
if sys.version_info >= (3, 10):
    dataclass_opt = {"slots": True}
else:
    dataclass_opt = {}


@dataclass(**dataclass_opt)
class StreamEntry:
    id: int
    length: int
    batch: EncodedBatch
    is_seal: bool

    @classmethod
    def from_raw(cls, raw):
        id = int(raw[0].split(b"-")[0])
        if id < _MAX_STREAM_ID:
            length = int(raw[1].get(b"len", 1))
            batch = EncodedBatch(raw[1][b"payload"], length)
            return cls(id=id, length=length, batch=batch, is_seal=False)
        else:
            # STREAM SEALING ENTRY
            # Don't use _MAX_STREAM_ID but get 'id' field instead,
            # so the following assert keeps true for any entry:
            #     total length = entry.id + entry.length
            id = int(raw[1][b"id"])
            return cls(id=id, length=0, batch=None, is_seal=True)


class EventStream:
    """EventStream objects are created in Read-Only or Read-Write mode. Two factory methods exist for this:
        - EventStream.open(...)
        - EventStream.create(...)

    For writing, it is very important to use a single RW instance per stream. Because each instance
    owns a socket, and writing to multiple sockets in parallel can't guarantee data ordering. You don't
    have to care when using a Scan object as it will handle EventStream instantiation for you.

    EventStream can be accessed like arrays, with index or slices, eg:
        my_stream[42]
        my_stream[20:203]
        my_stream[50:300:20]
        my_stream[-1] # be careful, this is only the current last, but it will likely change
        len(my_stream)

    EventStream objects are simple to use when picking values by index, but if you need to keep up with
    many streams in a running scan. Then you should use a CursorGroup which provides synchronous
    primitives, like a blocking read, on multiple streams at the same time (based on redis xread).
    """

    def __init__(self, name, model):
        self._name = name
        self._model = model
        self._seal = None

    def needs_write_permission(func):
        @wraps(func)
        def wrapper(self, *args, **kwargs):
            if self._write_permission:
                return func(self, *args, **kwargs)
            else:
                raise NoWritePermission(f"Stream {self.name} is read-only")

        return wrapper

    @property
    def name(self):
        return self._name

    @property
    def key(self):
        return self._model.key()

    @property
    def encoding(self):
        return self._model.encoding

    @property
    def dtype(self):
        return self._encoder.dtype

    @property
    def shape(self):
        return self._encoder.shape

    @property
    def info(self):
        return self._model.info

    def __len__(self):
        if self._seal is not None:
            return self._seal.id

        try:
            entry = self._revrange(count=1)[0]
            if entry.is_seal:
                # take the opportunity to save the seal
                self._seal = entry
        except IndexError:
            return 0
        return entry.length + entry.id

    def _range(self, max="+", min="-", count=None):
        raw = self._data_store._redis.xrange(self.key, min=min, max=max, count=count)
        if raw:
            return [StreamEntry.from_raw(item) for item in raw]
        else:
            return []

    def _revrange(self, max="+", min="-", count=None):
        raw = self._data_store._redis.xrevrange(self.key, min=min, max=max, count=count)
        if raw:
            return [StreamEntry.from_raw(item) for item in reversed(raw)]
        else:
            return []

    def _read_last(self, index):
        entries = self._revrange(min=index, count=1)
        if not entries:
            return index, [], False

        if entries[0].is_seal and entries[0].id > index:
            prev_entries = self._revrange(min=index, max=entries[0].id - 1, count=1)
            entries = prev_entries + entries

        index, data = self._decode_entries(entries)
        return index, data, entries[-1].is_seal

    def _read_next(self, index, timeout, count):
        raw = self._data_store._redis.xread(
            {self.key: index}, block=timeout, count=count
        )
        if not raw:
            return index, [], False
        else:
            entries = [StreamEntry.from_raw(raw_entry) for raw_entry in raw[0][1]]
            index, data = self._decode_entries(entries)
            return index, data, entries[-1].is_seal

    @staticmethod
    def _read_last_multi(stream_ids: dict["EventStream", int]):
        if not stream_ids:
            return {}

        data_store = list(stream_ids.keys())[0]._data_store

        # Read last entry of each stream (it's likely to be the seal)
        pipe = data_store._redis.pipeline(transaction=False)
        for stream, index in stream_ids.items():
            pipe.xrevrange(stream.key, min=index, count=1)
        raw = pipe.execute()

        output = {}
        entries = {}
        for (stream, index), raw_entries in zip(stream_ids.items(), raw):
            if raw_entries:
                entries[stream] = [StreamEntry.from_raw(raw_entries[0])]
            else:
                output[stream] = index, [], False

        # When a seal is found, we need to read one more entry to get the actual
        # last value. This doesn't cost much as it happens only once in a stream
        missing_value_streams = []
        pipe = data_store._redis.pipeline(transaction=False)
        for stream, stream_entries in list(entries.items()):
            if stream_entries[0].is_seal:
                index = stream_ids[stream]
                if stream_entries[0].id > index:
                    # there something we haven't yet read before the seal, go get it
                    pipe.xrevrange(
                        stream.key, min=index, max=stream_entries[0].id - 1, count=1
                    )
                    missing_value_streams.append(stream)
        raw = pipe.execute()

        for stream, raw_entries in zip(missing_value_streams, raw):
            if raw_entries:
                prev_stream_entries = [StreamEntry.from_raw(raw_entries[0])]
                entries[stream] = prev_stream_entries + entries[stream]

        for stream, stream_entries in entries.items():
            index, data = stream._decode_entries(stream_entries)
            output[stream] = index, data, stream_entries[-1].is_seal

        return output

    @staticmethod
    def _read_next_multi(
        stream_ids: dict["EventStream", int], timeout: int, count: int
    ):
        if not stream_ids:
            return {}

        data_store = list(stream_ids.keys())[0]._data_store

        # Read at most count entries on each stream
        raw = data_store._redis.xread(
            {stream.key: index for stream, index in stream_ids.items()},
            block=timeout,
            count=count,
        )
        entries = {
            raw_key.decode(): [
                StreamEntry.from_raw(raw_entry) for raw_entry in raw_entries
            ]
            for raw_key, raw_entries in raw
        }

        output = {}
        for stream, index in stream_ids.items():
            stream_entries = entries.get(stream.key, None)
            if stream_entries is None:
                output[stream] = index, [], False
            else:
                data_index, data = stream._decode_entries(stream_entries)
                output[stream] = data_index, data, stream_entries[-1].is_seal

        return output

    def is_sealed(self):
        # cache sealing info as it is final
        if self._seal is None:
            try:
                self._seal = self._revrange(min=_MAX_STREAM_ID)[0]
                assert self._seal.is_seal
            except IndexError:
                return False
        return True

    def wait_seal(self, timeout: float = 0.0) -> bool:
        if self._seal is not None:
            return True
        timeout_ms = int(timeout * 1000)
        raw = self._data_store._redis.xread(
            {self.key: f"{_MAX_STREAM_ID}-{_MAX_STREAM_ID-1}"}, block=timeout_ms
        )
        try:
            self._seal = StreamEntry.from_raw(raw[0][1][0])
            return True
        except IndexError:
            return False

    def __getitem__(self, key):
        if isinstance(key, int):
            return self._get_index(key)
        elif isinstance(key, slice):
            return self._get_slice(key.start, key.stop, key.step)
        else:
            raise TypeError(
                f"stream indices must be integers or slices, not {type(key).__name__}"
            )

    def _get_index(self, index):
        if index < 0:
            if self.is_sealed():
                index += self._seal.id
                if index < 0:
                    raise IndexError("index out of range")
            else:
                raise IndexNotYetThereError(
                    "Negative index have no meaning before stream is sealed"
                )

        # succeed fast...
        result = self._revrange(max=index, count=1)
        if result:
            entry = result[0]
            if entry.id + entry.length > index:
                decoded = self._encoder.decode([entry.batch])
                return decoded[index - entry.id]

        # ...or fail slow
        # In case the requested index is not returned directly, it is read again
        # along with the last entry of the stream in a transaction. This way we
        # have an instant view of the stream length and sealing to raise the
        # proper exception.
        pipe = self._data_store._redis.pipeline(transaction=True)
        pipe.xrevrange(self.key, max=index, count=1)
        pipe.xrevrange(self.key, count=1)
        results = pipe.execute()
        entry, last_entry = [
            StreamEntry.from_raw(raw[0]) if raw else None for raw in results
        ]

        if last_entry is None:
            # the stream does not even exist
            raise IndexNotYetThereError(f"Index {index} not yet published")

        if entry is None:
            stream_len = last_entry.length + last_entry.id
            # ex: in [0, 1] array, [-2, -1, 0, 1] are valid indexes
            if -stream_len <= index < stream_len:
                raise IndexNoMoreThereError(f"Index {index} have been trimmed off")
            elif last_entry.is_seal:
                raise IndexWontBeThereError(
                    f"Stream is closed, there will be no index {index}"
                )
            else:
                raise IndexNotYetThereError(f"Index {index} not yet published")

        if entry.id + entry.length <= index:
            if last_entry.is_seal:
                raise IndexWontBeThereError(
                    f"Stream is closed, there will be no index {index}"
                )
            else:
                raise IndexNotYetThereError(f"Index {index} not yet published")

        decoded = self._encoder.decode([entry.batch])
        return decoded[index - entry.id]

    def _get_slice(self, start, stop, step):
        if start is None:
            start = 0
        elif start < 0:
            if self.is_sealed():
                start = max(start + self._seal.id, 0)
            else:
                raise IndexNotYetThereError(
                    "Negative index have no meaning before stream is sealed"
                )

        if stop is None:
            stop = _MAX_STREAM_ID
        elif stop < 0:
            if self.is_sealed():
                stop = max(stop + self._seal.id, 0)
            else:
                raise IndexNotYetThereError(
                    "Negative index have no meaning before stream is sealed"
                )

        if stop <= start:
            # Return empty data but in the decoder format.
            # Could be an empty numpy array, but with a precise shape for example.
            return self._encoder.decode([])

        entries = self._range(min=start, max=stop - 1)

        # if our slice is not aligned with batches in redis, we may need to retrieve
        # one more batch at the beginning.
        if not entries or entries[0].id > start:
            try:
                prev_entry = self._revrange(max=start, count=1)[0]
            except IndexError:
                if start < len(self):
                    raise IndexNoMoreThereError(f"Index {start} have been trimmed off")
                else:
                    # exceptions are only raised when part of the slice is trimmed
                    # otherwise return empty data, just like numpy.arange(3)[10:20]
                    return self._encoder.decode([])

            entries.insert(0, prev_entry)

        batches = (entry.batch for entry in entries)
        data = self._encoder.decode(batches)

        first_recv_id = entries[0].id
        stop = None if stop is None else (stop - first_recv_id)
        return data[(start - first_recv_id) : stop : step]

    @needs_write_permission
    def send(self, data):
        try:
            batch = self._encoder.encode(data)
        except TypeError as e:
            raise TypeError(f"Encoding of '{self.name}': {e}")

        self._sink.xadd(
            name=self.key,
            fields=batch.todict(),
            id=f"{self._write_index}-1",
        )
        self._write_index += batch.len

    @needs_write_permission
    def join(self):
        self._sink.join()

    @needs_write_permission
    def seal(self):
        try:
            self._sink.stop()
        except redis.exceptions.ResponseError as e:
            if "ID specified in XADD is equal or smaller" in str(e):
                _logger.warning(
                    f"Cannot publish into stream '{self.name}' due to index discrepancy (it's very likely you're publishing it from multiple places)."
                )

        try:
            return self._data_store._redis.fcall("seal_stream", 1, self.key)
        except redis.exceptions.ResponseError as e:
            if "is already sealed" in str(e):
                return len(self)
            else:
                raise

    def __del__(self):
        # Thread based sinks can't be garbage collected, because their internal thread
        # still hold a reference. Then it is sink's owner responsibility to stop them.
        if hasattr(self, "_sink"):
            try:
                self._sink.stop()
            except Exception:
                # Errors raised by sinks are ignored at garbage collection time.
                pass

    @classmethod
    def open(cls, data_store, name, model):
        stream = cls(name, model)
        stream._data_store = data_store
        if stream.encoding["type"] == "numeric":
            stream._encoder = NumericStreamEncoder.from_info(stream.encoding)
        elif stream.encoding["type"] == "json":
            stream._encoder = JsonStreamEncoder.from_info(stream.encoding)
        else:
            raise UnknownEncodingError(f"Unknow stream encoding {stream.encoding}")

        stream._write_permission = False
        return stream

    @classmethod
    def create(cls, data_store, name, model):
        stream = cls.open(data_store, name, model)
        stream._sink = RedisSink(data_store)
        stream._write_index = 0
        stream._write_permission = True
        return stream

    def _decode_entries(self, entries: list[StreamEntry]):
        """entries shouldn't be empty"""
        if entries[-1].is_seal:
            self._seal = entries[-1]
            if len(entries) == 1:
                return entries[0].id, []
            else:
                entries = entries[:-1]
        batches = (entry.batch for entry in entries)
        data = self._encoder.decode(batches)
        return entries[0].id, data
