import enum
from typing import NamedTuple


@enum.unique
class ChannelDataType(enum.IntEnum):
    SCAN_REFERENCE = enum.auto()
    LIMA_STATUS = enum.auto()
    NUMERIC_DATA = enum.auto()


class Channel(NamedTuple):
    name: str
    data_type: ChannelDataType
    info: dict
