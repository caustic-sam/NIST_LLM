# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

from __future__ import annotations

import gevent
import uuid
import lru
from typing import Any, NamedTuple
from collections.abc import Callable

from .tasks.task import Task
from .tasks.func_task import FuncTask


class TaskIdNotValid(Exception):
    pass


class Result(NamedTuple):
    terminated: bool
    result: Any
    progress: dict[str, Any] | None


class AsyncTasks:
    """
    Service to handle async taks requested by the REST API.

    It executes session and object async calls.

    Each call is stored as a task. A task is drop after
    termination when the client dont need it anymore, or if the
    size limit of the store is hit.
    """

    def __init__(self):
        self._tasks: lru.LRU[str, Task] = lru.LRU(256)

    def disconnect(self):
        """Disconnect the service"""
        for t in self._tasks.values():
            if t._g is not None:
                t._g.kill()
        self._tasks.clear()

    def spawn(self, callable: Callable | Task, args, kwargs) -> str:
        """Execute a callable and return"""
        g = gevent.spawn(callable, *args, **kwargs)
        if isinstance(callable, Task):
            task = callable
        else:
            task = FuncTask(callable)

        task_id = str(uuid.uuid4())
        g.name = f"rest-task-{task_id}"
        task._g = g
        task._set_task_id(task_id)
        self._tasks[task_id] = task
        return task_id

    def get_state(self, task_id: str) -> Result:
        """
        Return the result of the task.

        If the task have finished, it is automatically dropped
        during the call of this function.

        Raises:
            TaskIdNotValid: If the task id is not valid
        """
        task = self._tasks.get(task_id)
        if task is None:
            raise TaskIdNotValid(f"Task {task_id} is not available")

        if task.has_progress():
            progress = task.progress()
        else:
            progress = None

        greenlet = task._g
        if greenlet is None:
            raise TaskIdNotValid(f"Task {task_id} is not available")

        if not greenlet.ready():
            return Result(terminated=False, result=None, progress=progress)

        del self._tasks[task_id]
        result = greenlet.get(block=True)
        return Result(terminated=True, result=result, progress=progress)

    def kill(self, task_id: str):
        """Kill a task.

        The task stays stored in the manager until it's complete
        terminating and it's last state it is retrieved by the client.

        Raises:
            TaskIdNotValid: If the task id is not valid
        """
        task = self._tasks.get(task_id, None)
        if task is None:
            raise TaskIdNotValid(f"Task {task_id} is not available")
        greenlet = task._g
        if greenlet is None:
            raise TaskIdNotValid(f"Task {task_id} is not available")
        greenlet.kill(block=False)

    def __info__(self):
        if len(self._tasks) == 0:
            return "No tasks"
        result = ["List of tasks"]

        def get_state(task: Task):
            greenlet = task._g
            if greenlet is None:
                return "INVALID"
            if not greenlet.ready():
                return "RUNNING"
            return "DONE   "

        for task_id, task in self._tasks.items():
            state = get_state(task)
            result.append(f"{task_id}  {state}  {task.description}")
        return "\n".join(result)
