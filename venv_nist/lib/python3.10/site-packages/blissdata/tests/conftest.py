import os
from redis import Redis
from pytest import fixture
from pytest_redis.factories.proc import redis_proc

from blissdata.redis_engine.store import DataStore


redis_db = redis_proc(
    modules=[
        os.path.join(os.getenv("CONDA_PREFIX", "/usr"), "lib", "librejson.so"),
        os.path.join(os.getenv("CONDA_PREFIX", "/usr"), "lib", "redisearch.so"),
    ],
)


@fixture
def redis_url(redis_db):
    url = f"redis://{redis_db.host}:{redis_db.port}"
    Redis.from_url(url).flushall()
    _ = DataStore(url, init_db=True)
    yield url


@fixture
def data_store(redis_url):
    data_store = DataStore(redis_url)
    try:
        yield data_store
    finally:
        data_store._redis.connection_pool.disconnect()
        data_store._redis = None
