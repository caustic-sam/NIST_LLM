try:
    import gevent.monkey
except ImportError:
    GEVENT_PATCHED = False
else:
    GEVENT_PATCHED = gevent.monkey.is_anything_patched()

from querypool.pools import CooperativeQueryPool as QueryPool  # noqa F401

if GEVENT_PATCHED:
    from gevent import Timeout
    from gevent import spawn  # noqa F401
    from gevent.queue import Queue, Empty

    def wait_process(process, timeout) -> bool:
        """
        :param process: A process object from `subprocess` or `psutil`
        """
        try:
            with Timeout(timeout) as local_timeout:
                # gevent timeout has to be used here
                # See https://github.com/gevent/gevent/issues/622
                process.wait()
            return True
        except Timeout as raised_timeout:
            if local_timeout is not raised_timeout:
                raise
            return False

else:
    import threading
    from subprocess import TimeoutExpired
    from queue import Queue, Empty

    def spawn(func, *args, **kwargs):
        thread = threading.Thread(target=func, args=args, kwargs=kwargs)
        thread.start()
        return thread

    def wait_process(process, timeout) -> bool:
        """
        :param process: A process object from `subprocess` or `psutil`
        """
        try:
            process.wait(timeout)
            return True
        except (TimeoutError, TimeoutExpired):
            return False


def flush_queue(q: Queue):
    while True:
        try:
            yield q.get(timeout=0)
        except Empty:
            break
