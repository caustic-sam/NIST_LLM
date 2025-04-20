# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.
"""
Device server to be used with the MCCE bliss controller.
The only property is 'beacon_name', which is the name, given to the bliss
object.
"""

from tango import DevState, GreenMode
from tango.server import Device, DeviceMeta, device_property, attribute, command
from tango.server import run

from bliss.config.static import get_config
from bliss.controllers.mcce import Mcce, MCCE_TYPE, MCCE_FREQUENCY


class MCCE(Device):
    """The MCCE class"""

    __metaclass__ = DeviceMeta

    beacon_name = device_property(dtype=str, doc="Beacon object name")

    def __init__(self, *args):
        super().__init__(*args)
        self._mcce = None
        self.init_device()

    def init_device(self):
        """Initialise the tango device"""
        self.set_state(DevState.ON)
        super().init_device()
        if self.beacon_name:
            config = get_config().get_config(self.beacon_name)
            if config is None:
                raise RuntimeError(
                    f"Could not find a Beacon object with name {self.beacon_name}"
                )
        self._mcce = Mcce(self.beacon_name, config)
        self._mcce.init()

    @command
    def reset(self):
        """Reset the electrometer"""
        self._mcce.reset()

    @attribute(dtype="int")
    def type(self):
        """Read the electrometer type.
        Returns:
            (int): The type (1-6).
        Raises:
            RuntimeError: Command not executed
        """
        return self._mcce.mcce_type

    @attribute(dtype="str")
    def type_verbose(self):
        """Read the electrometer type.
        Returns:
            (str: Human readble electrometer type.
        Raises:
            RuntimeError: Command not executed
        """
        return MCCE_TYPE[self._mcce.mcce_type]

    @attribute(dtype="str")
    def range_scale(self):
        """Get the possible range
        Returns:
            (tuple): Range list
        """
        # @attribute(dtype=("str",), max_dim_x=100)
        # return self._mcce.mcce_range_str
        return f"{self._mcce.mcce_range_str}"

    @attribute(dtype="str")
    def range(self):
        """Read the electrometer range.
        Returns:
            (string): Current range.
        Raises:
            RuntimeError: Command not executed
        """
        return self._mcce.range

    @range.setter
    def range(self, value):
        """Set the electrometer range
        Args:
           value(str or int): The desired range
        """
        self._mcce.range = value

    @attribute(dtype="str")
    def frequency_scale(self):
        """Get the possible frequency values
        Returns:
            (string): Frequency list
        """
        return f"{MCCE_FREQUENCY}"

    @attribute(dtype=int)
    def frequency(self):
        """Read the frequency filter of the fotovoltaic electrometers.
        Returns:
            (short): The frequency [Hz]
        Raises:
           TypeError: No frequency for electrometers type 4 and 5
        """
        return self._mcce.frequency

    @frequency.setter
    def frequency(self, value):
        """Set the frequency filter of the photovoltaic electrometers.
        Args:
           value(int): Filter value
        Raises:
           TypeError: No frequency for electrometers type 4 and 5
        """
        self._mcce.frequency = value

    @attribute(dtype=str)
    def gain_scale(self):
        """Get the available gain values
        Returns:
            (str): The gain values.
        """
        return f"{self._mcce.mcce_gain}"

    @attribute(dtype=int)
    def gain(self):
        """Read the gain of the photoconductive electrometers.
        Returns:
            (int): The gain value
        Raises:
           TypeError: No gain for electrometers type 1,2,3 and 6
        """
        return self._mcce.gain

    @gain.setter
    def gain(self, value):
        """Set the gain of the fotoconductive electrometers
        Args:
           (int): The value
        Raises:
           TypeError: No gain for electrometers type 1,2,3 and 6
        """
        self._mcce.gain = value

    @attribute(dtype="str")
    def polarity(self):
        """Read the polarity of the current
        Returns:
            (str): positive - input current, negative - output current
        """
        return self._mcce.polarity

    @polarity.setter
    def polarity(self, value):
        """Set the polarity of the current
        Args:
           value(str): positive (input current) or negative (output current)
        """
        self._mcce.polarity = value

    @attribute(dtype="str")
    def description(self):
        """Read the complete information from the electrometer.
        Returns:
            (str): The status
        """
        return self._mcce.__info__()


def main():
    run([MCCE], green_mode=GreenMode.Gevent)


if __name__ == "__main__":
    main()
