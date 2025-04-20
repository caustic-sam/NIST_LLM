# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

from __future__ import annotations

from typing import Any

from .task import Task


class ProxyTask(Task):
    """Task based on another task."""

    def __init__(self, task: Task):
        self._task: Task = task

    @property
    def description(self) -> str:
        return self._task.description

    @property
    def task_id(self) -> str | None:
        return self._task.task_id

    def _set_task_id(self, task_id: str):
        self._task._set_task_id(task_id)

    def validate(self, *args, **kwargs):
        return self._task.validate(*args, **kwargs)

    def __call__(self, *args, **kwargs):
        return self._task(*args, **kwargs)

    def has_progress(self):
        return self._task.has_progress()

    def progress(self) -> dict[str, Any]:
        return self._task.progress()
