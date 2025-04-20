import pytest

from ..client.archive import StatusLevel, StatusType


def test_send_archive_status(icat_archive_client):
    client, messages = icat_archive_client
    client.check_health()
    client.send_archive_status(
        dataset_id=12345,
        type=StatusType.ARCHIVING,
        level=StatusLevel.INFO,
        message="dataset archived",
    )
    messages.get(timeout=10)
    assert messages.empty()


def test_send_archive_status_raises_error_if_dataset_id_missing(icat_archive_client):
    client, messages = icat_archive_client
    with pytest.raises(AssertionError, match="ICAT requires the datasetId"):
        client.send_archive_status(
            dataset_id=None,
            type=StatusType.ARCHIVING,
            level=StatusLevel.INFO,
            message="dataset archived",
        )


def test_send_archive_status_raises_error_if_type_missing(icat_archive_client):
    client, messages = icat_archive_client
    with pytest.raises(AssertionError, match="ICAT requires the type"):
        client.send_archive_status(
            dataset_id=123,
            type=None,
            level=StatusLevel.INFO,
            message="dataset archived",
        )


def test_send_archive_status_raises_error_if_level_missing(icat_archive_client):
    client, messages = icat_archive_client
    with pytest.raises(AssertionError, match="ICAT requires the level"):
        client.send_archive_status(
            dataset_id=123,
            type=StatusType.RESTORATION,
            level=None,
            message="dataset restored",
        )
