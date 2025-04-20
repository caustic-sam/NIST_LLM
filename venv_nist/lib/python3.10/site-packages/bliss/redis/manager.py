"""Top-level API to Redis
"""

import os
import socket
from typing import Optional
import weakref
from dataclasses import dataclass
import gevent.lock

from bliss.redis.connection import create_connection_pool
from bliss.redis.connection import RedisDbConnectionPool
from bliss.redis.proxy import RedisDbProxyBase


@dataclass(frozen=True)
class RedisPoolId:
    db: int


@dataclass(frozen=True)
class RedisProxyId:
    db: int
    caching: bool


@dataclass(frozen=True)
class RedisAddress:
    host: str
    port: Optional[int]
    sock: Optional[str]

    @classmethod
    def factory(cls, address: Optional[str] = None):
        host = None
        port = None
        sock = None
        DEFAULT_REDIS_PORT = 6379
        if address is None:
            address = os.environ.get("REDIS_HOST")
        if address is None:
            host = "localhost"
            port = DEFAULT_REDIS_PORT
        elif ":" in address:
            host, suffix = address.split(":", 1)
            try:
                port = int(suffix)
            except ValueError:
                sock = suffix
        else:
            host = address
            port = DEFAULT_REDIS_PORT
        return cls(host=host, port=port, sock=sock)

    @property
    def url(self):
        if self.sock is not None:
            return f"unix://{self.sock}"
        else:
            return f"redis://{self.host}:{self.port}"


class RedisConnectionManager:
    """
    Use `get_db_proxy` to create a connection or use an existing one.
    Use `close_all_connections` to close all Redis connections.
    """

    CLIENT_NAME = f"{socket.gethostname()}:{os.getpid()}"

    def __init__(self, addresses: dict[int, RedisAddress]):
        self._addresses = addresses

        self._get_proxy_lock = gevent.lock.Semaphore()

        # Keep hard references to all shared Redis proxies
        # (these proxies don't hold a `redis.Redis.Connection` instance)
        self._shared_proxies: dict[RedisProxyId, RedisDbProxyBase] = dict()

        # Keep weak references to all shared Redis connection pools:
        self._connection_pools: dict[
            RedisPoolId, RedisDbConnectionPool
        ] = weakref.WeakValueDictionary()

        # Keep weak references to all cached Redis proxies which are not
        # reused (although they could be but their cache with kep growing)
        self._non_shared_proxies: set[RedisDbProxyBase] = weakref.WeakSet()

        # Hard references to the connection pools are held by the
        # Redis proxies themselves. Connections of RedisDbConnectionPool
        # are closed upon garbage collection of RedisDbConnectionPool. So
        # when the proxies too a pool are the only ones having a hard
        # reference too that pool, the connections are closed when all
        # proxies are garbage collected.

    def get_db_proxy(
        self, db: int = 0, caching: bool = False, shared: bool = True
    ) -> RedisDbProxyBase:
        """Get a greenlet-safe proxy to a Redis database.

        :param int db: Redis database too which we need a proxy
        :param bool caching: client-side caching
        :param bool shared: use a shared proxy held by the Beacon connection
        """
        proxyid = RedisProxyId(db=db, caching=caching)
        if shared:
            return self._get_shared_db_proxy(proxyid)
        else:
            return self._get_non_shared_db_proxy(proxyid)

    def close_all_connections(self):
        # To close `redis.connection.Connection` you need to call its
        # `disconnect` method (also called on garbage collection).
        #
        # Connection pools have a `disconnect` method that disconnect
        # all their connections, which means close and destroy their
        # socket instances.
        #
        # Note: closing a proxy will not close any connections
        proxies = list(self._non_shared_proxies)
        proxies.extend(self._shared_proxies.values())
        self._shared_proxies = dict()
        self._non_shared_proxies = weakref.WeakSet()
        for proxy in proxies:
            proxy.close()
            proxy.connection_pool.disconnect()

    def _get_shared_db_proxy(self, proxyid: RedisProxyId) -> RedisDbProxyBase:
        """Get a reusabed proxy and create it when it doesn't exist."""
        with self._get_proxy_lock:
            proxy = self._shared_proxies.get(proxyid)
            if proxy is None:
                pool = self._get_connection_pool(proxyid)
                proxy = pool.create_proxy(caching=proxyid.caching)
                self._shared_proxies[proxyid] = proxy
            return proxy

    def _get_non_shared_db_proxy(self, proxyid: RedisProxyId) -> RedisDbProxyBase:
        """Get a reusabed proxy and create it when it doesn't exist."""
        with self._get_proxy_lock:
            pool = self._get_connection_pool(proxyid)
            proxy = pool.create_proxy(caching=proxyid.caching)
            self._non_shared_proxies.add(proxy)
            return proxy

    def _get_connection_pool(self, proxyid: RedisProxyId) -> RedisDbConnectionPool:
        """Get a Redis connection pool (create when it does not exist yet)
        for the db.
        """
        poolid = RedisPoolId(db=proxyid.db)
        pool = self._connection_pools.get(poolid)
        if pool is None:
            pool = create_connection_pool(
                self._get_url(poolid.db), poolid.db, client_name=self.CLIENT_NAME
            )
            self._connection_pools[poolid] = pool
        return pool

    def _get_url(self, db: int) -> str:
        """Full Redis URL as a string"""
        address = self._addresses.get(db)
        if address is None:
            raise RuntimeError(f"No Redis address specified for database {db}")
        return address.url
