# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

from packaging.version import Version
from importlib.metadata import version

try:
    from prompt_toolkit import cursor_shapes
except ImportError:
    pass
else:
    # fix weird characters display on old terminals
    saved_cursor_shape_config = cursor_shapes.to_cursor_shape_config
    cursor_shapes.to_cursor_shape_config = lambda _: saved_cursor_shape_config(None)

from prompt_toolkit import application

_PROMPT_TOOLKIT_HANDLES_SIGINT = Version(version("prompt_toolkit")) > Version("3.0.24")

if _PROMPT_TOOLKIT_HANDLES_SIGINT:
    # Prevent prompt toolkit to handle SIGINT by default
    class Application(application.Application):
        async def run_async(self, **kwargs):
            kwargs["handle_sigint"] = False
            return await super().run_async(**kwargs)

    application.Application = Application
    application.application.Application = Application
