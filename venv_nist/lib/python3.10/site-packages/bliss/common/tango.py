"""Compatibility module for pytango."""

from bliss.common.proxy import Proxy
from bliss.common.logtools import get_logger
from bliss import global_map
from enum import IntEnum
import functools
import os

__all__ = [
    "AttrQuality",
    "EventType",
    "DevState",
    "AttributeProxy",
    "DeviceProxy",
    "ApiUtil",
]


class AttrQuality(IntEnum):
    ATTR_VALID = 0
    ATTR_INVALID = 1
    ATTR_ALARM = 2
    ATTR_CHANGING = 3
    ATTR_WARNING = 4


class EventType(IntEnum):
    CHANGE_EVENT = 0
    QUALITY_EVENT = 1
    PERIODIC_EVENT = 2
    ARCHIVE_EVENT = 3
    USER_EVENT = 4
    ATTR_CONF_EVENT = 5
    DATA_READY_EVENT = 6
    INTERFACE_CHANGE_EVENT = 7
    PIPE_EVENT = 8


class DevState(IntEnum):
    ON = 0
    OFF = 1
    CLOSE = 2
    OPEN = 3
    INSERT = 4
    EXTRACT = 5
    MOVING = 6
    STANDBY = 7
    FAULT = 8
    INIT = 9
    RUNNING = 10
    ALARM = 11
    DISABLE = 12
    UNKNOWN = 13


class DevSource(IntEnum):
    DEV = 0
    CACHE = 1
    CACHE_DEV = 2


def _DeviceProxy(*args, **kwargs):
    raise RuntimeError(
        "Tango is not imported. Hint: is tango Python module installed ?"
    )


def _AttributeProxy(*args, **kwargs):
    raise RuntimeError(
        "Tango is not imported. Hint: is tango Python module installed ?"
    )


def _Database(*args, **kwargs):
    raise RuntimeError(
        "Tango is not imported. Hint: is tango Python module installed ?"
    )


class _ApiUtil:
    def __getattribute__(self, attr):
        raise RuntimeError(
            "Tango is not imported. Hint: is tango Python module installed ?"
        )


ApiUtil = _ApiUtil()

try:
    from tango import (  # noqa: F811
        AsynReplyNotArrived,
        AttrQuality,
        EventType,
        ConnectionFailed,
        CommunicationFailed,
        DevState,
        DevFailed,
        DevError,
        DevSource,
        ApiUtil,
    )
    from tango import Database as _Database  # noqa: F811

    from tango.gevent import (  # noqa: F811
        DeviceProxy as _DeviceProxy,
        AttributeProxy as _AttributeProxy,
    )

    # from tango import cb_sub_model
except ImportError:
    # PyTango < 9 imports
    try:
        from PyTango import (  # noqa: F401
            AsynReplyNotArrived,
            AttrQuality,
            EventType,
            ConnectionFailed,
            CommunicationFailed,
            DevState,
            DevFailed,
            DevError,
            DevSource,
            ApiUtil,
        )

        from PyTango import Database as _Database

        from PyTango.gevent import (
            DeviceProxy as _DeviceProxy,
            AttributeProxy as _AttributeProxy,
        )

        # from PyTango import cb_sub_model
    except ImportError:
        pass
    else:
        pass
        # tango_util = ApiUtil.instance()
        # tango_util.set_asynch_cb_sub_model(cb_sub_model.PUSH_CALLBACK)
else:
    pass
    # for some reason, setting PUSH model makes tests to get stuck
    # tango_util = ApiUtil.instance()
    # tango_util.set_asynch_cb_sub_model(cb_sub_model.PUSH_CALLBACK)


def logging_call(
    *args, _log_name=None, _log_tango_func=None, _log_logger=None, **kwargs
):
    _log_logger("call %s%s%s", _log_name, args, kwargs)
    ret = _log_tango_func(*args, **kwargs)
    _log_logger("returned: %s", ret)
    return ret


class DeviceProxy(Proxy):
    """A transparent wrapper of DeviceProxy, to make sure TANGO cache is not used by default

    Also adds logging capability, to be able to follow Tango calls
    """

    __sphinx_skip__ = True

    def __init__(self, *args, **kwargs):
        super().__init__(
            functools.partial(_DeviceProxy, *args, **kwargs), init_once=True
        )

        dev_name = args[0]
        object.__setattr__(self, "_DeviceProxy__dev_name", dev_name)

        global_map.register(self, parents_list=["comms"])
        object.__setattr__(self, "_DeviceProxy__logger", get_logger(self).debug)

        self.set_source(DevSource.DEV)

    def __str__(self):
        """
        Re-implemented function as workaround for Tango issue:

        https://github.com/tango-controls/pytango/issues/298
        """
        return f"{type(self).__name__}({self.__dev_name})"

    def __repr__(self):
        """
        Re-implemented function as workaround for Tango issue:

        https://github.com/tango-controls/pytango/issues/298
        """
        return f"{type(self).__name__}({self.__dev_name},{id(self)})"

    def __getattr__(self, name):
        try:
            attr = getattr(self.__wrapped__, name)
        except AttributeError as e:
            if name == "_DeviceProxy__logger":
                return super().__getattr__("_DeviceProxy__logger")
            else:
                # The cause of this AttributeError maybe
                # a communication failure with the device.
                try:
                    self.__wrapped__.ping()
                except Exception as cause:
                    raise e from cause
                raise
        else:
            if not callable(attr):
                self.__logger("getting attribute '%s': %s", name, attr)
                return attr

            else:
                return functools.partial(
                    logging_call,
                    _log_name=name,
                    _log_tango_func=attr,
                    _log_logger=self.__logger,
                )

    def __setattr__(self, name, value):
        self.__logger("setting attribute '%s': %s", name, value)
        super().__setattr__(name, value)


class AttributeProxy(Proxy):
    """A transparent wrapper of AttributeProxy, to make sure TANGO cache is not used by default"""

    __sphinx_skip__ = True

    def __init__(self, *args, **kwargs):
        super().__init__(
            functools.partial(_AttributeProxy, *args, **kwargs), init_once=True
        )
        self.get_device_proxy().set_source(DevSource.DEV)


def get_fqn(proxy):
    """
    Returns the fully qualified name of a DeviceProxy or an AttributeProxy in the format
    `tango://<host>:<port>/<dev_name>[/<attr_name>]`
    """
    try:
        name = proxy.dev_name()
    except AttributeError:
        name = get_fqn(proxy.get_device_proxy())
        return "{}/{}".format(name, proxy.name())
    host = proxy.get_db_host()
    port = proxy.get_db_port()
    return "tango://{}:{}/{}".format(host, port, name)


def Database(tghost=None):
    """Return tango Database object.

    If url is None it uses the current TANGO_HOST
    otherwise uses url to find TANGO_HOST
    """

    if tghost is not None:
        orig_tango_host = os.environ.get("TANGO_HOST")
        if tghost:
            try:
                os.environ["TANGO_HOST"] = tghost
                return _Database()
            finally:
                os.environ["TANGO_HOST"] = orig_tango_host
    return _Database()


def get_tango_host_from_url(url):
    if url.startswith("//") or url.startswith("tango://"):
        idx = url.find("//")
        tghost = url[idx + 2 :].split("/")[0]
        return tghost


def get_tango_device_name_from_url(url):
    if url.startswith("//") or url.startswith("tango://"):
        idx = url.find("//")
        device_name = "/".join(url[idx + 2 :].split("/")[1:])
        return device_name
    return url
