import json
from typing import Optional, List

from .messaging import IcatMessagingClient
from . import defaults


class IcatUpdateMetadataClient:
    """Client for updating the metadata linked to datasets."""

    def __init__(
        self,
        queue_urls: Optional[List[str]] = None,
        queue_name: Optional[str] = None,
        monitor_port: Optional[int] = None,
        timeout: Optional[float] = None,
    ):
        if queue_name is None:
            queue_name = defaults.UPDATE_METADATA_QUEUE
        if queue_urls is None:
            queue_urls = defaults.UPDATE_METADATA_BROKERS
        self._client = IcatMessagingClient(
            queue_urls, queue_name, monitor_port=monitor_port, timeout=timeout
        )

    def disconnect(self):
        self._client.disconnect()

    def send_update_metadata(
        self,
        proposal: str,
        beamline: str,
        dataset_paths: List[str],
        metadata_name: str,
        metadata_value: str,
    ):
        assert proposal, "ICAT requires the proposal name"
        assert beamline, "ICAT requires the beamline name"
        assert dataset_paths, "ICAT requires the dataset paths"
        assert metadata_name, "ICAT requires the metadata name"
        assert metadata_value, "ICAT requires the metadata value"
        root = {
            "proposal": proposal,
            "beamline": beamline,
            "datasetPaths": dataset_paths,
            "metadataName": metadata_name,
            "metadataValue": metadata_value,
        }
        data = json.dumps(root).encode("utf-8")
        self._client.send(data)

    def check_health(self):
        """Raises an exception when not healthy"""
        self._client.check_health()
