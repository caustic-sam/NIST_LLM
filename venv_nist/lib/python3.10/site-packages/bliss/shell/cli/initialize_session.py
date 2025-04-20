# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

from __future__ import annotations

import typing
from bliss.common.session import Session, DefaultSessionLoading
from bliss.shell.pt.text_block_app import TextBlockApplication
from bliss.shell.pt.utils import isatty
from prompt_toolkit.application import current


class _SessionLoading(DefaultSessionLoading):
    """
    Display the session loading with print.
    """

    def __init__(self):
        DefaultSessionLoading.__init__(self, verbose=True)
        self._block = 1, ""

    def get_message(self) -> tuple[int, str]:
        return self._block

    def _set_message(self, nb_lines: int, text: str):
        self._block = nb_lines, text

    def object_loading(self, session: Session, object_name: str):
        """Called when an object is about to be loaded"""
        self._set_message(1, f"Initializing: {object_name}")

    def objects_loaded(self, session: Session, item_count: int):
        """Called when every objects of the session were loaded"""
        self._set_message(1, "")
        DefaultSessionLoading.objects_loaded(
            self, session=session, item_count=item_count
        )

    def session_loaded(self, session: Session):
        self._set_message(1, "")


def setup_session(session: Session, env_dict: typing.Optional[dict]):
    result = False

    hook = _SessionLoading()

    app_session = current.get_app_session()
    if not isatty(app_session.input):
        # Sounds like some pt app fails in this case
        # (when a session is launched from a subprocess)
        return session.setup(env_dict)

    def background():
        nonlocal result
        result = session.setup(env_dict, hook=hook)

    app = TextBlockApplication(
        render=hook.get_message,
        refresh_interval=0.1,
        use_toolbar=False,
    )

    try:
        app.exec(process=background)
    except KeyboardInterrupt:
        return False
    return result
