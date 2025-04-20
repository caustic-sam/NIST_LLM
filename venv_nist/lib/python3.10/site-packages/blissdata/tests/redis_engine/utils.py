import redis
from contextlib import contextmanager


@contextmanager
def redis_config_ctx(redis_url, redis_config: dict):
    """Apply and restore redis config fields"""
    red = redis.Redis.from_url(redis_url)
    if redis_config:
        # backup config
        config_backup = red.config_get(*redis_config.keys())
        # apply new one
        flat_config = [arg for key_value in redis_config.items() for arg in key_value]
        red.config_set(*flat_config)
    try:
        yield
    finally:
        if redis_config:
            # restore config
            flat_config = [
                arg for key_value in config_backup.items() for arg in key_value
            ]
            red.config_set(*flat_config)
