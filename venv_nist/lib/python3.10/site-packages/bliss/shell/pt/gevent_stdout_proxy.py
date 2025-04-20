# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

import threading
import gevent
import logging
import traceback
from prompt_toolkit.patch_stdout import StdoutProxy as _StdoutProxy
from prompt_toolkit.application import current


_logger = logging.getLogger(__name__)


class GeventStdoutProxy(_StdoutProxy):
    """Implementation of prompt-toolkit `StdoutProxy` without thread.

    It's a file-like object, which prints everything written to it, output
    above the current application/prompt. This class is compatible with other
    file objects and can be used as a drop-in replacement for `sys.stdout` or
    can for instance be passed to `logging.StreamHandler`.

    The current application, above which we print, is determined by looking
    what application currently runs in the `AppSession` that is active during
    the creation of this instance.
    """

    MAX_SIZE = 10000
    """
    If the flushing greenlet dies, the gueue is not amymore
    flushed. This let the memory growing for ever.

    Use a fixed size to be sure there is no memory leak.
    """

    def _start_write_thread(self) -> threading.Thread:
        """Overwrite _start_write_thread to use a gevent thread"""
        # Patch the already initialized queue with
        self._flush_queue = gevent.queue.Queue(self.MAX_SIZE)

        # The only used API used by the base class is `join`.
        # It is compatible with a greenlet
        gwriter = gevent.spawn(self._write_greenlet)
        gwriter.link_exception(self._log_exception)
        gwriter.name = "patch-stdout-flush"
        return gwriter

    def _write_greenlet(self):
        """Process the content forever"""
        # Propagate app_session to the asyncio contextvars
        current._current_app_session.set(self.app_session)
        self._write_thread()

    def _log_exception(self, greenlet):
        try:
            greenlet.get()
        except Exception:
            # This class will be use to flush logging
            # better not to use logging for such error
            print(traceback.format_exc())
            # _logger.error("Error while flushing patch-stdout", exc_info=e)

    def wait_flush(self, timeout=0.1):
        """Wait until the stdout was flushed."""
        self.flush()
        # TODO: Rework it with passive wait
        SLEEP = 0.01
        for _ in range(int(timeout // SLEEP) + 1):
            if self._flush_queue.empty():
                break
            gevent.sleep(SLEEP)
