import importlib
import gevent
from gevent import greenlet, timeout

from bliss.common.greenlet_utils.killmask import BlissTimeout, BlissGreenlet
from bliss.common.greenlet_utils.asyncio_gevent import enable_asyncio_gevent


def unpatch(socket=False, subprocess=False):
    """Unpatch libraries which was patched by BLISS.

    This is needed for some servers because BLISS is patching
    libs when it is imported.
    """
    if subprocess:
        import subprocess

        importlib.reload(subprocess)
    if socket:
        import socket

        importlib.reload(socket)


def unpatch_all(subprocess=True, socket=True):
    """Unpatch libraries which was patched by BLISS.

    This is needed for some servers because BLISS is patching
    libs when it is imported.
    """
    unpatch(subprocess=subprocess, socket=socket)


def bliss_patch_all():
    """Use this instead of `gevent.monkey.patch_all` for
    Bliss to work properly.
    """
    # Patch gevent's Greenlet and Timeout classes from KillMask
    # Note: do this before importing anything else from gevent
    #       or before monkey patching
    gevent.Timeout = BlissTimeout
    gevent.Greenlet = BlissGreenlet
    gevent.spawn = BlissGreenlet.spawn
    gevent.spawn_later = BlissGreenlet.spawn_later
    timeout.Timeout = BlissTimeout
    greenlet.Greenlet = BlissGreenlet

    # disable hub exception stream
    # (solves issue #3918)
    # Maybe this should go to the log...
    gevent.get_hub().exception_stream = None

    # allow asyncio to be used in a gevent patched process
    enable_asyncio_gevent()

    # make python builtins gevent cooperative
    from gevent.monkey import patch_all

    patch_all(thread=False)
