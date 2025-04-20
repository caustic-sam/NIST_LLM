import socket
from typing import Optional
from importlib.metadata import version as get_version

release = get_version("pyicat_plus")


def check_parameter_exists(dataset, param_name):
    return any(param["name"] == param_name for param in dataset["parameter"])


def assert_dataset_message(message: dict, expected: dict):
    expected["dataset"]["startDate"] = message["dataset"]["startDate"]
    expected["dataset"]["endDate"] = message["dataset"]["endDate"]
    expected["dataset"]["parameter"].append(_get_parameter(message, "startDate"))
    expected["dataset"]["parameter"].append(_get_parameter(message, "endDate"))
    if not check_parameter_exists(expected["dataset"], "machine"):
        expected["dataset"]["parameter"].append(
            {"name": "machine", "value": socket.getfqdn()}
        )
    if not check_parameter_exists(expected["dataset"], "software"):
        expected["dataset"]["parameter"].append(
            {"name": "software", "value": "pyicat-plus_v" + release}
        )
    expected["dataset"]["parameter"] = sorted(
        expected["dataset"]["parameter"], key=lambda adict: adict["name"]
    )
    message["dataset"]["parameter"] = sorted(
        message["dataset"]["parameter"], key=lambda adict: adict["name"]
    )
    assert message == expected


def assert_investigation_message(message: dict, expected: dict):
    expected["investigation"]["startDate"] = message["investigation"]["startDate"]
    assert message == expected


def _get_parameter(root: dict, parameter_name: str) -> Optional[dict]:
    for parameter in root["dataset"]["parameter"]:
        if parameter["name"] == parameter_name:
            return parameter
