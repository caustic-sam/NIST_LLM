import os
from pathlib import Path

import pytest

from ..concurrency import Empty
from .utils.message import assert_dataset_message
from .utils.message import assert_investigation_message
from .utils import generate


def test_start_investigation(icat_metadata_client):
    client, messages = icat_metadata_client
    client.check_health()
    client.start_investigation(proposal="hg123", beamline="id00")
    message = messages.get(timeout=10)
    assert messages.empty()

    expected = {"investigation": {"experiment": "hg123", "instrument": "id00"}}
    assert_investigation_message(message, expected)


def test_start_bad_investigation(icat_metadata_client):
    client, messages = icat_metadata_client
    client.check_health()
    client.start_investigation(proposal="hg666", beamline="id00")
    with pytest.raises(Empty):
        messages.get(timeout=2)


def test_send_metadata(icat_metadata_client):
    client, messages = icat_metadata_client
    client.send_metadata(
        proposal="hg123",
        beamline="id00",
        dataset="datasetname",
        path=_dummy_path("dataset"),
        metadata={"Sample_name": "samplename", "field1": "value1", "field2": [1, 2, 3]},
    )
    message = messages.get(timeout=10)
    assert messages.empty()

    expected = {
        "dataset": {
            "@complete": "true",
            "@xmlns": {"tns": "http://www.esrf.fr/icat"},
            "instrument": "id00",
            "investigation": "hg123",
            "location": str(_dummy_path("dataset")),
            "name": "datasetname",
            "parameter": [
                {"name": "Sample_name", "value": "samplename"},
                {"name": "field1", "value": "value1"},
                {"name": "field2", "value": "1,2,3"},
            ],
            "sample": {"name": "samplename"},
        }
    }
    assert_dataset_message(message, expected)


def test_send_metadata_via_file(icat_metadata_client, tmpdir):
    store_filename = tmpdir / "test.xml"

    client, messages = icat_metadata_client
    client.store_metadata(
        str(store_filename),
        proposal="hg123",
        beamline="id00",
        dataset="datasetname",
        path=_dummy_path("dataset"),
        metadata={"Sample_name": "samplename", "field1": "value1", "field2": [1, 2, 3]},
    )

    with pytest.raises(Empty):
        message = messages.get(timeout=1)

    assert store_filename.exists()

    client.send_metadata_from_file(str(store_filename))

    message = messages.get(timeout=10)
    assert messages.empty()

    expected = {
        "dataset": {
            "@complete": "true",
            "@xmlns": {"tns": "http://www.esrf.fr/icat"},
            "instrument": "id00",
            "investigation": "hg123",
            "location": str(_dummy_path("dataset")),
            "name": "datasetname",
            "parameter": [
                {"name": "Sample_name", "value": "samplename"},
                {"name": "field1", "value": "value1"},
                {"name": "field2", "value": "1,2,3"},
            ],
            "sample": {"name": "samplename"},
        }
    }
    assert_dataset_message(message, expected)


def test_send_missing_data(icat_metadata_client):
    client, messages = icat_metadata_client
    with pytest.raises(AssertionError, match="ICAT requires the beamline name"):
        client.send_metadata(
            proposal=None,
            beamline=None,
            dataset=None,
            path=None,
            metadata=None,
        )


def test_send_missing_metadata(icat_metadata_client):
    client, messages = icat_metadata_client
    with pytest.raises(
        AssertionError, match="ICAT metadata field 'Sample_name' is missing"
    ):
        client.send_metadata(
            proposal="hg123",
            beamline="id00",
            dataset="datasetname",
            path=_dummy_path("dataset"),
            metadata={},
        )


def test_send_metadata_with_machine_software(icat_metadata_client):
    client, messages = icat_metadata_client
    client.send_metadata(
        proposal="hg123",
        beamline="id00",
        dataset="datasetname",
        path=_dummy_path("dataset"),
        metadata={
            "Sample_name": "samplename",
            "field1": "value1",
            "field2": [1, 2, 3],
            "machine": "mymachine",
            "software": "mysoftware_version",
        },
    )
    message = messages.get(timeout=10)
    assert messages.empty()

    expected = {
        "dataset": {
            "@complete": "true",
            "@xmlns": {"tns": "http://www.esrf.fr/icat"},
            "instrument": "id00",
            "investigation": "hg123",
            "location": str(_dummy_path("dataset")),
            "name": "datasetname",
            "parameter": [
                {"name": "Sample_name", "value": "samplename"},
                {"name": "field1", "value": "value1"},
                {"name": "field2", "value": "1,2,3"},
                {"name": "machine", "value": "mymachine"},
                {"name": "software", "value": "mysoftware_version"},
            ],
            "sample": {"name": "samplename"},
        }
    }
    assert_dataset_message(message, expected)


def test_reschedule_investigation(icat_metadata_client):
    client, messages = icat_metadata_client
    client.check_health()
    investigation_id = generate.investigation_id()
    client.reschedule_investigation(investigation_id=investigation_id)
    message = messages.get(timeout=10)
    assert messages.empty()

    expected = {
        "investigation": {
            "experiment": None,
            "instrument": None,
            "investigationId": investigation_id,
        }
    }
    assert_investigation_message(message, expected)


@pytest.mark.parametrize("path_defect", ["none", "trailing_slash", "up"])
def test_send_metadata_path_defects(icat_metadata_client, path_defect):
    expected_path = _dummy_path("dataset")
    expected_input_datasets = [_dummy_path("dataset1"), _dummy_path("dataset2")]

    if path_defect == "trailing_slash":
        path = _add_trailing_slash(expected_path)
        assert path.endswith(os.path.sep)
        input_datasets = [_add_trailing_slash(s) for s in expected_input_datasets]
    elif path_defect == "up":
        path = _add_up(expected_path)
        input_datasets = [_add_up(s) for s in expected_input_datasets]
    else:
        path = expected_path
        input_datasets = expected_input_datasets

    client, messages = icat_metadata_client
    client.send_metadata(
        proposal="hg123",
        beamline="id00",
        dataset="datasetname",
        path=path,
        metadata={
            "Sample_name": "samplename",
            "field1": "value1",
            "field2": [1, 2, 3],
            "input_datasets": input_datasets,
        },
    )

    message = messages.get(timeout=10)
    assert messages.empty()

    expected_path = str(expected_path)
    input_datasets = ",".join([str(s) for s in expected_input_datasets])

    expected = {
        "dataset": {
            "@complete": "true",
            "@xmlns": {"tns": "http://www.esrf.fr/icat"},
            "instrument": "id00",
            "investigation": "hg123",
            "location": str(expected_path),
            "name": "datasetname",
            "parameter": [
                {"name": "Sample_name", "value": "samplename"},
                {"name": "field1", "value": "value1"},
                {"name": "field2", "value": "1,2,3"},
                {"name": "input_datasets", "value": input_datasets},
            ],
            "sample": {"name": "samplename"},
        }
    }
    assert_dataset_message(message, expected)


def _add_trailing_slash(path: Path) -> str:
    return str(path / "_")[:-1]


def _add_up(path: Path) -> Path:
    return path / "_" / ".."


def _dummy_path(dirname: str) -> Path:
    return Path.home() / dirname
