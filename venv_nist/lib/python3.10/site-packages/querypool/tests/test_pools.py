import pytest
from .. import pools

if pools.GEVENT_PATCHED:
    from gevent import sleep as cooperative_sleep
    from gevent.monkey import get_original

    noncooperative_sleep = get_original("time", "sleep")
else:
    from time import sleep as cooperative_sleep
    from time import sleep as noncooperative_sleep


@pytest.mark.timeout(5)
@pytest.mark.parametrize("query_type", ["cooperative", "noncooperative"])
def test_query_success(query_type):
    """test executing a query which returns a value"""
    pool = init_pool(query_type, timeout=0.1)

    def myfunc(a, b=0):
        query(query_type, a + b)
        return True

    while not pool.execute(myfunc, args=(0.5,), kwargs={"b": 0.5}):
        pass  # every iteration waits for 0.1 seconds


@pytest.mark.timeout(5)
@pytest.mark.parametrize("query_type", ["cooperative", "noncooperative"])
def test_query_exception(query_type):
    """test executing a query which raises an exception"""
    pool = init_pool(query_type, timeout=0.1)

    def myfunc(a, b=0):
        query(query_type, a + b)
        # Note: this exception will also be printed when the test passed
        raise RuntimeError("tested exeception")

    with pytest.raises(RuntimeError, match="tested exeception"):
        while not pool.execute(myfunc, args=(0.5,), kwargs={"b": 0.5}):
            pass  # every iteration waits for 0.1 seconds


@pytest.mark.parametrize("query_type", ["cooperative", "noncooperative"])
def test_query_wait(query_type):
    """test executing a query which returns a value"""
    pool = init_pool(query_type, timeout=0.1)

    finished = False

    def myfunc():
        nonlocal finished
        query(query_type, 1)
        finished = True

    pool.execute(myfunc)

    assert pool.wait(timeout=3), "query did not finish in time"

    assert finished, "query did not finish"


@pytest.mark.parametrize("query_type", ["cooperative", "noncooperative"])
def test_query_cancel(query_type):
    """test canceling a query"""
    pool = init_pool(query_type, timeout=0.1)

    myfunc_state = "none"

    def myfunc():
        nonlocal myfunc_state
        myfunc_state = "started"

        try:
            query(query_type, 2)
        except BaseException as e:
            myfunc_state = str(type(e).__name__)
            raise
        else:
            myfunc_state = "cancel failed"

    pool.execute(myfunc)

    if pools.GEVENT_PATCHED and query_type == "cooperative":
        assert pool.cancel(timeout=3), "could not cancel the query in time"
        assert myfunc_state == "CancelQuery"
    else:
        with pytest.raises(NotImplementedError, match="not supported"):
            pool.cancel(timeout=3)


@pytest.mark.timeout(5)
@pytest.mark.parametrize("query_type", ["cooperative", "noncooperative"])
def test_query_maxqueries1(query_type):
    """test executing a query which returns a value"""
    pool = init_pool(query_type, timeout=0.1, maxqueries=1)

    def myfunc(a, b=0):
        query(query_type, a + b)
        return True

    while not pool.execute(myfunc, args=(0.1,), kwargs={"b": 0.1}):
        pass  # every iteration waits for 0.1 seconds
    while not pool.execute(myfunc, args=(0.1,), kwargs={"b": 0.2}):
        pass  # every iteration waits for 0.1 seconds

    previous = pool.execute(myfunc, timeout=0, args=(0.1,), kwargs={"b": 0.2})
    assert previous, "pool size not large enough"
    missing = pool.execute(myfunc, timeout=0, args=(0.1,), kwargs={"b": 0.1})
    assert missing is None, "pool size too large"
    assert pool.wait(timeout=3)


@pytest.mark.timeout(5)
@pytest.mark.parametrize("query_type", ["cooperative", "noncooperative"])
def test_query_maxqueries2(query_type):
    """test executing a query which returns a value"""
    pool = init_pool(query_type, timeout=0.1, maxqueries=3)

    def myfunc(a, b=0):
        query(query_type, a + b)
        return True

    while not pool.execute(myfunc, args=(0.1,), kwargs={"b": 0.1}):
        pass  # every iteration waits for 0.1 seconds
    while not pool.execute(myfunc, args=(0.1,), kwargs={"b": 0.2}):
        pass  # every iteration waits for 0.1 seconds

    previous = pool.execute(myfunc, timeout=0, args=(0.1,), kwargs={"b": 0.2})
    assert previous, "pool size not large enough"
    previous = pool.execute(myfunc, timeout=0, args=(0.1,), kwargs={"b": 0.1})
    assert previous, "pool size not large enough"
    assert pool.wait(timeout=3)


@pytest.mark.skipif(
    not pools.GEVENT_PATCHED, reason="requires gevent patched environment"
)
def test_gevent_noncooperative_query_in_noncooperative_pool():
    """test executing a non-cooperative query in a non-cooperative pool (proper usage)"""
    pool = init_pool("noncooperative", timeout=0.1)

    call_count = 0

    def myfunc(**kw):
        nonlocal call_count
        call_count += 1
        if call_count > 1:
            # Note: this exception will also be printed when the test passed
            raise RuntimeError("the gevent loop was blocked")
        query("noncooperative", **kw)

    import gevent

    with pytest.raises(gevent.Timeout, match="the gevent loop was not blocked"):
        with gevent.Timeout(0.5, "the gevent loop was not blocked"):
            while not pool.execute(myfunc, kwargs={"seconds": 2}):
                pass  # every iteration waits for 0.1 seconds

    assert pool.wait(timeout=4)


@pytest.mark.skipif(
    not pools.GEVENT_PATCHED, reason="requires gevent patched environment"
)
def test_gevent_noncooperative_query_in_cooperative_pool():
    """test executing a non-cooperative query in a cooperative pool (improper usage)"""
    pool = init_pool("cooperative", timeout=0.1)

    call_count = 0

    def myfunc(**kw):
        nonlocal call_count
        call_count += 1
        if call_count > 1:
            # Note: this exception will also be printed when the test passed
            raise RuntimeError("the gevent loop was blocked")
        query("noncooperative", **kw)

    import gevent

    with pytest.raises(RuntimeError, match="the gevent loop was blocked"):
        with gevent.Timeout(0.5, "the gevent loop was not blocked"):
            while not pool.execute(myfunc, kwargs={"seconds": 2}):
                pass  # every iteration waits for 0.1 seconds

    assert pool.cancel(timeout=4)


def init_pool(query_type: str, **kw):
    if query_type == "cooperative":
        return pools.CooperativeQueryPool(**kw)
    if query_type == "noncooperative":
        return pools.NonCooperativeQueryPool(**kw)
    raise ValueError(query_type)


def query(query_type: str, seconds: float) -> None:
    if query_type == "cooperative":
        return _cooperative_query(seconds)
    if query_type == "noncooperative":
        return _noncooperative_query(seconds)
    raise ValueError(query_type)


def _cooperative_query(seconds: float):
    print(f"sleep for {seconds} seconds ...")
    try:
        cooperative_sleep(seconds)
    except BaseException as e:
        print(f"sleep interrupted: {e}")
        raise
    print("sleep done")


def _noncooperative_query(seconds):
    print(f"sleep for {seconds} seconds ...")
    noncooperative_sleep(seconds)
    print("sleep done")
