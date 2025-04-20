"""Get Bliss data information from Beacon."""

import struct
from ._base import BeaconClient
from ._base import IncompleteBeaconMessage
from ._utils import Undefined


class BeaconData(BeaconClient):
    """Provides the API to read the redis databases urls."""

    REDIS_QUERY = 30
    REDIS_QUERY_ANSWER = 31

    REDIS_DATA_SERVER_QUERY = 32
    REDIS_DATA_SERVER_FAILED = 33
    REDIS_DATA_SERVER_OK = 34

    KEY_SET = 140
    KEY_SET_OK = 141
    KEY_SET_FAILED = 142
    KEY_GET = 143
    KEY_GET_OK = 144
    KEY_GET_FAILED = 145
    KEY_GET_UNDEFINED = 146

    def get_redis_db(self) -> str:
        """Returns the URL of the Redis database that contains the Bliss settings.
        For example 'redis://foobar:25001' or 'unix:///tmp/redis.sock'."""
        while True:
            try:
                message_type, message, data = self._raw_get_redis_db()
                break
            except BrokenPipeError:
                self.reconnect()

        if message_type != self.REDIS_QUERY_ANSWER:
            raise RuntimeError(f"Unexpected message type '{message_type}'")
        host, port = message.decode().split(":")
        try:
            return f"redis://{host}:{int(port)}"
        except ValueError:
            return f"unix://{port}"

    def get_redis_data_db(self) -> str:
        """Returns the URL of the Redis database that contains the Bliss scan data.
        For example 'redis://foobar:25002' or 'unix:///tmp/redis_data.sock'."""
        response_type, data = self._request(self.REDIS_DATA_SERVER_QUERY, "")
        if response_type == self.REDIS_DATA_SERVER_OK:
            host, port = data.decode().split("|")[:2]
            try:
                return f"redis://{host}:{int(port)}"
            except ValueError:
                return f"unix://{port}"
        elif response_type == self.REDIS_DATA_SERVER_FAILED:
            raise RuntimeError(data.decode())
        raise RuntimeError(f"Unexpected Beacon response type {response_type}")

    def _raw_get_redis_db(self):
        """redis_db cannot be retrieved with self._request(). Some commands are
        custom among the already custom beacon protocol."""
        msg = b"%s%s" % (struct.pack("<ii", self.REDIS_QUERY, 0), b"")
        self._connection.sendall(msg)
        data = b""
        while True:
            raw_data = self._connection.recv(16 * 1024)
            if not raw_data:
                # socket closed on server side (would have raised otherwise)
                raise BrokenPipeError
            data = b"%s%s" % (data, raw_data)
            try:
                return self._unpack_message(data)
            except IncompleteBeaconMessage:
                continue

    def get(self, key: str, default=Undefined):
        """Returns the value of the `key` stored in Beacon.

        Arguments:
            key: Name of the key to read
            default: The default value to return if the key is not defined
        Raises
            KeyError: If the key does not exist and no default value is defined
        """
        response_type, data = self._request(self.KEY_GET, key)
        if response_type == self.KEY_GET_OK:
            return data.decode()
        if response_type == self.KEY_GET_UNDEFINED:
            if default is not Undefined:
                return default
            raise KeyError(f"Beacon key '{key}' is undefined")
        elif response_type == self.KEY_GET_FAILED:
            raise RuntimeError(data.decode())
        raise RuntimeError(f"Unexpected Beacon response type {response_type}")

    def set(self, key: str, value: str):
        """Set the value of the `key` stored in Beacon."""
        response_type, data = self._request(self.KEY_SET, key, value)
        if response_type == self.KEY_SET_OK:
            return data
        elif response_type == self.KEY_SET_FAILED:
            raise RuntimeError(data.decode())
        raise RuntimeError(f"Unexpected Beacon response type {response_type}")
