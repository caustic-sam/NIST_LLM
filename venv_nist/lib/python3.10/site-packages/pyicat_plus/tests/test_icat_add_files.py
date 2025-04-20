import pytest
import json


def add_files(client, value):
    client.add_files(
        dataset_id=value["datasetId"],
    )


def test_add_files(icat_add_files_client):
    client, messages = icat_add_files_client
    client.check_health()
    expected = {
        "datasetId": 1234,
    }
    add_files(client, expected)
    message = messages.get(timeout=10)
    message = json.loads(message)
    assert message == expected
    assert messages.empty()


def test_add_files_raises_error_if_dataset_id_is_missing(
    icat_add_files_client,
):
    client, messages = icat_add_files_client
    with pytest.raises(AssertionError, match="ICAT requires the datasetId"):
        client.add_files(
            dataset_id=None,
        )
