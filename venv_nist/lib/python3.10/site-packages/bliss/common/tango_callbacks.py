# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

"""
Class to simplify dealing with tango events.
"""

from __future__ import annotations

import tango
import logging
import gevent
import dataclasses
import typing
from bliss.common.logtools import log_warning
from collections.abc import Callable
from bliss.common.utils import Undefined
from bliss.common.tango import DeviceProxy
from tango import is_numerical_type


_logger = logging.getLogger(__name__)


def _is_int_str(v: str) -> bool:
    try:
        int(v)
    except ValueError:
        return False
    return True


def _is_float_str(v: str) -> bool:
    try:
        float(v)
    except ValueError:
        return False
    return True


@dataclasses.dataclass
class TangoCallbackEvent:
    attr_name: str
    """Name of the monitored tango attribute"""

    callback: Callable[[str, typing.Any], None]
    """Callback to cal when the attribute change"""

    event_id: int | None = None
    """When the event was registered to tango"""

    polled: bool = False
    """Is the attribute polled"""

    last_polled_value: typing.Any = Undefined


class TangoCallbacks:
    """Helper to deal tango events.

    It tried to handle the different kind of behaviour we can have to recieve
    change event from tango. Else finally fallback with a client side polling.

    .. code-block:: python

        def on_state_changed(attr_name, value):
            print(attr_name, value)

        proxy = DeviceProxy("foo/bar/buz")
        cb = TangoCallbacks(proxy)
        cb.add_callback("state", on_state_changed)
        ...
        cb.stop()
    """

    SERVER_SIDE_POLLING = 1000  # in ms or None
    """
    Default server-side polling period used when the polling was not enabled.
    """

    CLIENT_SIDE_POLLING = 1000  # in ms
    """
    Default client-side polling.

    Now the client side polling is never used, because the server-side
    polling is turned on if needed. The code is still there in case we have some
    troubles and we have to enable it back.
    """

    def __init__(self, device_proxy: DeviceProxy):
        self._device_proxy = device_proxy
        self._name = device_proxy.name()
        self._connected: bool = False
        self._watchdog: gevent.Greenlet = gevent.spawn(self._try_device)
        self._watchdog.name = f"{__name__}.{self._name}"
        self._events: dict[str, TangoCallbackEvent] = {}
        self._poller: gevent.Greenlet | None = None

    def add_callback(self, attr_name: str, callback: Callable[[str, typing.Any], None]):
        # Normalize to lower case, to be consistent with att_name from tango event callback
        attr_name = attr_name.lower()
        event_callback = TangoCallbackEvent(attr_name=attr_name, callback=callback)
        self._events[attr_name] = event_callback

    def _connect(self):
        """Try to connect the tango device

        raises:
            tango.ConnectionFailed: Connection have failed
            tango.DevFailed: Another tango thing have failed
            Exception: Another thing have failed
        """
        _logger.info("Trying to connect to %s", self._name)
        self._device_proxy.ping()
        self._subscribe_device()
        self._connected = True

    def _disconnect(self):
        """Disconnect from tango device"""
        self._unsubscribe_device()
        self._connected = False

    def stop(self):
        """Terminate the callbacks.

        The object should not be used anymore.
        """
        if self._watchdog is not None:
            self._watchdog.kill()
        self._watchdog = None
        if self._poller is not None:
            self._poller.kill()
        self._poller = None
        self._disconnect()

    def _try_device(self):
        """Watch dog trying to reconnect to the remote hardware if it was
        disconnected"""
        while True:
            if not self._connected:
                try:
                    self._connect()
                except Exception:
                    _logger.info(
                        "Could not connect to %s. Retrying in 10s",
                        self._name,
                        exc_info=True,
                    )
            gevent.sleep(10)

    def _subscribe_device(self):
        """Subscribe events for this device"""
        for event_callback in self._events.values():
            try:
                if self.SERVER_SIDE_POLLING is None:
                    raise RuntimeError("Server-side events turned off by the client")
                event_id = self._force_subscribe_event(
                    event_callback.attr_name,
                    tango.EventType.CHANGE_EVENT,
                    event_callback.callback,
                )
                event_callback.event_id = event_id
            except Exception:
                _logger.info(
                    "Could not subscribe to property %s %s. Fallback with polling.",
                    self._name,
                    event_callback.attr_name,
                    exc_info=True,
                )
                self._subscribe_polling_attribute(event_callback.attr_name)

    def _unsubscribe_device(self):
        """Unsubscribe registred events for this daiquiri hardware"""
        for event_callback in self._events.values():
            event_callback.polled = False
            if event_callback.event_id is None:
                continue
            try:
                self._device_proxy.unsubscribe_event(event_callback.event_id)
            except Exception:
                _logger.info("Couldnt unsubscribe from %s", event_callback.attr_name)
            else:
                event_callback.event_id = None

    def _get_dev_error_reason(self, e: tango.DevFailed) -> str:
        if hasattr(e, "reason"):
            # Not working with pytango 9.3.6
            return e.reason
        elif hasattr(e, "args"):
            # Working with pytango 9.3.6
            if isinstance(e.args, tuple):
                err = e.args[0]
                if isinstance(err, tango.DevError):
                    return err.reason
        return "UNKONWN"

    def _force_subscribe_event(self, attr_name: str, event_type, callback):
        """Force polling a tango attribute.

        Trying first to subscribe.

        If it fails, setup the server side events.

        raises:
            tango.DevFailed: If the subscription failed
            RuntimeError: If the event configuration have failed
        """
        obj = self._device_proxy

        def update_server_polling():
            is_polled = obj.is_attribute_polled(attr_name)
            if not is_polled:
                _logger.warning(
                    "Server-side polling not enabled for %s.%s", self._name, attr_name
                )
                _logger.warning(
                    "Active server-side polling for %s.%s (%sms)",
                    self._name,
                    attr_name,
                    self.SERVER_SIDE_POLLING,
                )
                obj.poll_attribute(attr_name, self.SERVER_SIDE_POLLING)

        def update_server_event_config():
            info: tango.AttributeInfoEx = obj.get_attribute_config(attr_name)
            changes = []

            valid_period = _is_int_str(info.events.per_event.period)
            if not valid_period:
                changes += ["period"]
                info.events.per_event.period = f"{self.SERVER_SIDE_POLLING}"

            if is_numerical_type(info.data_type):
                valid_rel_change = _is_float_str(info.events.ch_event.rel_change)
                valid_abs_change = _is_float_str(info.events.ch_event.abs_change)
                if not valid_abs_change and not valid_rel_change:
                    changes += ["rel_change"]
                    info.events.ch_event.rel_change = "0.001"

            if changes != []:
                msg = " + ".join(changes)
                _logger.info("Active %s for %s %s", msg, self._name, attr_name)
                try:
                    info.name = attr_name
                    obj.set_attribute_config(info)
                except tango.DevFailed:
                    raise RuntimeError(
                        f"Failed to configure events {self._name} {attr_name}"
                    )

        try:
            result = obj.subscribe_event(
                attr_name,
                event_type,
                self._push_event,
                green_mode=tango.GreenMode.Gevent,
            )
        except tango.DevFailed as e:
            reason = self._get_dev_error_reason(e)
            if reason in [
                "API_EventPropertiesNotSet",
                "API_AttributePollingNotStarted",
            ]:
                pass
            else:
                raise
        else:
            return result

        update_server_polling()
        update_server_event_config()

        _logger.info("Retry using event for %s.%s", self._name, attr_name)
        for i in range(3):
            # Sometimes it stuck, no idea why
            # Retry with a gevent timeout in case
            try:
                with gevent.Timeout(1.0, TimeoutError):
                    return obj.subscribe_event(
                        attr_name,
                        event_type,
                        self._push_event,
                        green_mode=tango.GreenMode.Gevent,
                    )
            except TimeoutError:
                continue
            else:
                pass

    def _subscribe_polling_attribute(self, attr_name: str):
        obj = self._device_proxy
        log_warning(
            obj,
            "Use client-side polling for %s %s. To remove this warning, the server-side polling can be enabled.",
            self._name,
            attr_name,
        )
        if self._poller is None:
            self._poller = gevent.spawn(self._poll_attributes)
            self._poller.name = f"{__name__}.{self._name}"
        self._events[attr_name].polled = True

    def _poll_attributes(self):
        _logger.info("Start client side polling for %s", self._name)
        while True:
            something_to_poll = False
            for event_callback in self._events.values():
                if event_callback.polled:
                    something_to_poll = True
                    attr = self._device_proxy.read_attribute(event_callback.attr_name)
                    value = attr.value
                    if event_callback.last_polled_value != value:
                        event_callback.last_polled_value = value
                        # Second check in case of change during context switch
                        if event_callback.polled:
                            event_callback.callback(event_callback.attr_name, value)
            if not something_to_poll:
                break
            gevent.sleep(self.CLIENT_SIDE_POLLING / 1000.0)

        _logger.info("Stop client side polling for %s", self._name)
        for event_callback in self._events.values():
            event_callback.last_polled_value = Undefined

        self._poller = None

    def _push_event(self, event: tango.EventData):
        """Callback triggered when the remote tango hardware fire an event"""
        try:
            self._protected_push_event(event)
        except Exception:
            _logger.error("Error while processing push_event", exc_info=True)

    def _protected_push_event(self, event: tango.EventData):
        """Callback triggered when the remote tango hardware fire an event

        Any exceptions raised are catched by `_push_event`.
        """
        if not self._connected:
            return

        if event.errors:
            error = event.errors[0]
            _logger.info(f"Error in push_event for {event.attr_name}: {error.desc}")
            # if error.reason == 'API_EventTimeout':
            self._disconnect()
            return

        if event.attr_value is not None:
            event_callback = self._events.get(event.attr_value.name, None)
            if event_callback is not None:
                event_callback.callback(
                    event_callback.attr_name, event.attr_value.value
                )
