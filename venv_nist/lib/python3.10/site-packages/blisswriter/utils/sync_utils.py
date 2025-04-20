import threading
from contextlib import contextmanager
from typing import Hashable, List, Generator

try:
    from gevent.monkey import is_anything_patched
    import gevent.lock
except ImportError:

    def is_anything_patched():
        return False


class SharedLockPool:
    """
    Allows to acquire locks identified by key (hashable type) recursively.
    """

    def __init__(self, timeout: float = -1):
        self.__locks = dict()
        if is_anything_patched():
            self.__locks_mutex = gevent.lock.Semaphore(value=1)
            self.__lock_class = gevent.lock.RLock
        else:
            self.__locks_mutex = threading.Lock()
            self.__lock_class = threading.RLock
        self._timeout = timeout

    def __len__(self) -> int:
        with self.__locks_mutex:
            return len(self.__locks)

    def keys(self) -> List[Hashable]:
        with self.__locks_mutex:
            return list(self.__locks)

    @contextmanager
    def acquire(self, key: Hashable) -> Generator[None, None, None]:
        lock = self.__get_lock(key)
        try:
            if not lock.acquire(timeout=self._timeout):
                raise TimeoutError(f"could not lock {key}")
            try:
                yield
            finally:
                lock.release()
        finally:
            self.__pop_lock(key)

    def __get_lock(self, key: Hashable) -> threading.RLock:
        with self.__locks_mutex:
            lock, count = self.__locks.get(key, (None, 0))
            if lock is None:
                lock = self.__lock_class()
            self.__locks[key] = lock, count + 1
            return lock

    def __pop_lock(self, key: Hashable) -> None:
        with self.__locks_mutex:
            lock, count = self.__locks.pop(key, (None, 0))
            count -= 1
            if count > 0:
                self.__locks[key] = lock, count
