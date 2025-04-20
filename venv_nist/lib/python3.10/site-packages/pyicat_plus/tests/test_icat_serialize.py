import pytest
import datetime
from ..client.serialize import serialize_metadata


def test_icat_serialize_valid_data():
    assert serialize_metadata(None) is None
    assert serialize_metadata("string") == "string"
    assert serialize_metadata(b"string") == "string"
    assert serialize_metadata(123.456) == "123.456"

    assert serialize_metadata([]) == ""
    assert serialize_metadata([None]) == ""
    assert serialize_metadata([1, 2]) == "1,2"
    assert serialize_metadata([[1, 2], [3, 4]]) == "1,2 3,4"

    assert serialize_metadata({}) == {}
    assert serialize_metadata({"key": None}) == {}
    assert serialize_metadata({"key": "string"}) == {"key": "string"}
    assert serialize_metadata({"key": b"string"}) == {"key": "string"}
    assert serialize_metadata({"key": 123.456}) == {"key": "123.456"}
    assert serialize_metadata({"key": []}) == {"key": ""}
    assert serialize_metadata({"key": [None]}) == {"key": ""}
    assert serialize_metadata({"key": [1, 2]}) == {"key": "1,2"}
    assert serialize_metadata({"key": [[1, 2], [3, 4]]}) == {"key": "1,2 3,4"}

    now = datetime.datetime.now().astimezone()
    assert serialize_metadata({"key": now}) == {"key": now.isoformat()}


def test_icat_serialize_invalid_data():
    invalid_data = ([{}], [[[1, 2], [3, 4]], [[5, 6], [7, 8]]])
    for data in invalid_data:
        with pytest.raises((TypeError, ValueError)):
            print(serialize_metadata(data))
