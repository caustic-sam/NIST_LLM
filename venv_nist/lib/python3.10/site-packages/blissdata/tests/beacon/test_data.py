import pytest
from blissdata.beacon.data import BeaconData


def test_empty_constructor(mocker):
    mock_socket = mocker.patch("socket.socket")
    mock_config = mocker.patch(
        "blissdata.beacon.config.get_beacon_address", return_value=("foo", 100)
    )
    _ = BeaconData()
    mock_config.assert_called_once()
    mock_socket.return_value.connect.assert_called_with(("foo", 100))


def test_param_constructor(mocker):
    mock_socket = mocker.patch("socket.socket")
    mock_config = mocker.patch(
        "blissdata.beacon.config.get_beacon_address", return_value=("foo", 100)
    )
    _ = BeaconData("foo", 200)
    mock_config.assert_not_called()
    mock_socket.return_value.connect.assert_called_with(("foo", 200))


def test_get_redis_db(mocker):
    RESPONSE1 = b"\x1f\x00\x00\x00C\x00\x00\x00localhost:/tmp/demo_resourcesnd1ti3cr/configuration/redis_demo.sock"
    mock_socket = mocker.patch("socket.socket")
    mock_socket.return_value.recv.side_effect = [RESPONSE1]
    service = BeaconData("foo", 200)
    result = service.get_redis_db()
    mock_socket.return_value.sendall.assert_called_once_with(
        b"\x1e\x00\x00\x00\x00\x00\x00\x00"
    )
    assert result == "unix:///tmp/demo_resourcesnd1ti3cr/configuration/redis_demo.sock"


def test_get_redis_data_db(mocker):
    RESPONSE1 = b'"\x00\x00\x00J\x00\x00\x001|localhost|/tmp/demo_resourcesnd1ti3cr/configuration/redis_data_demo.sock'
    mock_socket = mocker.patch("socket.socket")
    mock_socket.return_value.recv.side_effect = [RESPONSE1]
    service = BeaconData("foo", 200)
    result = service.get_redis_data_db()
    mock_socket.return_value.sendall.assert_called_once_with(
        b"\x20\x00\x00\x00\x02\x00\x00\x001|"
    )
    assert (
        result
        == "unix:///tmp/demo_resourcesnd1ti3cr/configuration/redis_data_demo.sock"
    )


def test_get(mocker):
    RESPONSE1 = b"\x90\x00\x00\x00\x05\x00\x00\x001|BAR"
    mock_socket = mocker.patch("socket.socket")
    mock_socket.return_value.recv.side_effect = [RESPONSE1]
    service = BeaconData("foo", 200)
    result = service.get("FOO2")
    mock_socket.return_value.sendall.assert_called_once_with(
        b"\x8f\x00\x00\x00\x06\x00\x00\x001|FOO2"
    )
    assert result == "BAR"


def test_get__missing_key(mocker):
    RESPONSE1 = b"\x92\x00\x00\x00\x01\x00\x00\x001"
    mock_socket = mocker.patch("socket.socket")
    mock_socket.return_value.recv.side_effect = [RESPONSE1]
    service = BeaconData("foo", 200)
    with pytest.raises(KeyError):
        service.get("FOO2")
    mock_socket.return_value.sendall.assert_called_once_with(
        b"\x8f\x00\x00\x00\x06\x00\x00\x001|FOO2"
    )


def test_get__missing_key_with_default(mocker):
    RESPONSE1 = b"\x92\x00\x00\x00\x01\x00\x00\x001"
    mock_socket = mocker.patch("socket.socket")
    mock_socket.return_value.recv.side_effect = [RESPONSE1]
    service = BeaconData("foo", 200)
    result = service.get("FOO2", "BAR")
    mock_socket.return_value.sendall.assert_called_once_with(
        b"\x8f\x00\x00\x00\x06\x00\x00\x001|FOO2"
    )
    assert result == "BAR"


def test_set(mocker):
    RESPONSE1 = b"\x8d\x00\x00\x00\x01\x00\x00\x001"
    mock_socket = mocker.patch("socket.socket")
    mock_socket.return_value.recv.side_effect = [RESPONSE1]
    service = BeaconData("foo", 200)
    result = service.set("FOO2", "BAR")
    mock_socket.return_value.sendall.assert_called_once_with(
        b"\x8c\x00\x00\x00\n\x00\x00\x001|FOO2|BAR"
    )
    assert result is None
