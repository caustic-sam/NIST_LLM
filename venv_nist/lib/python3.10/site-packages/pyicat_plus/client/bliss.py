import os
from typing import Optional

from blissdata.beacon.files import read_config

from .main import IcatClient


def get_icat_client(
    timeout: Optional[float] = None,
) -> IcatClient:
    beacon_host = os.environ.setdefault("BEACON_HOST", "id00:25000")

    url = f"beacon://{beacon_host}/__init__.yml"
    config = read_config(url).get("icat_servers")

    if not config:
        raise RuntimeError(
            f"Beacon host {beacon_host} does not provide ICAT configuration"
        )
    return IcatClient(
        metadata_urls=config["metadata_urls"],
        elogbook_url=config["elogbook_url"],
        elogbook_token=config["elogbook_token"],
        feedback_timeout=timeout,
        add_files_urls=config["metadata_urls"],
    )
