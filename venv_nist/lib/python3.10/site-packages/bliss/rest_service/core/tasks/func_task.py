# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

from __future__ import annotations

from typing import Any
from collections.abc import Callable

from .task import Task


class FuncTask(Task):
    """Async task defined by a basic function"""

    def __init__(self, func: Callable, description: str):
        Task.__init__(self)
        self.__func = func
        self.__task_id: str | None = None
        self._description = description

    @property
    def description(self) -> str:
        return self._description

    @property
    def func(self) -> Callable:
        return self.__func

    @property
    def task_id(self) -> str | None:
        return self.__task_id

    def _set_task_id(self, task_id: str):
        self.__task_id = task_id

    def validate(self, *args, **kwargs):
        pass

    def __call__(self, *args, **kwargs) -> Any:
        return self.__func(*args, **kwargs)

    def has_progress(self) -> bool:
        return False

    def progress(self) -> dict[str, Any]:
        return {}
