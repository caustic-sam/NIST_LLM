from __future__ import annotations

import json
import time
from typing import Any
from collections.abc import Iterator
from collections.abc import MutableMapping

from bliss.redis.proxy import RedisDbProxyBase
from bliss.config.wardrobe import ParametersWardrobe

from bliss.config.conductor.client import get_redis_proxy


class ExternalRedisStore(MutableMapping):
    def __init__(self, redis_key: str):
        self._connection: RedisDbProxyBase = get_redis_proxy(caching=False)
        self._redis_key = redis_key

    def cleanup_legacy_wardrobe(
        self, legacy_wardrobe_name: str, fields: list[str]
    ) -> None:
        legacy_redis_key = f"parameters:{legacy_wardrobe_name}:default"
        if not self._connection.exists(legacy_redis_key):
            return
        legacy = ParametersWardrobe(legacy_wardrobe_name, connection=self._connection)
        for key, value in legacy.to_dict().items():
            if key in fields:
                self[key] = value
        self._connection.delete(legacy_redis_key)
        self._connection.delete(f"{legacy_redis_key}:creation_order")

    def __getitem__(self, key: str) -> Any:
        value = self._connection.hget(self._redis_key, key)
        if value is None:
            raise KeyError(key)
        return json.loads(value)

    def __setitem__(self, key: str, value: Any) -> None:
        self._connection.hset(self._redis_key, key, json.dumps(value))

    def __delitem__(self, key: str) -> None:
        self._connection.hdel(self._redis_key, key)

    def __iter__(self) -> Iterator[Any]:
        keys = self._connection.hkeys(self._redis_key)
        for key in keys:
            yield key.decode()

    def __len__(self) -> int:
        return self._connection.hlen(self._redis_key)


global REDISDB
REDISDB = dict()


class ExternalTestStore(MutableMapping):
    DELAY = 0

    def __init__(self, redis_key: str):
        self.reset_counters()
        self._data = REDISDB.setdefault(redis_key, dict())

    def __getitem__(self, key: str) -> Any:
        self._get_count += 1
        time.sleep(self.DELAY)
        return self._data[key]

    def __setitem__(self, key: str, value: Any) -> None:
        self._set_count += 1
        time.sleep(self.DELAY)
        self._data[key] = value

    def __delitem__(self, key: str) -> None:
        self._del_count += 1
        time.sleep(self.DELAY)
        del self._data[key]

    def __iter__(self) -> Iterator[Any]:
        self._other_count += 1
        for key in self._data:
            time.sleep(self.DELAY)
            yield key

    def __len__(self) -> int:
        self._other_count += 1
        return len(self._data)

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(len={len(self)}, get={self._get_count}, set={self._set_count}, del={self._del_count}, other={self._other_count})"

    def reset_counters(self) -> None:
        self._get_count = 0
        self._set_count = 0
        self._del_count = 0
        self._other_count = 0
