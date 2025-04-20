# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

import redis
import time
import weakref
from numbers import Number
from redis.commands.search.query import Query
from redis.commands.search.querystring import querystring, tags
from redis_om import HashModel, JsonModel, Migrator

from .scan import Scan, scan_creation_stream, ScanState
from .exceptions import NoScanAvailable, ScanNotFoundError
from .identities import ESRFIdentityModel

_PROTOCOL_KEY = "_PROTOCOL_VERSION_"
_PROTOCOL_VERSION = 1


def reset_redis_db(data_store):
    """WARNING: This will drop all the data in Redis, before re-configuring.
    This is not part of the DataStore to prevent any accidental use."""
    data_store._redis.flushall()
    data_store._setup_redis_db()


class DataStore:
    def __init__(
        self,
        url,
        identity_model_cls=ESRFIdentityModel,
        init_db=False,
    ):
        self._url = url
        self._redis = redis.Redis.from_url(url)

        # define a finalizer to close all pool's sockets upon DataStore garbage collection
        redis_pool = self._redis.connection_pool
        close_func = type(redis_pool).disconnect
        weakref.finalize(self, close_func, redis_pool)

        class StreamModel(HashModel):
            """Actual stream model to be embedded into ScanModel, but not exposed to user directly.
            They are wrapped into Stream objects instead."""

            class Meta:
                global_key_prefix = identity_model_cls._meta.global_key_prefix
                model_key_prefix = "stream"
                database = (
                    identity_model_cls._meta.database
                )  # TODO use _UninitializedRedis directly

            # Accepts 'str' to stay compatible with older blissdata versions.
            # Because default values were not defined, an empty string was
            # stored instead of None.
            encoding: dict | str | None = None
            info: dict | str | None = None

        class ScanModel(JsonModel):
            class Meta:
                global_key_prefix = identity_model_cls._meta.global_key_prefix
                model_key_prefix = "scan"
                database = self._redis

            id: identity_model_cls
            info: dict
            state_stream: StreamModel
            data_streams: dict[str, StreamModel]

        self._stream_model = StreamModel
        self._scan_model = ScanModel

        if init_db:
            self._setup_redis_db()
        else:
            # Check protocol version
            db_protocol = self._redis.get(_PROTOCOL_KEY)
            if db_protocol is None:
                raise RuntimeError(
                    f"No blissdata protocol version found on {url} Redis database, make sure it is initialized."
                )
            else:
                db_protocol = int(db_protocol.decode())

            if _PROTOCOL_VERSION != db_protocol:
                raise RuntimeError(
                    f"Found blissdata protocol {db_protocol} on {url} Redis database, but only version {_PROTOCOL_VERSION} is supported."
                )

    def _setup_redis_db(self):
        """Setup Redis database for blissdata needs. To prevent it is not call by accident,
        this function only accepts to execute on an empty database. Therefore, you need to
        flush the database first if you want to re-apply setup.

        setup steps:
            - Load data models on RediSearch to enable server-side indexing
            - Increase MAXPREFIXEXPANSIONS for RediSearch to not limit search query length
            - Upload stream sealing function to (required for atomic sealing)
            - Write protocol version into Redis for clients
        """
        # Ensure the database is empty
        nb_keys = self._redis.dbsize()
        if nb_keys:
            raise RuntimeError(
                f"Cannot re-index a non-empty Redis database, {nb_keys} keys are already present"
            )

        # Configure RediSearch indexing with redis-om, but block redis-om mechanism which auto-discover any
        # model class for indexing. Instead we set model_registry by hand to only enable self._scan_model indexing
        # and not to interfer with existing DataStores.
        from redis_om.model import model

        scan_mdl = self._scan_model
        model.model_registry = {
            f"{scan_mdl.__module__}.{scan_mdl.__qualname__}": scan_mdl
        }
        Migrator().run()

        # TODO how to ensure this is always greater than the number of scan in redis ?
        # otherwise search results may be truncated without warning
        self._redis.ft().config_set("MAXPREFIXEXPANSIONS", 2000)

        # Server side sealing function:
        # For the seal to contain the complete length of the stream, we
        # need to read the last entry first. Also, both commands should be
        # executed atomically.
        # Because command 2 depends on command 1, this can't be done within
        # a transaction, thus we need a server side function.
        stream_seal_func = """#!lua name=mylib
local function seal_stream(keys, args)
  local stream = keys[1]
  local seal = '18446744073709551615-18446744073709551615'
  local entry = redis.call('XREVRANGE', stream, '+', '-', 'COUNT', 1)[1]

  local id
  local value
  if entry == nil then
      id = -1
      value = {}
  elseif entry[1] == seal then
    error(keys[1].." is already sealed.")
  else
      id = tonumber(string.match(entry[1], "([^-]+)"))
      value = entry[2]
  end

  local len = 1
  for i, sub in ipairs(value) do
      if sub == 'len' then
          len = value[i+1]
      end
  end
  local sealing_id = len + id
  redis.call('XADD', stream, seal, 'id', sealing_id)
  return sealing_id
end

redis.register_function('seal_stream', seal_stream)"""
        self._redis.function_load(code=stream_seal_func, replace=True)

        self._redis.set(_PROTOCOL_KEY, _PROTOCOL_VERSION)

    def load_scan(self, key):
        return Scan._load(self, key)

    def create_scan(self, identity, info={}):
        return Scan._create(self, identity, info)

    def get_last_scan(self):
        """Find the latest scan created. If it was deleted, then the previous one is returned, etc.
        Raises NoScanAvailable if no scan exists.
        return: (timetag, scan_key)
        """
        max = "+"
        while True:
            raw = self._redis.xrevrange(scan_creation_stream, count=1, max=max)

            if not raw:
                raise NoScanAvailable
            else:
                timetag, data = raw[0]
                timetag = timetag.decode()
                key = data[b"key"].decode()

            ttl = self._redis.ttl(key)
            if ttl != -1 and ttl <= 10:
                # scan was deleted, skip that one
                a, b = [int(x) for x in timetag.split("-")]
                if b == 0:
                    max = f"{a - 1}-{2**64-1}"
                else:
                    max = f"{a}-{b - 1}"
                continue

            return timetag, key

    def get_last_scan_timetag(self) -> str:
        """Return the timetag of the last scan or '0-0' when there is no scan.
        Useful to save a starting point before telling publisher you're ready.
        """
        timetag = self._redis.xrevrange(scan_creation_stream, count=1)
        if timetag:
            return timetag[0][0].decode()
        else:
            return "0-0"

    def get_next_scan(self, since=None, block=True, timeout=0):
        """Blocking function waiting for the next scan, starting from now or a specific moment.
        since: Moment after which the next scan is awaited, using None means "from now".
            In order to iterate over new scans without missing any between subsequent calls, be sure to
            start from the previously returned timetag each time e.g:
            >    prev_timetag = None
            >    while True:
            >        prev_timetag, scan = get_next_scan(since=prev_timetag)
            >        # process scan
            Note that search functions also return a timetag which can be used to get the direct next
            scan created after search request, thus ensuring to not miss any scan in between.
        timeout: Given in seconds, zero means infinite timeout. Raises a NoScanAvailable exception when expiring.
        return: (timetag, scan_key)
        """
        if since is None:
            since = "$"

        if not block:
            timeout = None
        else:
            timeout = int(timeout * 1000)

        while True:
            if timeout not in [None, 0]:
                start = time.perf_counter()

            raw = self._redis.xread(
                {scan_creation_stream: since},
                block=timeout,
                count=1,
            )

            if timeout not in [None, 0]:
                stop = time.perf_counter()
                timeout -= int((stop - start) * 1000)
                if timeout <= 0:
                    timeout = None

            if not raw:
                raise NoScanAvailable
            else:
                timetag, data = raw[0][1][0]
                timetag = timetag.decode()
                key = data[b"key"].decode()

            ttl = self._redis.ttl(key)
            if ttl != -1 and ttl <= 10:
                # scan was deleted, skip that one
                since = timetag
                continue

            return timetag, key

    def _timestamped_pipeline(self, func):
        """Execute func(pipe) and get the last scan created timetag in an atomic way."""

        def catch_time_and_aggregate(pipe: redis.client.Pipeline) -> None:
            pipe.xrevrange(scan_creation_stream, count=1)
            func(pipe)

        timetag, raw_result = self._redis.transaction(catch_time_and_aggregate)

        if timetag:
            timetag = timetag[0][0].decode()
        else:
            timetag = "0-0"

        return timetag, raw_result

    def search_existing_scans(self, **kwargs):
        """Search for scans matching with ScanModel.id fields matching with those provided in kwargs.

        Example (assuming there is "name" and "dataset" fields in ScanModel.id):
            timetag, scan_keys = search_existing_scans(name="myscan", dataset="abc654")

        A wildcard can be used for prefix/infix/suffix matches in each field, for example:
            name="abcd*" OR name="*efgh*" OR name="*ijkl"
            But "abcd*ijkl" is forbidden as it is matching on both prefix and suffix.

        Return the following tuple:
            (<timetag>, [<scan key>, ...])
            Where timetag correspond to the last scan creation event at the time of the search.
            It can be used by get_next_scan(since=timetag) to wait for any scan posterior to that search.
        """
        # Escape all RedisSearch special characters except "*"
        escape_table = str.maketrans(
            {c: f"\\{c}" for c in ",.<>{}[]\"':;!@#$%^&()-+=~?/ "}
        )

        assert "since" not in kwargs
        if not kwargs:
            query_string = "*"
        else:
            query_items = {}
            for k, v in kwargs.items():
                if isinstance(v, Number):
                    query_items[f"id_{k}"] = f"[{v} {v}]"
                else:
                    query_items[f"id_{k}"] = tags(v.translate(escape_table))
            query_string = querystring(**query_items)

        max_count = 1000
        while True:
            query = Query(query_string).paging(0, max_count).no_content()
            timetag, raw_result = self._timestamped_search(query)
            count = raw_result[0]
            if count <= max_count:
                break
            else:
                # In the unlikely case more than max_count results are available,
                # increase max_count and retry.
                max_count = count

        return timetag, [key.decode() for key in raw_result[1:]]

    def _timestamped_search(self, query):
        def search(pipe: redis.client.Pipeline) -> None:
            pipe.ft("esrf:scan:index").search(query)

        return self._timestamped_pipeline(search)

    def _timestamped_aggregate(self, aggregate_request):
        def aggregate(pipe: redis.client.Pipeline) -> None:
            pipe.ft("esrf:scan:index").aggregate(aggregate_request)

        return self._timestamped_pipeline(aggregate)

    def get_scans_state(self, scan_keys: list[str]):
        """Return the current state of a list of scans from their keys in an efficient way.
        This is done by only requesting last state in the state stream of each scan, without
        syncing json content as Scan._load() would do.
        """
        if not scan_keys:
            return {}

        raw = self._redis.json().mget(scan_keys, "state_stream.pk")
        stream_keys = [self._stream_model.make_key(pk) for pk in raw]

        pipe = self._redis.pipeline(transaction=False)
        for stream_key in stream_keys:
            pipe.xrevrange(stream_key, count=1)
        raw = pipe.execute()
        scan_states = [ScanState(val[0][1][b"state"].decode()) for val in raw]

        return {key: state for key, state in zip(scan_keys, scan_states)}

    def delete_scan(self, scan_key, force=False):
        """Delete a scan which is already closed or raise a RuntimeError.
        If force is True, the scan will be closed first if not terminated.

        Raises:
            ScanNotFoundError: the scan doesn't exists (or it is already deleted).
            RuntimeError: the scan is not terminated and force is False.

        Note: Deletion actually sets a 10 seconds expiration time on the scan.
        Consequently, any scan with 10 seconds expiration or less is considered deleted.
        By doing this, we avoid race conditions where the scan would disappear in the middle
        of some transactions."""
        try:
            scan = Scan._load_rw(self, scan_key)
        except ScanNotFoundError:
            return

        if scan.state < ScanState.CLOSED:
            # Never delete a scan without eventually closing it, this allows
            # clients in a blocking scan.update() to leave.
            # Then, the key's time-to-live will prevent others to enter blocking calls.
            if force:
                # close() also seals any opened stream, making any reader to leave.
                scan.info["end_reason"] = "DELETION"
                scan.close()
            else:
                raise RuntimeError(
                    f"Scan {scan_key} is not terminated, use force=True to delete anyway."
                )

        def delete_scan_keys(pipe: redis.client.Pipeline) -> None:
            keys = [stream.key() for stream in scan._model.data_streams.values()]
            keys.append(scan._model.state_stream.key())
            keys.append(scan_key)
            for key in keys:
                # Set a small expiration time instead of deleting immediately.
                # This is to prevent race conditions. Running operations have time to terminate,
                # but new ones check for expiration time and will consider it deleted.
                pipe.expire(key, 10)

        self._redis.transaction(delete_scan_keys)
