import platform
import asyncio
from typing import Optional
import gevent.event
import gevent.core
import gevent
import gevent.selectors
import threading
import time


class EventLoop(asyncio.SelectorEventLoop):
    """
    An asyncio event loop that uses gevent for scheduling
    """

    def __init__(self, selector=None):
        super().__init__(selector or gevent.selectors.DefaultSelector())

    def time(self):
        if platform.system() == "Windows":
            return time.time()
        else:
            return gevent.core.time()

    def create_task(self, *args, **kwargs):
        task = super().create_task(*args, **kwargs)
        self._write_to_self()
        return task

    def call_soon(self, callback, *args, **kwargs):
        handle = super(EventLoop, self).call_soon(callback, *args, **kwargs)
        if self._selector is not None and not self._selector._ready.is_set():
            # selector.select() is running: write into the self-pipe to wake up
            # the selector
            self._write_to_self()
        return handle

    def call_at(self, when, callback, *args, **kwargs):
        handle = super(EventLoop, self).call_at(when, callback, *args, **kwargs)
        if self._selector is not None and not self._selector._ready.is_set():
            # selector.select() is running: write into the self-pipe to wake up
            # the selector
            self._write_to_self()
        return handle


class EventLoopPolicy(asyncio.DefaultEventLoopPolicy):  # type: ignore
    """
    An asyncio event loop policy with the all the default behaviours except
    that it uses the `asyncio_gevent.EventLoop` which uses gevent for
    scheduling
    """

    _loop_factory = EventLoop

    def __init__(self):
        # gevent does not support threads, an attribute is enough
        self._loop = None

    def get_event_loop(self):
        if not isinstance(threading.current_thread(), threading._MainThread):
            raise RuntimeError(
                "asyncio+gevent event loop must run in " "the main thread"
            )
        if self._loop is None:
            self._loop = self.new_event_loop()
        return self._loop

    def set_event_loop(self, loop):
        self._loop = loop

    def new_event_loop(self):
        return self._loop_factory()


def enable_asyncio_gevent():
    asyncio.set_event_loop_policy(EventLoopPolicy())


def yield_future(
    future: asyncio.Future,
    loop: Optional[asyncio.AbstractEventLoop] = None,
):
    """
    Wait for a future, a task or a coroutine from a greenlet.

    Yield control to other eligible greenlet until the future is done (finished
    successfully or failed with an exception).

    Return the result or raise the exception of the future.
    """
    future = asyncio.ensure_future(future)
    if loop is None:
        loop = asyncio.get_event_loop()

    if loop.is_running():
        done_ev = gevent.event.Event()

        def set_done_event(_):
            done_ev.set()

        future.add_done_callback(set_done_event)
        done_ev.wait()
    else:
        loop.run_until_complete(future)

    return future.result()


def future_to_greenlet(
    future: asyncio.Future, loop: Optional[asyncio.AbstractEventLoop] = None
):
    """
    Wrap a future in a greenlet
    """
    return gevent.spawn(yield_future, future, loop)


def greenlet_to_future(
    greenlet: gevent.Greenlet,
    loop: Optional[asyncio.AbstractEventLoop] = None,
    autokill_greenlet: bool = True,
) -> asyncio.Future:
    """
    Wrap a greenlet in a future.

    The greenlet should not have been started yet, thus gevent.spawn() needs to
    be decomposed like the following:
        greenlet = Greenlet(func, arg)
        future = greenlet_to_future(greenlet)
        greenlet.start()
        await future

    If the future gets cancelled, then by default the greenlet is killed. To
    prevent the greenlet from getting killed, you can pass
    `autokill_greenlet=False` as an argument to `greenlet_to_future`.
    """
    future = asyncio.Future(loop=loop)

    if not isinstance(greenlet, gevent.Greenlet):
        raise TypeError(
            f"greenlet_to_future: a gevent Greenlet is requested, not {type(greenlet)}"
        )

    if greenlet or greenlet.dead:
        raise RuntimeError("greenlet_to_future: the greenlet has already started")

    if autokill_greenlet:

        def fut_done(future):
            if future.cancelled():
                greenlet.kill(KeyboardInterrupt)

        future.add_done_callback(fut_done)

    orig_func = greenlet._run

    def wrap_func(*args, **kw):
        try:
            result = orig_func(*args, **kw)
        except BaseException as exc:
            if not future.cancelled():
                # setting exception on a cancelled future
                # raises InvalidState error
                future.set_exception(exc)
            raise
        else:
            if not future.cancelled():
                # setting result on a cancelled future
                # raises InvalidState error
                future.set_result(result)
        return result

    greenlet._run = wrap_func

    return future
