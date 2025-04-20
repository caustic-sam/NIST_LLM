import pytest
from blissdata.beacon import config


def test_beacon_address(mocker):
    mock_environ = mocker.patch("os.environ.get", return_value="foo:200")
    result = config.get_beacon_address()
    assert result == ("foo", 200)
    mock_environ.assert_called_with("BEACON_HOST")


def test_missing_beacon_address(mocker):
    mock_environ = mocker.patch("os.environ.get", return_value=None)
    with pytest.raises(ValueError) as excinfo:
        config.get_beacon_address()
    assert "BEACON_HOST" in excinfo.value.args[0]
    mock_environ.assert_called_with("BEACON_HOST")


def test_wrong_beacon_address_formatting(mocker):
    mock_environ = mocker.patch("os.environ.get", return_value="foo")
    with pytest.raises(ValueError) as excinfo:
        config.get_beacon_address()
    assert "BEACON_HOST" in excinfo.value.args[0]
    assert "foo" in excinfo.value.args[0]
    mock_environ.assert_called_with("BEACON_HOST")
