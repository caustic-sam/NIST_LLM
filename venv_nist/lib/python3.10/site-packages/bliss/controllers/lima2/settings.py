# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

# from __future__ import annotations

import logging
import sys

from bliss.config.settings import HashObjSetting
from bliss.config.channels import EventChannel
from bliss.common import event
from bliss.common.utils import typecheck, Undefined
from bliss.config.beacon_object import _ConfigGetter, _Property

_logger = logging.getLogger("bliss.ctrl.lima2")


class Settings:
    """
    Object which handle shared properties from the configuration.

    Arguments:
        config: A configuration node
        path: Can be used to define an offset inside the `config` that
              is supposed to be used as `config` for this object.
    """

    _config_getter = _ConfigGetter
    _property = _Property

    @typecheck
    def __init__(
        self, config, path: list = None, eager_init: bool = True
    ):  #: dict | Config,
        self._path = path
        self._config_name = config.get("name")

        if path:
            self._config = config.goto_path(config, path, key_error_exception=False)
        else:
            self._config = config

        if config.get("name"):
            # check if there is a name in config
            if path:
                self._name = config["name"] + ":" + ":".join(path)
            else:
                self._name = config["name"]
        else:
            raise RuntimeError("No name for settings defined!")

        self._local_initialized = False
        self.__initialized = False
        self._in_initialize_with_setting = False

        self._event_channel = EventChannel(f"__EVENT__:{self._name}")
        self._event_channel.register_callback(self.__event_handler)

        if eager_init:
            self._initialize_with_setting()

    def __info__(self):
        """Return info about settings:
        * list of properties + values
        * settings etc.
        * name / path / share_hwd
        * status: is_init etc.
        """

        info_str = "Settings:\n"
        info_str += f"    path={self._path}\n"
        info_str += f"    config_name={self._config_name}\n"
        info_str += f"    name={self._name}\n"
        info_str += "    \n"
        info_str += f"    _local_initialized={self._local_initialized}\n"
        info_str += f"    __initialized={self.__initialized}\n"
        info_str += f"    __settings_properties={self.__settings_properties()}\n"
        #        info_str += f"    settings={self.settings.get_all()}\n"   # only after apply_config ?
        #        info_str += f"    _disabled_settings={self._disabled_settings}\n"

        return info_str

    def __close__(self):
        self._event_channel.unregister_callback(self.__event_handler)

    def __update_settings(self):
        config = self._config
        settings_property = self.__settings_properties()
        settings_property = {v.fget.__name__: v for v in settings_property.values()}
        default_values = {
            name: prop.default
            for name, prop in settings_property.items()
            if prop.default is not Undefined
        }
        must_be_in_config = set(
            [name for name, prop in settings_property.items() if prop.must_be_in_config]
        )
        must_be_in_config.update(self.__config_getter().keys())

        if not must_be_in_config <= config.keys():
            missing_keys = must_be_in_config - config.keys()
            raise RuntimeError(
                f"For device {self._name} configuration must contains {missing_keys}."
            )
        config_values = {
            name: config.get(name)
            for name in settings_property.keys()
            if config.get(name, Undefined) is not Undefined
        }
        default_values.update(config_values)
        self._settings = HashObjSetting(
            f"{self._name}:settings", default_values=default_values
        )
        self._disabled_settings = HashObjSetting(f"{self._name}:disabled_settings")

    def _apply_config(self, reload=False):
        if reload:
            if not self._config_name:
                raise RuntimeError(
                    "Cannot apply config on unindexed config node. Hint: provide configuration of a valid, named object in __init__"
                )

            self.config.reload()

        try:
            keys = [v.fget.__name__ for v in self.__settings_properties().values()]
            self._settings.remove(*keys)
        except AttributeError:  # apply config before init
            pass

        self.__initialized = False
        self._initialize_with_setting()

    def _initialize_with_setting(self, setting_name=None, setting_value=None):
        """Initialize with redis settings

        If setting_name is specified, set this setting with given setting_value;
        otherwise use the redis values
        """
        if self._in_initialize_with_setting:
            return
        try:
            self._in_initialize_with_setting = True

            if not self._local_initialized:
                self.__update_settings()
                self._local_initialized = True

            if not self.__initialized:
                values = self._settings.get_all()
                error_messages = []
                for name, prop in self.__settings_properties().items():
                    if prop.fset is None:
                        error_messages.append(
                            f"object {self._name} doesn't have property setter for {name}"
                        )
                        continue
                    if self._disabled_settings.get(name):
                        continue
                    prop_name = prop.fget.__name__
                    val = values.get(prop_name, Undefined)
                    if val is not Undefined:
                        try:
                            setattr(self, name, val)
                        except AttributeError:
                            raise AttributeError(
                                f"can't set attribute {name} for device {self._name}"
                            )
                    else:  # initialize setting
                        if prop_name == setting_name:
                            val = setting_value
                        else:
                            val = getattr(self, name)
                        self._settings[prop_name] = val

                if error_messages:
                    raise NotImplementedError("\n".join(error_messages))
                self.__initialized = True
        except ValueError as ve:
            _logger.warning(f"Failed to initialize from setting [{ve}]")
        finally:
            self._in_initialize_with_setting = False

    def __filter_attribute(self, filter):
        # Follow the order of declaration in the class
        # Don't use dir() which alphabetize
        prop_dict = dict()
        for klass in reversed(self.__class__.mro()):
            for name, prop in klass.__dict__.items():
                if isinstance(prop, filter):
                    prop_dict[name] = prop
                else:
                    prop_dict.pop(name, None)
        return prop_dict

    def __settings_properties(self):
        setting_properties = self.__filter_attribute(Settings._property)
        return {
            key: value
            for key, value in sorted(
                setting_properties.items(), key=lambda x: x[1].priority
            )
        }

    def __config_getter(self):
        return self.__filter_attribute(Settings._config_getter)

    def __event_handler(self, events):
        events = [ev for ev in set(events) if event.get_receivers(self, ev)]
        if not events:
            return  # noting to do

        settings_values = self.settings.get_all()
        for ev in events:
            value = settings_values.get(ev)
            try:
                event.send(self, ev, value)
            except Exception:
                sys.excepthook(*sys.exc_info())

    def __clear_local_init(self, value):
        if self._local_initialized and not value:
            self._local_initialized = False


def setting_property(
    fget=None,
    fset=None,
    fdel=None,
    doc=None,
    must_be_in_config=False,
    only_in_config=False,
    default=Undefined,
    priority=0,
):
    if fget is None:

        def f(fget):
            return Settings._property(
                fget,
                must_be_in_config=must_be_in_config,
                only_in_config=only_in_config,
                default=default,
                priority=priority,
            )

        return f
    return Settings._property(
        fget, fset, fdel, doc, must_be_in_config, only_in_config, default, priority
    )
