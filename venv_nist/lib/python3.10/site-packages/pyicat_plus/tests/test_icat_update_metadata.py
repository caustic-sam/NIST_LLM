import pytest
import json


def send_update_metadata(client, value):
    client.send_update_metadata(
        proposal=value["proposal"],
        beamline=value["beamline"],
        dataset_paths=value["datasetPaths"],
        metadata_value=value["metadataValue"],
        metadata_name=value["metadataName"],
    )


def test_send_update_metadata_sample(icat_update_metadata_client):
    client, messages = icat_update_metadata_client
    client.check_health()
    expected = {
        "proposal": "sc415",
        "beamline": "id00",
        "datasetPaths": ["/data/visitor/sc415/id00/20230101/RAW_DATA/sample/dataset"],
        "metadataName": "Sample_name",
        "metadataValue": "newsamplename",
    }
    send_update_metadata(client, expected)
    message = messages.get(timeout=10)
    message = json.loads(message)
    assert message == expected
    assert messages.empty()


def test_send_update_metadata_sample_with_multiple_datasets(
    icat_update_metadata_client,
):
    client, messages = icat_update_metadata_client
    client.check_health()
    expected = {
        "proposal": "sc415",
        "beamline": "id00",
        "datasetPaths": [
            "/data/visitor/sc415/id00/20230101/RAW_DATA/sample/dataset1",
            "/data/visitor/sc415/id00/20230101/RAW_DATA/sample/dataset2",
        ],
        "metadataName": "Sample_name",
        "metadataValue": "newsamplename",
    }
    send_update_metadata(client, expected)
    message = messages.get(timeout=10)
    message = json.loads(message)
    assert message == expected
    assert messages.empty()


def test_send_update_metadata_raises_error_if_proposal_is_missing(
    icat_update_metadata_client,
):
    client, messages = icat_update_metadata_client
    with pytest.raises(AssertionError, match="ICAT requires the proposal"):
        client.send_update_metadata(
            proposal=None,
            beamline="id00",
            dataset_paths="/data/visitor/sc415/id00/20230101/RAW_DATA/sample/dataset/",
            metadata_value="newsamplename",
            metadata_name="Sample_name",
        )


def test_send_update_metadata_raises_error_if_beamline_is_missing(
    icat_update_metadata_client,
):
    client, messages = icat_update_metadata_client
    with pytest.raises(AssertionError, match="ICAT requires the beamline"):
        client.send_update_metadata(
            proposal="sc415",
            beamline=None,
            dataset_paths="/data/visitor/sc415/id00/20230101/RAW_DATA/sample/dataset/",
            metadata_value="newsamplename",
            metadata_name="Sample_name",
        )


def test_send_update_metadata_raises_error_if_dataset_paths_is_missing(
    icat_update_metadata_client,
):
    client, messages = icat_update_metadata_client
    with pytest.raises(AssertionError, match="ICAT requires the dataset path"):
        client.send_update_metadata(
            proposal="sc415",
            beamline="id00",
            dataset_paths=None,
            metadata_value="newsamplename",
            metadata_name="Sample_name",
        )


def test_send_update_metadata_raises_error_if_dataset_paths_is_empty(
    icat_update_metadata_client,
):
    client, messages = icat_update_metadata_client
    with pytest.raises(AssertionError, match="ICAT requires the dataset path"):
        client.send_update_metadata(
            proposal="sc415",
            beamline="id00",
            dataset_paths=[],
            metadata_value="newsamplename",
            metadata_name="Sample_name",
        )


def test_send_update_metadata_raises_error_if_metadata_value_is_missing(
    icat_update_metadata_client,
):
    client, messages = icat_update_metadata_client
    with pytest.raises(AssertionError, match="ICAT requires the metadata value"):
        client.send_update_metadata(
            proposal="sc415",
            beamline="id00",
            dataset_paths="/data/visitor/sc415/id00/20230101/RAW_DATA/sample/dataset/",
            metadata_value=None,
            metadata_name="Sample_name",
        )


def test_send_update_metadata_raises_error_if_metadata_name_is_missing(
    icat_update_metadata_client,
):
    client, messages = icat_update_metadata_client
    with pytest.raises(AssertionError, match="ICAT requires the metadata name"):
        client.send_update_metadata(
            proposal="sc415",
            beamline="id00",
            dataset_paths="/data/visitor/sc415/id00/20230101/RAW_DATA/sample/dataset/",
            metadata_value="newsamplename",
            metadata_name=None,
        )
