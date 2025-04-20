import enum


class ScanState(enum.IntEnum):
    """Scan state with ordering. The names are used in serialization (Redis)."""

    IDLE = 0
    PREPARING = 1
    STARTING = 2
    STOPPING = 3
    DONE = 4
    USER_ABORTED = 5
    KILLED = 6
