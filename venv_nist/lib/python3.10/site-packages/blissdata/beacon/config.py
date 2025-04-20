"""Beacon configuration"""

import os


def get_beacon_address() -> tuple[str, int]:
    """Beacon address from the environment var `BEACON_HOST`.

    For example `('foobar', 25000)`.

    Raises:
        ValueError: If $BEACON_HOST is missing or not properly set
    """
    beacon_host = os.environ.get("BEACON_HOST")
    if beacon_host is None:
        raise ValueError("$BEACON_HOST is not specified")
    try:
        host, port = beacon_host.split(":")
        return host, int(port)
    except Exception:
        raise ValueError(
            f"$BEACON_HOST variable not properly set. Expected: 'hostname:port'. Found: from '{beacon_host}'."
        )
