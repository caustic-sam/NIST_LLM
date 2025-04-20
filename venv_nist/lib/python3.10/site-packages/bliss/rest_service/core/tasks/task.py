# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

from __future__ import annotations

import gevent
from abc import ABC, abstractmethod
from typing import Any


class Task(ABC):
    """Async function call description"""

    def __init__(self):
        self._g: gevent.Greenlet | None = None
        """Greenlet which is handling the task"""

    @property
    @abstractmethod
    def description(self) -> str:
        """Short description of the task"""
        ...

    @property
    @abstractmethod
    def task_id(self) -> str | None:
        """If set, return the identifier of this task"""
        ...

    @abstractmethod
    def _set_task_id(self, task_id: str):
        """For internal use by the async tasks manager"""
        ...

    @abstractmethod
    def validate(self, *args, **kwargs):
        """Validate the arguments for this task.

        Args:
            function_name: The function to call.
            args: Function arguments
            kwargs: Function keywork arguments

        Raises:
            pydantic.ValidationError: If validation fails
        """
        ...

    @abstractmethod
    def __call__(self, *args, **kwargs) -> Any:
        ...

    @abstractmethod
    def has_progress(self) -> bool:
        """True if extra information can be retrieve from `.progress` for this task"""
        ...

    @abstractmethod
    def progress(self) -> dict[str, Any]:
        """Return extra information which can be accessed during the async processing"""
        ...
