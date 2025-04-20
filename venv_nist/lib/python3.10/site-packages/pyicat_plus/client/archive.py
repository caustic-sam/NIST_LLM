import json
from enum import Enum
from typing import Optional, List

from .messaging import IcatMessagingClient
from . import defaults


class StatusType(Enum):
    ARCHIVING = "archiving"
    RESTORATION = "restoration"


class StatusLevel(Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class IcatArchiveStatusClient:
    """Client for storing archive and restoration status in ICAT."""

    def __init__(
        self,
        queue_urls: Optional[List[str]] = None,
        queue_name: Optional[str] = None,
        monitor_port: Optional[int] = None,
        timeout: Optional[float] = None,
    ):
        if queue_name is None:
            queue_name = defaults.ARCHIVE_QUEUE
        if queue_urls is None:
            queue_urls = defaults.ARCHIVE_BROKERS
        self._client = IcatMessagingClient(
            queue_urls, queue_name, monitor_port=monitor_port, timeout=timeout
        )

    def disconnect(self):
        self._client.disconnect()

    def send_archive_status(
        self, dataset_id: int, type: StatusType, level: StatusLevel, message: str
    ):
        assert dataset_id, "ICAT requires the datasetId"
        assert type, "ICAT requires the type"
        assert level, "ICAT requires the level"
        root = {
            "datasetId": dataset_id,
            "type": type.value,
            "level": level.value,
            "message": message,
        }
        data = json.dumps(root).encode("utf-8")
        self._client.send(data)

    def check_health(self):
        """Raises an exception when not healthy"""
        self._client.check_health()
