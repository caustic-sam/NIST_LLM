# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

from __future__ import annotations

from typing import Any

from .task import Task
from ...mappingdef import ObjectMapping


class ObjectFuncTask(Task):
    """Async task defined by a basic function"""

    def __init__(self, object: ObjectMapping, function_name: str):
        Task.__init__(self)
        self.__object: ObjectMapping = object
        self.__function_name: str = function_name
        self.__task_id: str | None = None

    @property
    def description(self) -> str:
        return f"{self.__object.name}.{self.__function_name}(...)"

    @property
    def task_id(self) -> str | None:
        return self.__task_id

    def _set_task_id(self, task_id: str):
        self.__task_id = task_id

    def validate(self, *args, **kwargs):
        """Validate the call with the arguments

        Args:
            function_name: The function to call.
            args: Function arguments
            kwargs: Function keywork arguments

        Raises:
            pydantic.ValidationError: If validation fails
        """
        self.__object.validate_call(self.__function_name, *args, **kwargs)

    def __call__(self, *args, **kwargs) -> Any:
        return self.__object._call(self.__function_name, *args, **kwargs)

    def has_progress(self) -> bool:
        return False

    def progress(self) -> dict[str, Any]:
        return {}
