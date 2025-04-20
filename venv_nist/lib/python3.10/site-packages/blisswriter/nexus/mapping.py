"""Mapping between blissdata channels and devices info"""

import re
from typing import Dict

from . import devices


def normalize_nexus_name(name: str):
    # TODO: could cause unique names to become non-unique ...
    return re.sub("[^a-zA-Z0-9_]+", "_", name)


def get_device_info(channel_name: str, subscan_devices: Dict) -> Dict:
    """Device information from which the NeXus structure
    of the channel data and metadata is derived."""
    device = subscan_devices.get(channel_name, None)
    if device is None:
        device = devices.update_device(subscan_devices, channel_name)
    return device


def get_primary_dataset_path(channel_name: str, subscan_devices: Dict) -> str:
    """Primary HDF5 dataset in which the channel data is saved."""
    device = get_device_info(channel_name, subscan_devices)
    if device["device_name"]:
        group = normalize_nexus_name(device["device_name"])
        dataset = normalize_nexus_name(device["data_name"])
        return f"instrument/{group}/{dataset}"
    dataset = normalize_nexus_name(device["data_name"])
    return f"measurement/{dataset}"
