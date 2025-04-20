import os
from typing import Optional


def find_existing(path: str) -> Optional[str]:
    """Returns `path` or one of its parent directories."""
    path = os.path.normpath(path)
    while not os.path.exists(path):
        previous = path
        path = os.path.dirname(path)
        if path == previous:
            break
    if not os.path.exists(path):
        return
    return path


def has_required_disk_space(
    path: str,
    required_disk_space: float,
    query_pool=None,
) -> bool:
    """
    :param path: may not exist yet
    :param required_disk_space: is MB
    :param query_pool:
    :returns: also returns `True` when no path was found or
              the call did not finish within the query pool's
              timeout.
    """
    if required_disk_space <= 0:
        return True
    path = find_existing(path)
    if not path:
        return True
    stat = statvfs(path, query_pool=query_pool)
    if stat is None:
        return True
    free_space = stat.f_frsize * stat.f_bavail / 1024**2
    return free_space >= required_disk_space


def statvfs(path, query_pool=None):
    """os.statvfs could take several seconds on NFS"""
    if query_pool is None:
        return os.statvfs(path)
    return query_pool.execute(os.statvfs, args=(path,), default=None)


def has_write_permissions(path: str) -> bool:
    if os.path.exists(path):
        return os.access(path, os.W_OK)
    # Check whether we can create the path
    path = os.path.dirname(os.path.normpath(path))
    path = find_existing(path)
    if path and os.path.isdir(path):
        return os.access(path, os.W_OK)
    return False
