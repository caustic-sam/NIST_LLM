from time import time
from threading import RLock
from contextlib import contextmanager
from typing import Any, Callable, Dict, Optional
from concurrent.futures import ThreadPoolExecutor, Future, TimeoutError

from . import GEVENT_PATCHED
from .interface import QueryPool
from ..utils.maxsizedict import MaxSizeDict


class ThreadQueryPool(QueryPool):
    """
    This query pool is to be used in an environment that is not gevent patched.
    """

    def __init__(
        self, timeout: Optional[float] = None, maxqueries: Optional[int] = None
    ):
        if GEVENT_PATCHED:
            raise RuntimeError("requires an environment non pactehd by gevent")

        self.__pool = ThreadPoolExecutor(max_workers=10000)
        if timeout is None:
            timeout = 0.1
        self.timeout = timeout
        self.__futures: Dict[tuple, Future] = dict()
        if maxqueries:
            self.__lock = RLock()
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
                self.__futures[call_id] = future = self.__pool.submit(wrapper)

        if timeout is None:
            timeout = self.timeout
        try:
            future.result(timeout=timeout)
        except TimeoutError:
            pass
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
            with self._lock():
                futures = list(self.__futures.values())
            for future in futures:
                t0 = time()
                future.result(timeout=timeout)
                timeout = max(timeout - time() + t0, 0)
        except TimeoutError:
            return False
        return True

    wait.__doc__ = QueryPool.wait.__doc__

    def cancel(self, timeout=None, block=True) -> Optional[bool]:
        raise NotImplementedError("not supported")

    cancel.__doc__ = QueryPool.cancel.__doc__


ThreadQueryPool.__doc__ = ThreadQueryPool.__doc__ + QueryPool.__doc__
