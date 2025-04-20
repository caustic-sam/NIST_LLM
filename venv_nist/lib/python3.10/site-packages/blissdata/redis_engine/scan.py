# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.
import enum
import redis
import json
import math
import logging
import numpy as np
from functools import wraps

from redis_om.model import model
from pydantic import ValidationError
from importlib.metadata import entry_points

from .event_stream import EventStream
from .exceptions import (
    UnauthorizeStateTransition,
    ScanNotFoundError,
    ScanValidationError,
    NoWritePermission,
)
from blissdata.streams.base import (
    BaseStream,
    Stream,
    StreamDefinition,
    MissingPluginStream,
)
from blissdata.streams.hdf5_fallback import Hdf5BackedStream

logger = logging.getLogger(__name__)

discovered_plugins = {entry.name: entry for entry in entry_points(group="blissdata")}
loaded_plugins = {}


scan_creation_stream = "_SCAN_HISTORY_"


class ScanState(enum.IntEnum):
    """A scan can only evolve to a state with a strictly greater order.
    This allows to wait for a state to be over, without enumerating all the next possible cases.
    """

    CREATED = 0
    PREPARED = 1
    STARTED = 2
    STOPPED = 3
    CLOSED = 4


def add_property(inst, name, getter, setter=None, deleter=None):
    cls = type(inst)
    module = cls.__module__
    if not hasattr(cls, "__perinstance"):
        cls = type(cls.__name__, (cls,), {})
        cls.__perinstance = True
        cls.__module__ = module
        inst.__class__ = cls
    setattr(cls, name, property(getter, setter, deleter))


class Scan:
    def needs_write_permission(func):
        @wraps(func)
        def wrapper(self, *args, **kwargs):
            if self._write_permission:
                return func(self, *args, **kwargs)
            else:
                raise NoWritePermission(f"Scan {self.key} is read-only")

        return wrapper

    @classmethod
    def _load(cls, data_store, key):
        scan = cls()
        scan._write_permission = False
        scan._data_store = data_store

        prefix = scan._data_store._scan_model.make_key("")
        if not key.startswith(prefix):
            raise RuntimeError(f"Scan key should be prefixed by '{prefix}'")

        id_model = cls._get_identity_model_cls(scan)
        cls._expose_identity_model_fields(scan, id_model)

        scan._streams = {}
        scan._last_entry_id = b"0-0"
        try:
            # pk is just unprefixed version of key
            pk = key[len(prefix) :]
            scan._model = scan._data_store._scan_model.get(pk)
        except model.NotFoundError as e:
            raise ScanNotFoundError(
                "Scan has been deleted from Redis, or key is wrong"
            ) from e
        except ValidationError as e:
            raise ScanValidationError(
                "Scan exists in Redis but is invalid, most likely the scan model version on the publisher side is different"
            ) from e
        else:
            scan._state = ScanState.CREATED
            scan.update(block=False)

        return scan

    @classmethod
    def _create(cls, data_store, identity, info={}):
        scan = cls()
        scan._write_permission = True
        scan._data_store = data_store

        id_model = cls._get_identity_model_cls(scan)
        scan._model = scan._data_store._scan_model(
            id=id_model(**identity),
            info=info,
            state_stream=scan._data_store._stream_model(),
            data_streams={},
        )
        cls._expose_identity_model_fields(scan, id_model)

        scan._streams = {}
        scan._writer_streams = {}
        scan._state = ScanState.CREATED

        scan._model.info = Scan._filter_nan_values(scan._model.info)

        def _create_scan(pipe: redis.client.Pipeline) -> None:
            scan._model.save(pipeline=pipe)
            pipe.xadd(scan_creation_stream, {"key": scan.key}, maxlen=2048)
            pipe.xadd(scan._model.state_stream.key(), {"state": scan.state.value})

        scan._data_store._redis.transaction(_create_scan)
        scan.json_info = ""  # TODO to be removed, used to check info modification between state transitions
        return scan

    @staticmethod
    def _get_identity_model_cls(scan):
        """Get the scan identity class."""
        # pydantic v2
        #  id.field: <class 'pydantic.fields.FieldInfo'>
        #  id.field.annotation: <class 'redis_om.model.model.ModelMeta'>
        #
        # pydantic v1
        #  id.field:  <class 'pydantic.fields.ModelField'>
        #  id.field.annotation: <class 'redis_om.model.model.ModelMeta'>
        return scan._data_store._scan_model.id.field.annotation

    @staticmethod
    def _expose_identity_model_fields(scan, id_model):
        """Expose scan identity fields as properties of the scan instance."""
        # id_model: <class 'redis_om.model.model.ModelMeta'>
        #
        #  pydantic v2: id_model.model_fields or id_model.__fields__
        #  pydantic v1: id_model.__fields__
        try:
            prop_names = list(id_model.model_fields)
        except AttributeError:
            prop_names = list(id_model.__fields__)
        for prop_name in prop_names:
            if prop_name == "pk":
                continue

            def get_id_field(self, field=prop_name):
                return getattr(self._model.id, field, None)

            add_property(scan, prop_name, get_id_field)

    @classmethod
    def _load_rw(cls, data_store, key):
        scan = Scan._load(data_store, key)
        scan._write_permission = True
        scan._writer_streams = {}
        scan.json_info = ""
        return scan

    @staticmethod
    def _filter_nan_values(obj):
        # json_constant_map = {
        #     "-Infinity": float("-Infinity"),
        #     "Infinity": float("Infinity"),
        #     "NaN": None,
        # }

        class NumpyEncoder(json.JSONEncoder):
            def default(self, o):
                if isinstance(o, np.ndarray):
                    return o.tolist()
                elif isinstance(o, np.number):
                    return o.item()
                else:
                    return json.JSONEncoder.default(self, o)

        def format_bytes(nbytes):
            suffixes = ["B", "KB", "MB", "GB", "TB", "PB"]
            exp = int(math.log(nbytes, 1024))
            return f"{nbytes/1024**exp:.4g}{suffixes[exp]}"

        # Inspired from https://stackoverflow.com/a/65317610
        # Only solution found to replace NaN values with null, which is valid in JSON.
        # Other solutions imply new dependency or overriding methods which are not
        # supposed to be in json module.
        def json_nan_to_none(obj):
            json_string = NumpyEncoder().encode(obj)
            json_size = len(json_string)
            if json_size > 2**20:
                raise RuntimeError(
                    f"Scan JSON metadata is taking {format_bytes(json_size)} (limit 1MB)"
                )
            return json.loads(json_string, parse_constant=lambda constant: None)
            # OR to define specific value for each constant
            # return json.loads(json_string, parse_constant=lambda constant: json_constant_map[constant])

        return json_nan_to_none(obj)

    @property
    def key(self):
        return self._model.key()

    @property
    def info(self):
        return self._model.info

    @info.setter
    @needs_write_permission
    def info(self, info):
        self._model.info = info

    @property
    def state(self):
        return self._state

    @property
    def streams(self):
        return self._streams.copy()

    def _refresh_streams(self):
        if self.state >= ScanState.PREPARED:
            # instanciate Stream objects for models which not already have one
            for name in self._model.data_streams.keys() - self._streams.keys():
                model = self._model.data_streams[name]
                event_stream = EventStream.open(self._data_store, name, model)
                self._streams[name] = self._wrap_event_stream(event_stream)

    def __str__(self):
        return f'{type(self).__name__}(key:"{self.key}")'

    def update(self, block=True, timeout=0) -> bool:
        """Update scan state and its content.
        If the scan is already in a terminal state, False is returned immediately.
        Otherwise it depends on 'block' and 'timeout' in seconds (timeout=0: wait forever).
        Return a True if the scan state has changed.
        Raise ScanNotFoundError if the scan is deleted."""
        # Updating a scan in RW mode makes no sense, there should be one writer only, so he never needs to read.
        assert not self._write_permission

        # Ensure scan is not deleted, neither it is soon to be
        # -1: scan has no planned expiration
        # -2: scan already expired
        #  n: scan expire in n seconds
        ttl = self._data_store._redis.ttl(self.key)
        if ttl != -1 and ttl <= 10:
            raise ScanNotFoundError("Scan has been deleted from Redis")

        if self.state == ScanState.CLOSED:
            return False

        if not block:
            timeout = None
        else:
            timeout = int(timeout * 1000)

        # Because of expiration time, the scan can't have disappeared after the check we made at the beginning of
        # this function. Therefore scan.state_stream exists and we won't get stucked on a non-existing stream.
        result = self._data_store._redis.xread(
            {self._model.state_stream.key(): self._last_entry_id}, block=timeout
        )
        if not result:
            if timeout == 0:
                raise RuntimeError(
                    "Redis blocking XREAD returned empty value, this is very unexpected !"
                )
            else:
                return False

        # Entries contain state, only last one is meaningful
        last_entry = result[0][1][-1]
        self._last_entry_id = last_entry[0].decode()
        self._state = ScanState(int(last_entry[1][b"state"]))

        # refresh json local copy on state change
        try:
            self._model = self._data_store._scan_model.get(self._model.pk)
        except model.NotFoundError as e:
            raise ScanNotFoundError("Scan has been deleted from Redis") from e
        except ValidationError as e:
            raise ScanValidationError(
                "Scan exists in Redis but is invalid, most likely the scan model version on the publisher side is different"
            ) from e

        self._refresh_streams()
        return True

    def _wrap_event_stream(self, event_stream):
        plugin_name = None
        if "plugin" not in event_stream.info:
            # map legacy blissdata to plugins
            if event_stream.encoding["type"] == "json":
                format = event_stream.info.get("format", "")
                if format == "lima_v1":
                    plugin_name = "lima"
                elif format == "lima_v2":
                    plugin_name = "lima2"
                elif format == "subscan":
                    plugin_name = "scan_sequence"
            elif self.info.get("save", False) and "data_path" in event_stream.info:
                # Old file backed stream where not self contained as the depended
                # on scan.path to locate their file. Need to be resolved manually.
                return Hdf5BackedStream(event_stream, self.path)
        else:
            plugin_name = event_stream.info["plugin"]

        if plugin_name is None:
            return Stream(event_stream)
        else:
            try:
                plugin = loaded_plugins[plugin_name]
            except KeyError:
                try:
                    plugin_entry = discovered_plugins[plugin_name]
                except KeyError:
                    # plugin is missing, but don't raise immediately to
                    # allow clients to access the rest of the scan.
                    # Return a broken stream object that will raise if used.
                    logger.warning(
                        f"Missing blissdata plugin '{plugin_name}', cannot load stream '{event_stream.name}'"
                    )
                    return MissingPluginStream(event_stream, plugin_name)
                else:
                    plugin = plugin_entry.load()
                    loaded_plugins[plugin_name] = plugin

            return plugin.stream_cls(event_stream)

    @needs_write_permission
    def create_stream(self, stream_definition: StreamDefinition) -> BaseStream:
        name = stream_definition.name
        encoder = stream_definition.encoder
        info = stream_definition.info
        if name in self._model.data_streams.keys():
            raise RuntimeError(f'Stream "{name}" already exists.')
        model = self._data_store._stream_model(encoding=encoder.info(), info=info)
        event_stream = EventStream.create(self._data_store, name, model)

        stream = self._wrap_event_stream(event_stream)
        self._model.data_streams[name] = model
        self._writer_streams[name] = stream
        return stream

    def get_writer_stream(self, name: str):
        """Load a Stream in read-write mode from an already prepared Scan.
        Intended use is to distribute data publication into multiple processes.

        IMPORTANT: Streams from a single Scan can be published from several
        places, but each single stream must be published from a single place (if
        ever it happens, Redis will not allow due to inconsistent indices).

        Stream sealing on the other hand, can be done multiple times, from
        multiple places."""
        model = self._model.data_streams[name]
        rw_event_stream = EventStream.create(self._data_store, name, model)
        return self._wrap_event_stream(rw_event_stream)

    @needs_write_permission
    def _close_writer_streams(self):
        """Seal streams that are not sealed yet.
        In the case of multiple processes/threads writing to the scan's streams,
        it is each writer's responsibility to seal its stream. Then the scan owner
        can wait for streams to be sealed and close the scan smoothly.
        Eventually, it may timeout and force the closure of all streams, making
        the still running writers to fail.
        """
        for writer_stream in self._writer_streams.values():
            writer_stream.seal()
        self._writer_streams = {}

    @needs_write_permission
    def prepare(self):
        if self.state is ScanState.CREATED:
            self._set_state(ScanState.PREPARED)
        else:
            raise UnauthorizeStateTransition(self.state, ScanState.PREPARED)

    @needs_write_permission
    def start(self):
        if self.state is ScanState.PREPARED:
            self._set_state(ScanState.STARTED)
        else:
            raise UnauthorizeStateTransition(self.state, ScanState.STARTED)

    @needs_write_permission
    def stop(self):
        if self.state is ScanState.STARTED:
            self._close_writer_streams()
            self._set_state(ScanState.STOPPED)
        else:
            raise UnauthorizeStateTransition(self.state, ScanState.STOPPED)

    @needs_write_permission
    def close(self):
        self._close_writer_streams()
        self._set_state(ScanState.CLOSED)

    @needs_write_permission
    def _set_state(self, state):
        prev_state = self._state
        self._state = state

        self._model.info = Scan._filter_nan_values(self._model.info)

        def update_scan_state(pipe: redis.client.Pipeline) -> None:
            json_info = json.dumps(self._model.info)

            if json_info != self.json_info:
                assert (
                    prev_state is ScanState.CREATED and state is ScanState.PREPARED
                ) or state is ScanState.CLOSED, f"Scan info changed between states {ScanState(prev_state).name} and {ScanState(state).name}"
            self.json_info = json_info

            self._model.save(pipeline=pipe)
            pipe.xadd(self._model.state_stream.key(), {"state": self.state.value})

        self._data_store._redis.transaction(update_scan_state)
        self._refresh_streams()
