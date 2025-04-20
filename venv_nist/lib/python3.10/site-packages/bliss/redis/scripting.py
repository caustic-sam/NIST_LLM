"""Utilities for Redis server-side scripting
"""

_SCRIPTS = set()


def register_script(redisproxy, script_name: str, script: str) -> None:
    """Local registration. The registration with the Redis server is
    done on first usage.
    """
    if script_name in _SCRIPTS:
        return
    _SCRIPTS.add(script_name)
    redisproxy.function_load(code=script, replace=True)


def evaluate_script(redisproxy, script_name: str, keys=tuple(), args=tuple()):
    """Evaluate a server-side Redis script"""
    return redisproxy.fcall(script_name, len(keys), *keys, *args)
