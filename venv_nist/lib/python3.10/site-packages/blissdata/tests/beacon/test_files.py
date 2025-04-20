import pytest
from blissdata.beacon.files import BeaconFiles, read_config, yaml_load


def test_empty_constructor(mocker):
    mock_socket = mocker.patch("socket.socket")
    mock_config = mocker.patch(
        "blissdata.beacon.config.get_beacon_address", return_value=("foo", 100)
    )
    _ = BeaconFiles()
    mock_config.assert_called_once()
    mock_socket.return_value.connect.assert_called_with(("foo", 100))


def test_param_constructor(mocker):
    mock_socket = mocker.patch("socket.socket")
    mock_config = mocker.patch(
        "blissdata.beacon.config.get_beacon_address", return_value=("foo", 100)
    )
    _ = BeaconFiles("foo", 200)
    mock_config.assert_not_called()
    mock_socket.return_value.connect.assert_called_with(("foo", 200))


def test_get_file(mocker):
    RESPONSE1 = b"\x34\x00\x00\x00\x0E\x00\x00\x001|MY FILE DATA"
    mock_socket = mocker.patch("socket.socket")
    mock_socket.return_value.recv.side_effect = [RESPONSE1]
    service = BeaconFiles("foo", 200)
    result = service.get_file("sessions/__init__.yml")
    mock_socket.return_value.sendall.assert_called_once_with(
        b"2\x00\x00\x00\x17\x00\x00\x001|sessions/__init__.yml"
    )
    assert result == b"MY FILE DATA"


def test_get_tree(mocker):
    RESPONSE1 = b"""\x58\x00\x00\x00\x2C\x00\x00\x001|{"afile":null,"adir":{"anotherfile":null}}"""
    mock_socket = mocker.patch("socket.socket")
    mock_socket.return_value.recv.side_effect = [RESPONSE1]
    service = BeaconFiles("foo", 200)
    result = service.get_tree("")
    mock_socket.return_value.sendall.assert_called_once_with(
        b"V\x00\x00\x00\x02\x00\x00\x001|"
    )
    assert result == {"adir": {"anotherfile": None}, "afile": None}


@pytest.mark.skipif(yaml_load is None, reason="No yaml lib found")
def test_read_config__from_file(tmp_path):
    filename = str(tmp_path / "test.yml")
    with open(filename, "wt") as f:
        f.write("foo:\n    bar: 2000\n")
    result = read_config(filename)
    assert result == {"foo": {"bar": 2000}}


@pytest.mark.skipif(yaml_load is None, reason="No yaml lib found")
def test_read_config__from_beacon(mocker, tmp_path):
    RESPONSE1 = b"\x34\x00\x00\x00\x15\x00\x00\x001|foo:\n    bar: 2000\n"
    mock_socket = mocker.patch("socket.socket")
    mock_socket.return_value.recv.side_effect = [RESPONSE1]
    mock_config = mocker.patch("blissdata.beacon.config.get_beacon_address")
    mock_config.return_value = ("foo", 100)
    result = read_config("beacon:///foo.yml")
    assert result == {"foo": {"bar": 2000}}
    mock_config.assert_called_once()
    mock_socket.return_value.connect.assert_called_with(("foo", 100))


@pytest.mark.skipif(yaml_load is None, reason="No yaml lib found")
def test_read_config__from_explicit_beacon(mocker, tmp_path):
    RESPONSE1 = b"\x34\x00\x00\x00\x15\x00\x00\x001|foo:\n    bar: 2000\n"
    mock_socket = mocker.patch("socket.socket")
    mock_socket.return_value.recv.side_effect = [RESPONSE1]
    mock_config = mocker.patch("blissdata.beacon.config.get_beacon_address")
    result = read_config("beacon://foo:200/foo.yml")
    assert result == {"foo": {"bar": 2000}}
    mock_config.assert_not_called()
    mock_socket.return_value.connect.assert_called_with(("foo", 200))
