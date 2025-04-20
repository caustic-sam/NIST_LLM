# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

from __future__ import annotations

from typing import Any
from collections.abc import Callable

from bliss.scanning.scan import ScanState
from .func_task import FuncTask


class ScanFactoryTask(FuncTask):
    """Wrap scan factory.

    - The function is executed during the whole scan (so what it can be killed)
    - The scan is returned as a "progress" metadata
    """

    def __init__(self, func: Callable, description: str):
        FuncTask.__init__(self, func, description)
        self._scan = None
        self._description = description

    @property
    def description(self) -> str:
        return self._description

    def __call__(self, *args, **kwargs):
        self._scan = self.func(*args, **kwargs, run=False)
        if self._scan is not None:
            self._scan.run()
        return self._scan

    def has_progress(self):
        return True

    def progress(self) -> dict[str, Any]:
        scan = self._scan
        if scan is None or scan.state < ScanState.STARTING:
            return {"scan": None}
        return {"scan": scan}
