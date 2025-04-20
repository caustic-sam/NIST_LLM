import pytest


@pytest.fixture
def dummy_id():
    return {
        "name": "dummy_scan",
        "number": 42,
        "data_policy": "dummy_policy",
        "session": "dummy_session",
        "proposal": "dummy_proposal",
        "collection": "dummy_collection",
        "dataset": "dummy_dataset",
    }
