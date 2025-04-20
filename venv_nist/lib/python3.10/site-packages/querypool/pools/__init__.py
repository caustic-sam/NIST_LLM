try:
    import gevent.monkey
except ImportError:
    GEVENT_PATCHED = False
else:
    GEVENT_PATCHED = gevent.monkey.is_anything_patched()


if GEVENT_PATCHED:
    from .gevent_cooperative import (
        GeventCooperativeQueryPool as CooperativeQueryPool,
    )  # noqa F401
    from .gevent_noncooperative import (
        GeventNonCooperativeQueryPool as NonCooperativeQueryPool,
    )  # noqa F401
else:
    from .threading import ThreadQueryPool as CooperativeQueryPool  # noqa F401
    from .threading import ThreadQueryPool as NonCooperativeQueryPool  # noqa F401
