"""Manage Redis connections and provide a low-level API to Redis.

The low-level API exposes all Redis commands (https://redis.io/commands)
as methods of Redis database proxies (`RedisDbProxyBase` and derived classes).

Redis database proxies are created through a Redis connection pool (`RedisDbConnectionPool`).

Redis connection pools are created through a Redis connection manager (`RedisConnectionManager`).
"""

from .manager import RedisConnectionManager  # noqa: F401
from .manager import RedisAddress  # noqa: F401
