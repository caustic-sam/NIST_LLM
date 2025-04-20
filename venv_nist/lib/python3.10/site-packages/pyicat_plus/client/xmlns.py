import os
import socket
import datetime
from typing import Union, Optional

import xml.etree.ElementTree as etree

from .serialize import serialize_metadata
from importlib.metadata import version as get_version

release = get_version("pyicat_plus")

ICAT_NAMESPACE_URL = "http://www.esrf.fr/icat"

etree.register_namespace("tns", ICAT_NAMESPACE_URL)


def root_node(name: str, **kw):
    return etree.Element(f"{{{ICAT_NAMESPACE_URL}}}{name}", **kw)


def child_node(parent, name: str, **kw):
    return etree.SubElement(parent, f"{{{ICAT_NAMESPACE_URL}}}{name}", **kw)


def encode_node_data(data) -> str:
    text = serialize_metadata(data)
    if not isinstance(text, str):
        raise ValueError(data)
    return text


def data_node(parent, name: str, data, **kw):
    node = child_node(parent, name, **kw)
    node.text = encode_node_data(data)


def parameter_node(parent, name: str, value, **kw):
    node = child_node(parent, "parameter", **kw)
    data_node(node, "name", name)
    data_node(node, "value", value)


def dataset_as_xml(
    beamline: str, proposal: str, dataset: str, path: str, metadata: dict = None
):
    assert beamline, "ICAT requires the beamline name"
    assert proposal, "ICAT requires the proposal name"
    assert dataset, "ICAT requires the dataset name"
    assert path, "ICAT requires the dataset path"
    if metadata is None:
        metadata = dict()

    # Required metadata
    assert "Sample_name" in metadata, "ICAT metadata field 'Sample_name' is missing"

    # Metadata with defaults
    if "startDate" not in metadata:
        metadata["startDate"] = datetime.datetime.now().astimezone()
    if "endDate" not in metadata:
        metadata["endDate"] = datetime.datetime.now().astimezone()

    metadata.setdefault("machine", socket.getfqdn())
    metadata.setdefault("software", "pyicat-plus_v" + release)

    # Normalize fields
    path = os.path.normpath(path)
    input_datasets = metadata.get("input_datasets", None)
    if input_datasets:
        metadata["input_datasets"] = [
            os.path.normpath(input_dataset) for input_dataset in input_datasets
        ]

    # Create XML tree
    root = root_node("dataset", attrib={"complete": "true"})
    data_node(root, "investigation", proposal)
    data_node(root, "instrument", beamline)
    data_node(root, "name", dataset)
    data_node(root, "location", path)

    metadata = serialize_metadata(metadata)
    for name, value in metadata.items():
        parameter_node(root, name, value)
        # Metadata included in the XML tree
        if name == "Sample_name":
            sample = child_node(root, "sample")
            data_node(sample, "name", value)
        elif name == "startDate":
            data_node(root, "startDate", value)
        elif name == "endDate":
            data_node(root, "endDate", value)

    return root


def investigation_as_xml(
    beamline: str,
    proposal: str,
    start_datetime: Optional[Union[datetime.datetime, str]] = None,
    end_datetime: Optional[Union[datetime.datetime, str]] = None,
    investigation_id: Optional[str] = None,
):
    root = root_node("investigation")
    data_node(root, "experiment", proposal)
    data_node(root, "instrument", beamline)
    if investigation_id is not None:
        data_node(root, "investigationId", investigation_id)
    if start_datetime is None:
        start_datetime = datetime.datetime.now().astimezone()
    data_node(root, "startDate", start_datetime)
    if end_datetime is not None:
        data_node(root, "endDate", end_datetime)
    return root
