# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

"""
Tango number attribute as a counter.

* counter name can be different than attribute name
* if unit is not specified, unit is taken from tango configuration (if any)
* conversion factor is taken from tango configuration (if any)

TODO:

* alarm?
* writability?
* string attribute
* spectrum attribute (tango_attr_as_spectrum ?)
* image attribute (tango_attr_as_image ?)

YAML_ configuration example:

.. code-block:: yaml

    - class: tango_attr_as_counter
      uri: orion:10000/fe/id/11
      counters:
        - name: srcur
          attr_name: SR_Current
          unit: mA
        - name: lifetime
          attr_name: SR_Lifetime
"""

import weakref
import numpy

from bliss.common.counter import SamplingCounter, SamplingMode
from bliss.common import tango
from bliss import global_map
from bliss.common.logtools import log_debug, log_warning

from bliss.controllers.counter import SamplingCounterController

_TangoCounterControllerDict = weakref.WeakValueDictionary()


class TangoCounterController(SamplingCounterController):
    def __init__(self, name, tango_uri_or_proxy, global_map_register=True):
        # tango_uri_or_proxy accepts either a Tango URL as a string,
        # or a device proxy ; this can be useful
        # in some situations, where no new proxy should be created
        # in this constructor
        super().__init__(name=name)

        if isinstance(tango_uri_or_proxy, str):
            self._proxy = tango.DeviceProxy(tango_uri_or_proxy)
        else:
            self._proxy = tango_uri_or_proxy

        self._attributes_config = None
        if global_map_register:
            global_map.register(self, tag=self.name, children_list=[self._proxy])

    def read_all(self, *counters):
        """
        Read all attributes at once each time it's required.

        In case of reading error of one of the attributes, return numpy.nan
        except if one of the counters is configured in 'allow_failure' mode.
        No retry.
        """

        # Build list of attribute names (str) to read (attributes must be unique).
        attributes_to_read = list()
        for cnt in counters:
            if cnt.attribute not in attributes_to_read:
                attributes_to_read.append(cnt.attribute)

        try:
            dev_attrs = self._proxy.read_attributes(attributes_to_read)
        except tango.CommunicationFailed as error:
            # NB: `allow_failure==True` means "the scan will crash if reading fails"
            if any(cnt.allow_failure for cnt in counters):
                raise
            else:
                if "API_DeviceTimedOut" in repr(error):
                    log_warning(self, "Communication timeout, all counters set to NaN.")
                else:
                    log_warning(self, "Communication failed, all counters set to NaN.")
                return numpy.array([numpy.nan] * len(counters))

        # Check error.
        attr_values = []
        for attr, cnt in zip(dev_attrs, counters):
            error = attr.get_err_stack()
            if error:
                if cnt.allow_failure:
                    raise tango.DevFailed(*error)
                else:
                    log_warning(self, f"'{attr.name}' reading failed, returned NaN.")
                    attr_values.append(numpy.nan)
            else:
                attr_values.append(attr.value)

        # Make a dict to ease counters affectation:
        #   keys->attributes, items->values
        attributes_values = dict(zip(attributes_to_read, attr_values))

        counters_values = list()
        for cnt in counters:
            if cnt.index is None:
                counters_values.append(attributes_values[cnt.attribute])
            else:
                counters_values.append(attributes_values[cnt.attribute][cnt.index])

        return counters_values


class tango_attr_as_counter(SamplingCounter):
    def __init__(self, name, config, controller=None):
        self.tango_uri = config.get_inherited("uri")
        if self.tango_uri is None:
            raise KeyError("uri")

        self.attribute = config["attr_name"]
        self.index = config.get("index")  # if index is None => scalar attribute

        if controller is None:
            # a controller is not provided => it is a stand-alone counter from config,
            # use a generic controller, readings will be grouped by Tango device
            # to optimise as much as possible
            controller = _TangoCounterControllerDict.get(self.tango_uri)
            if controller is None:
                controller = TangoCounterController(
                    "generic_tg_controller", self.tango_uri
                )
                _TangoCounterControllerDict[self.tango_uri] = controller

        log_debug(
            controller, "             to read '%s' tango attribute.", self.attribute
        )

        (
            self.tango_unit,
            tango_display_unit,
            self.tango_format,
        ) = self.get_tango_attribute_meta(controller._proxy)

        # UNIT
        # Use 'unit' if present in YAML, otherwise, try to use the
        # Tango configured 'unit'.
        self.yml_unit = config.get("unit")
        unit = self.tango_unit if self.yml_unit is None else self.yml_unit
        log_debug(
            controller, "             * unit read from YAML config: '%s'", self.yml_unit
        )
        log_debug(
            controller,
            "             * unit read from Tango config: '%s'",
            self.tango_unit,
        )
        log_debug(controller, "             * unit used: '%s'", unit)

        # DISPLAY_UNIT
        # Use 'display_unit' as conversion factor if present in Tango configuration.
        try:
            if tango_display_unit is not None:
                self.conversion_factor = float(tango_display_unit)
            else:
                self.conversion_factor = 1
        except (ValueError, TypeError):
            log_warning(
                controller,
                "display_unit '%s' cannot be converted to a float. Using 1 as Conversion factor for counter.",
                name,
            )
            self.conversion_factor = 1

        # Sampling MODE.
        # MEAN is the default, like all sampling counters
        sampling_mode = config.get("mode", SamplingMode.MEAN)

        # FORMAT
        # Use 'format' if present in YAML, otherwise, try to use the
        # Tango configured 'format'.
        # default: %6.2f
        self.yml_format = config.get("format")
        self.format_string = (
            self.tango_format if self.yml_format is None else self.yml_format
        )

        # ALLOW FAILURE
        self.__allow_failure = config.get("allow_failure", True)

        # INIT
        SamplingCounter.__init__(
            self,
            name,
            controller,
            conversion_function=self.convert_func,
            mode=sampling_mode,
            unit=unit,
        )

    def get_tango_attribute_meta(self, tango_proxy):
        """
        Example of tango_proxy.get_attribute_config

        .. code-block::

            AttributeInfoEx[
                        alarms = AttributeAlarmInfo(delta_t = 'Not specified', delta_val = 'Not specified',
                                                    extensions = [], max_alarm = 'Not specified',
                                                    max_warning = 'Not specified', min_alarm = 'Not specified',
                                                    min_warning = 'Not specified')
                   data_format = tango._tango.AttrDataFormat.SCALAR
                     data_type = tango._tango.CmdArgType.DevFloat
                   description = 'No description'
                    disp_level = tango._tango.DispLevel.OPERATOR
                  display_unit = 'No display unit'
                   enum_labels = []
                        events = AttributeEventInfo(arch_event = ArchiveEventInfo(archive_abs_change = 'Not specified',
                                                                                  archive_period = 'Not specified',
                                                                                  archive_rel_change = 'Not specified',
                                                                                  extensions = []),
                                                    ch_event = ChangeEventInfo(abs_change = 'Not specified',
                                                                               extensions = [],
                                                                               rel_change = 'Not specified'),
                                                    per_event = PeriodicEventInfo(extensions = [], period = '1000'))
                    extensions = []
                        format = '%6.2f'
                         label = 'hppstc1'
                     max_alarm = 'Not specified'
                     max_dim_x = 1
                     max_dim_y = 0
                     max_value = 'Not specified'
                     memorized = tango._tango.AttrMemorizedType.NOT_KNOWN
                     min_alarm = 'Not specified'
                     min_value = 'Not specified'
                          name = 'hppstc1'
                root_attr_name = ''
                 standard_unit = 'No standard unit'
                sys_extensions = []
                          unit = ''
                      writable = tango._tango.AttrWriteType.READ
            writable_attr_name = 'None']
        """
        _tango_attr_config = tango_proxy.get_attribute_config(self.attribute)

        tango_unit = _tango_attr_config.unit
        if not tango_unit:
            tango_unit = None

        tango_display_unit = _tango_attr_config.display_unit
        if tango_display_unit in ("None", "No display unit"):
            tango_display_unit = None

        tango_format = _tango_attr_config.format

        return tango_unit, tango_display_unit, tango_format

    def __info__(self):
        info_string = f"{self.__class__.__name__}:\n"
        info_string += f" {'name':15s} = {self.name}\n"
        info_string += f" {'device server':15s} = {self.tango_uri}\n"
        info_string += f" {'Tango attribute':15s} = {self.attribute}\n"

        # FORMAT
        if self.yml_format is not None:
            info_string += f" {'Beacon format':15s} = {self.yml_format}\n"
        else:
            info_string += f" {'Tango format':15s} = {self.tango_format}\n"

        # UNIT
        if self.yml_unit is not None:
            info_string += f" {'Beacon unit':15s} = {self.yml_unit}\n"
        else:
            info_string += f" {'Tango unit':15s} = {self.tango_unit}\n"

        # INDEX if any
        if self.index is not None:
            info_string += f" {'index':15s} = {self.index}\n"

        # VALUE
        info_string += f" {'value':15s} = {self.value}\n"

        return info_string

    def convert_func(self, raw_value):
        """
        Apply to the `raw_value`:

        * conversion_factor
        * formatting
        """
        log_debug(self, "raw_value=%s", raw_value)

        if raw_value is not None:
            attr_val = raw_value * self.conversion_factor
        else:
            attr_val = numpy.nan

        formated_value = float(
            self.format_string % attr_val if self.format_string else attr_val
        )
        return formated_value

    @property
    def allow_failure(self):
        """
        Allow failure during tango attribute read:

        - True: `PyTango.DevFailed` exception will be raised
        - False: No exception raised and read will return `numpy.nan`
        """
        return self.__allow_failure

    @allow_failure.setter
    def allow_failure(self, allow_failure):
        self.__allow_failure = allow_failure

    @property
    def value(self):
        """
        Return value of the attribute WITH conversion.
        """
        value = self.convert_func(self.raw_value)
        return value

    @property
    def raw_value(self):
        attr_value = self.raw_read
        if self.index is not None:
            value = attr_value[self.index]
        else:
            value = attr_value

        return value


TangoAttrCounter = tango_attr_as_counter
