from typing import Callable
from time import time


class PeriodicTask:
    def __init__(self, task: Callable[[], None], period: float = 0):
        self._tm0 = time()
        self._period = period
        self._task = task

    def reset(self):
        self._tm0 = time()

    def execute(self):
        tm = time()
        if (tm - self._tm0) > self._period:
            self._task()
            self._tm0 = tm
