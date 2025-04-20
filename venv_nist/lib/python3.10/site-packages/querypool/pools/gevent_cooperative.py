from typing import Any, Callable, Dict, Optional

import gevent

from . import GEVENT_PATCHED
from .interface import QueryPool
from ..utils.maxsizedict import MaxSizeDict


class CancelQuery(gevent.GreenletExit):
    pass


class GeventCooperativeQueryPool(QueryPool):
    """
    This query pool is to be used in a gevent patched environment.
    The queries are assumed to be gevent cooperative.
    """

    def __init__(
        self, timeout: Optional[float] = None, maxqueries: Optional[int] = None
    ):
        if not GEVENT_PATCHED:
            raise RuntimeError("requires a gevent patched environment")
        if timeout is None:
            timeout = 0.1
        self.timeout = timeout
        self.__futures: Dict[tuple, gevent.Greenlet] = dict()
        if maxqueries:
            self.__results = MaxSizeDict(maxsize=maxqueries)
        else:
            self.__results = dict()

    __init__.__doc__ = QueryPool.__init__.__doc__

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
                    self.__results[call_id] = False, query(*args, **kwargs)
                except BaseException as e:
                    self.__results[call_id] = True, e
                    raise
                finally:
                    self.__futures.pop(call_id, None)

            future = gevent.Greenlet(wrapper)
            self.__futures[call_id] = future
            future.start()
        if timeout is None:
            timeout = self.timeout
        future.join(timeout=timeout)
        result = self.__results.get(call_id, None)
        if result is None:
            return default
        is_error, result = result
        if is_error:
            raise result
        return result

    execute.__doc__ = QueryPool.execute.__doc__

    def wait(self, timeout=None) -> bool:
        futures = list(self.__futures.values())
        finished = gevent.joinall(futures, timeout=timeout)
        return len(futures) == len(finished)

    wait.__doc__ = QueryPool.wait.__doc__

    def cancel(self, timeout=None, block=True) -> Optional[bool]:
        futures = list(self.__futures.values())
        if not block:
            gevent.killall(futures, exception=CancelQuery, block=False)
            return
        try:
            with gevent.Timeout(timeout) as local_timeout:
                gevent.killall(futures, exception=CancelQuery, timeout=timeout)
        except gevent.Timeout as raised_timeout:
            if raised_timeout is not local_timeout:
                raise
            return False
        return True

    cancel.__doc__ = QueryPool.cancel.__doc__


GeventCooperativeQueryPool.__doc__ = (
    QueryPool.__doc__ + GeventCooperativeQueryPool.__doc__
)
