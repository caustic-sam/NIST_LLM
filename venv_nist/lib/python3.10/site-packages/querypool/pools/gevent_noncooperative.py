from contextlib import contextmanager
from typing import Any, Callable, Dict, Optional

import gevent
from gevent.monkey import get_original
from gevent.event import AsyncResult

from . import GEVENT_PATCHED
from .interface import QueryPool
from ..utils.maxsizedict import MaxSizeDict


class GeventNonCooperativeQueryPool(QueryPool):
    """
    This query pool is to be used in a gevent patched environment.
    The queries are assumed to be gevent non-cooperative calls.
    """

    def __init__(
        self, timeout: Optional[float] = None, maxqueries: Optional[int] = None
    ):
        if not GEVENT_PATCHED:
            raise RuntimeError("requires a gevent patched environment")

        if timeout is None:
            timeout = 0.1
        self.timeout = timeout
        self.__futures: Dict[tuple, AsyncResult] = dict()
        if maxqueries:
            self.__lock = get_original("threading", "RLock")()
            self.__results = MaxSizeDict(maxsize=maxqueries)
        else:
            self.__lock = None
            self.__results = dict()

    __init__.__doc__ = QueryPool.__init__.__doc__

    @contextmanager
    def _lock(self):
        if self.__lock is None:
            yield
        else:
            with self.__lock:
                yield

    def execute(
        self,
        query: Callable,
        args: Optional[tuple] = tuple(),
        kwargs: Optional[dict] = None,
        timeout: Optional[float] = None,
        default=None,
    ) -> Any:
        if kwargs is None:
            kwargs = dict()
        call_id = query, args, tuple(kwargs.items())
        future = self.__futures.get(call_id)
        if future is None:

            def wrapper():
                try:
                    result = query(*args, **kwargs)
                    with self._lock():
                        self.__results[call_id] = False, result
                except BaseException as e:
                    with self._lock():
                        self.__results[call_id] = True, e
                    raise
                finally:
                    with self._lock():
                        self.__futures.pop(call_id, None)

            with self._lock():
                pool = gevent.get_hub().threadpool
                future = pool.spawn(wrapper)
                self.__futures[call_id] = future
        if timeout is None:
            timeout = self.timeout
        future.wait(timeout=timeout)
        result = self.__results.get(call_id, None)
        if result is None:
            return default
        is_error, result = result
        if is_error:
            raise result
        return result

    execute.__doc__ = QueryPool.execute.__doc__

    def wait(self, timeout=None) -> bool:
        try:
            with gevent.Timeout(timeout) as local_timeout:
                with self._lock():
                    futures = list(self.__futures.values())
                for future in futures:
                    future.wait()
        except gevent.Timeout as raised_timeout:
            if raised_timeout is not local_timeout:
                raise
            return False
        return True

    wait.__doc__ = QueryPool.wait.__doc__

    def cancel(self, timeout=None, block=True) -> Optional[bool]:
        raise NotImplementedError("not supported")

    cancel.__doc__ = QueryPool.cancel.__doc__


GeventNonCooperativeQueryPool.__doc__ = (
    QueryPool.__doc__ + GeventNonCooperativeQueryPool.__doc__
)
