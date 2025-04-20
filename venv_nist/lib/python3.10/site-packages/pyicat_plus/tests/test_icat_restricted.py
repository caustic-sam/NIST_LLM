from requests.exceptions import HTTPError

import pytest

from .utils import generate


def test_login(icatplus_restricted_client):
    client, messages = icatplus_restricted_client

    with pytest.raises(RuntimeError, match="Login is required."):
        _ = client.session_id

    with pytest.raises(HTTPError, match="403 Client Error: Authentication failed"):
        _ = client.login("wrong")

    result = client.login("correct")

    assert result["sessionId"] == client.session_id

    assert messages.empty()


def test_get_investigations_by(icatplus_restricted_client, icat_metadata_client):
    client, messages = icatplus_restricted_client
    mclient, mmessages = icat_metadata_client

    with pytest.raises(RuntimeError, match="Login is required."):
        _ = client.get_investigations_by()

    assert client.login("correct")

    investigations = client.get_investigations_by()
    assert isinstance(investigations, list)

    mclient.start_investigation(proposal="hg123", beamline="id00")
    message = mmessages.get(timeout=10)

    investigations = client.get_investigations_by()
    expected = [
        {
            "experiment": "hg123",
            "id": 0,
            "instrument": {
                "name": "id00",
            },
            "proposal": "hg123",
            "startDate": message["investigation"]["startDate"],
        }
    ]
    assert investigations == expected

    assert messages.empty()
    assert mmessages.empty()


def test_get_parcels_by(icatplus_restricted_client):
    client, messages = icatplus_restricted_client

    investigation_id = generate.investigation_id()

    with pytest.raises(RuntimeError, match="Login is required."):
        _ = client.get_parcels_by(investigation_id)

    assert client.login("correct")

    parcels = client.get_parcels_by(investigation_id)

    assert parcels == []
    assert messages.empty()
