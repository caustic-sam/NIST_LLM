import time
import random
import multiprocessing
from concurrent.futures import ThreadPoolExecutor

import pytest

try:
    import gevent
    from gevent.monkey import is_anything_patched
except ImportError:
    gevent = None

    def is_anything_patched():
        return False


from blisswriter.utils import sync_utils


@pytest.mark.skipif(gevent is None, reason="Requires gevent")
def test_shared_lock_pool_gevent():
    keys = list(range(4))
    lockpool = sync_utils.SharedLockPool(timeout=3)

    def worker():
        for _ in range(100):
            for key in keys:
                with lockpool.acquire(key):
                    gevent.sleep(random.uniform(0, 1e-6))

    glts = [gevent.spawn(worker) for _ in range(100)]
    try:
        gltsdone = gevent.joinall(glts, raise_error=True, timeout=10)
        assert len(glts) == len(gltsdone)
        assert len(lockpool) == 0
    finally:
        gevent.killall(glts)


def test_shared_lock_pool_threading():
    ctx = multiprocessing.get_context("spawn")
    p = ctx.Process(target=_assert_lockpool)
    p.start()
    try:
        p.join(timeout=10)
        if p.is_alive():
            raise TimeoutError("test timed outed")
        if p.exitcode:
            raise RuntimeError("test failed")
    finally:
        p.kill()


def _assert_lockpool():
    assert not is_anything_patched()

    keys = list(range(4))
    lockpool = sync_utils.SharedLockPool(timeout=3)

    def worker(*_):
        for _ in range(100):
            for key in keys:
                with lockpool.acquire(key):
                    with lockpool.acquire(key):
                        time.sleep(random.uniform(0, 1e-6))

    with ThreadPoolExecutor(50) as pool:
        list(pool.map(worker, range(50)))

    assert len(lockpool) == 0
