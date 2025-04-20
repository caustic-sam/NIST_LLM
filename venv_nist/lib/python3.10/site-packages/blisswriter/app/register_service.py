import os
import sys
import logging
from typing import Optional

from tango import DeviceProxy, DevFailed, Database, DbDevInfo

from ..utils import logging_utils
from .utils import config_root_logger

logger = logging.getLogger(__name__)


WRITER_CLASS = "NexusWriter"


def find_session_writer(
    session_name: str, beacon_host: Optional[str], db: Optional[Database] = None
) -> Optional[str]:
    """
    Find TANGO device of class NexusWriter listening to a particular BLISS session, optionally on a particular Beacon host.
    """
    if db is None:
        db = Database()

    writers = list()
    for obj_name in db.get_device_name("*", WRITER_CLASS):
        _session_name = _get_device_property_from_db(db, obj_name, "session")
        if session_name != _session_name:
            continue

        # When `beacon_host` is not provided, any device with the correct session name is selected.
        if beacon_host:
            _beacon_host = _get_device_property_from_db(db, obj_name, "beacon_host")
            # When the device does not specify a Beacon host, it could belong to any Beacon host.
            if _beacon_host and beacon_host != _beacon_host:
                continue

        writers.append(obj_name)

    if len(writers) > 1:
        if beacon_host:
            err_msg = f"Found more than one writer for session '{session_name}' on Beacon host '{beacon_host}': {writers}"
        else:
            err_msg = (
                f"Found more than one writer for session '{session_name}': {writers}"
            )
        raise ValueError(err_msg)
    if writers:
        return writers[0]


def _get_device_property_from_db(
    db: Database, obj_name: str, property_name: str
) -> Optional[str]:
    value_list = db.get_device_property(obj_name, property_name)[property_name]
    if value_list:
        return value_list[0]


def _ensure_existence(
    session_name: str,
    server: Optional[str] = None,
    instance: Optional[str] = None,
    domain: Optional[str] = None,
    family: Optional[str] = None,
    member: Optional[str] = None,
    use_existing: bool = True,
    beacon_host: Optional[str] = None,
) -> DeviceProxy:
    """
    Find or register TANGO device of class NexusWriter
    """
    db = Database()
    if not server:
        server = "nexuswriter"
    if not family:
        server = "bliss_nxwriter"
    if not member:
        member = session_name
    if not domain:
        domain = _beamline()
    if not instance:
        instance = session_name
    dev_name = "/".join([domain, family, member])
    if use_existing:
        pdev_name = find_session_writer(session_name, beacon_host=beacon_host, db=db)
        if pdev_name:
            proxy = DeviceProxy(pdev_name)
            msg = f"'{get_uri(proxy)}' already registered"
            if dev_name == pdev_name:
                logger.info(msg)
            else:
                logger.warning(msg)
            return proxy
    return _register(
        session_name,
        dev_name,
        server=server,
        instance=instance,
        beacon_host=beacon_host,
        db=db,
    )


def _register(
    session_name: str,
    dev_name: str,
    server: Optional[str] = None,
    instance: Optional[str] = None,
    beacon_host: Optional[str] = None,
    db: Optional[Database] = None,
) -> DeviceProxy:
    """
    Register TANGO device of class NexusWriter
    """
    if not server:
        server = "nexuswriter"
    if not instance:
        instance = "nexuswriters"
    try:
        proxy = DeviceProxy(dev_name)
        logger.info(f"'{get_uri(proxy)}' already registered")
    except DevFailed:
        if db is None:
            db = Database()
        dev_info = DbDevInfo()
        dev_info.name = dev_name
        dev_info._class = WRITER_CLASS
        server = "/".join([server, instance])
        dev_info.server = server
        db.add_device(dev_info)
        proxy = DeviceProxy(dev_name)
        logger.info(f"'{get_uri(proxy)}' registered")
        properties = {"session": session_name}
        if beacon_host:
            properties["beacon_host"] = beacon_host
        proxy.put_property(properties)
    _session_name = _get_device_property(proxy, "session")
    if session_name != _session_name:
        raise RuntimeError(
            f"'{get_uri(proxy)}' is listening to Bliss session '{_session_name}' instead of '{session_name}'"
        )
    if beacon_host:
        _beacon_host = _get_device_property(proxy, "beacon_host")
        if beacon_host != _beacon_host:
            raise RuntimeError(
                f"'{get_uri(proxy)}' belongs to Beacon host '{_beacon_host}' instead of '{beacon_host}'"
            )
    return proxy


def _get_device_property(proxy: DeviceProxy, property_name: str) -> Optional[str]:
    try:
        return proxy.get_property(property_name)[property_name][0]
    except (IndexError, KeyError):
        pass


def get_uri(proxy: DeviceProxy) -> str:
    return f"tango://{proxy.get_db_host()}:{proxy.get_db_port()}/{proxy.dev_name()}"


def _beamline() -> str:
    name = "id00"
    for k in "BEAMLINENAME", "BEAMLINE":
        name = os.environ.get(k, name)
    return name.lower()


def main(argv=None):
    import argparse

    if argv is None:
        argv = sys.argv

    parser = argparse.ArgumentParser(
        description="Register a Tango device saving Bliss data in HDF5"
    )
    parser.add_argument("session", type=str, help="Bliss session name")
    parser.add_argument(
        "--server",
        type=str,
        default="nexuswriter",
        help="Server name ('nexuswriter' by default)",
    )
    parser.add_argument(
        "--instance",
        type=str,
        default="",
        help="Server instance name (session name by default)",
    )
    parser.add_argument(
        "--domain",
        type=str,
        default="",
        help="Device domain name (checks environment or 'id00' by default)",
    )
    parser.add_argument(
        "--family",
        type=str,
        default="bliss_nxwriter",
        help="Device family name ('bliss_nxwriter' by default)",
    )
    parser.add_argument(
        "--member", type=str, default="", help="Device name (session name by default)"
    )
    parser.add_argument(
        "--ignore_existing",
        action="store_false",
        dest="use_existing",
        help="Ignore existing writer for this session",
    )
    parser.add_argument(
        "--beacon-host", type=str, help="Beacon host to which the Bliss session belongs"
    )
    logging_utils.add_cli_args(parser)
    args = parser.parse_args(argv[1:])
    config_root_logger(args)
    _ensure_existence(
        args.session,
        server=args.server,
        instance=args.instance,
        domain=args.domain,
        family=args.family,
        member=args.member,
        use_existing=args.use_existing,
        beacon_host=args.beacon_host,
    )


if __name__ == "__main__":
    sys.exit(main())
