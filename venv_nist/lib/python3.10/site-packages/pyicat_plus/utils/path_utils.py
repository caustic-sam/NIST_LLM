import os


def basename(path: str) -> str:
    while path and not os.path.basename(path):
        path = os.path.dirname(path)
    return os.path.basename(path)


def dirname(path: str) -> str:
    while path and not os.path.basename(path):
        path = os.path.dirname(path)
    return os.path.dirname(path)


def split(path: str) -> str:
    while path and not os.path.basename(path):
        path = os.path.dirname(path)
    return path.split(os.sep)


def markdir(path: str) -> str:
    if path and os.path.basename(path):
        path = os.path.join(path, "")
    return path
