"""Get files from Beacon."""

import sys
import json
from typing import Any
from urllib.parse import urlparse, ParseResult
from ._base import BeaconClient

try:
    import ruamel.yaml

    yaml_load = ruamel.yaml.YAML().load
except ImportError:
    try:
        from yaml import safe_load as yaml_load
    except ImportError:
        yaml_load = None


def read_config(url: str) -> Any:
    """
    Read configuration from a url.

    In case of a Beacon url with missing host and port, the Beacon
    server will be found from environment variable `BEACON_HOST`.

    Arguments:
        url: This can be a local yaml file (for example `/path/to/file.yaml`, `file:///path/to/file.yaml`)
             or a Beacon URL (for example `beacon:///path/to/file.yml`, `beacon://id00:25000/path/to/file.yml`).
    Returns:
        A Python dict/list structure
    """
    url2 = _parse_config_url(url)
    if url2.scheme == "beacon":
        return _read_config_beacon(url2)
    elif url2.scheme in ("file", ""):
        return _read_config_yaml(url2)
    else:
        raise ValueError(
            f"Configuration URL scheme '{url2.scheme}' is not supported (Full URL: {url2})"
        )


def _parse_config_url(url: str) -> ParseResult:
    presult = urlparse(url)
    if presult.scheme == "beacon":
        # beacon:///path/to/file.yml
        # beacon://id00:25000/path/to/file.yml
        return presult
    elif presult.scheme in ("file", ""):
        # /path/to/file.yaml
        # file:///path/to/file.yaml
        return presult
    elif sys.platform == "win32" and len(presult.scheme) == 1:
        # c:\\path\\to\\file.yaml
        return urlparse(f"file://{url}")
    else:
        return presult


def _url_to_filename(url: ParseResult) -> str:
    if url.netloc and url.path:
        # urlparse("file://c:/a/b")
        return url.netloc + url.path
    elif url.netloc:
        # urlparse("file://c:\\a\\b")
        return url.netloc
    else:
        return url.path


def _read_config_beacon(url: ParseResult) -> Any:
    if url.netloc:
        host, port_str = url.netloc.split(":")
        port = int(port_str)
    else:
        host = None
        port = None

    # Bliss < 1.11: Beacon cannot handle leading slashes
    file_path = url.path
    while file_path.startswith("/"):
        file_path = file_path[1:]

    beacon = BeaconFiles(host=host, port=port)
    try:
        config = beacon.get_file(file_path)
        if yaml_load is None:
            raise ImportError(
                "No yaml parser available. Try to install 'ruamel.yaml' or 'pyyaml'"
            )
        return yaml_load(config)
    finally:
        beacon.close()


def _read_config_yaml(url: ParseResult) -> Any:
    if yaml_load is None:
        raise ImportError(
            "No yaml parser available. Try to install 'ruamel.yaml' or 'pyyaml'"
        )
    filename = _url_to_filename(url)
    with open(filename, "r") as f:
        return yaml_load(f)


class BeaconFiles(BeaconClient):
    """Provides the API to read files managed by Beacon."""

    CONFIG_GET_FILE = 50
    CONFIG_GET_FILE_FAILED = 51
    CONFIG_GET_FILE_OK = 52

    CONFIG_GET_DB_TREE = 86
    CONFIG_GET_DB_TREE_FAILED = 87
    CONFIG_GET_DB_TREE_OK = 88

    def get_file(self, file_path: str) -> bytes:
        """Returns the binary content of a file from the Beacon configuration
        file repository."""
        with self._lock:
            response_type, data = self._request(self.CONFIG_GET_FILE, file_path)
            if response_type == self.CONFIG_GET_FILE_OK:
                return data
            elif response_type == self.CONFIG_GET_FILE_FAILED:
                raise RuntimeError(data.decode())
            raise RuntimeError(f"Unexpected Beacon response type {response_type}")

    def get_tree(self, base_path: str = "") -> dict:
        """Returns the file tree from a base path of the Beacon configuration
        file repository.

        Return: A nested dictionary structure, where a file is a mapping
                `filename: None`, an a directory is mapping of a dirname and a
                nested dictionary.
        """
        with self._lock:
            response_type, data = self._request(self.CONFIG_GET_DB_TREE, base_path)
            if response_type == self.CONFIG_GET_DB_TREE_OK:
                return json.loads(data)
            elif response_type == self.CONFIG_GET_DB_TREE_FAILED:
                raise RuntimeError(data.decode())
            raise RuntimeError(f"Unexpected Beacon response type {response_type}")
