import json
from typing import Optional, List
from .messaging import IcatMessagingClient
from . import defaults


class IcatAddFilesClient:
    """Client for adding missing files to an existing dataset"""

    def __init__(
        self,
        queue_urls: Optional[List[str]] = None,
        queue_name: Optional[str] = None,
        monitor_port: Optional[int] = None,
        timeout: Optional[float] = None,
    ):
        if queue_name is None:
            queue_name = defaults.ADD_FILES_QUEUE
        if queue_urls is None:
            queue_urls = defaults.ADD_FILES_BROKERS
        self._client = IcatMessagingClient(
            queue_urls, queue_name, monitor_port=monitor_port, timeout=timeout
        )

    def disconnect(self):
        self._client.disconnect()

    def add_files(
        self,
        dataset_id: int,
    ):
        assert dataset_id, "ICAT requires the datasetId"
        root = {"datasetId": dataset_id}
        data = json.dumps(root).encode("utf-8")
        self._client.send(data)

    def check_health(self):
        """Raises an exception when not healthy"""
        self._client.check_health()
