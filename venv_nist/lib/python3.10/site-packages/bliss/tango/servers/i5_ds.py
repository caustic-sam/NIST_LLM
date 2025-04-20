from tango import DevState, GreenMode
from tango.server import Device, device_property, attribute
from bliss.config import static
from bliss.controllers.preciamolen_i5 import I5


"""
property
beacon_name --> i5
"""


class PreciamolenI5(Device):
    beacon_name = device_property(dtype="str")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._i5 = None
        self.init_device()

    def init_device(self):
        """Initialise the tango device"""
        super().init_device()
        self.set_state(DevState.FAULT)
        self.get_device_properties(self.get_device_class())
        config = static.get_config()
        self.__i5 = config.get_config(self.beacon_name)
        self._i5 = I5(self.beacon_name, self.__i5)
        self.set_state(DevState.ON)

    @attribute(dtype=("float",), max_dim_x=3, label="Measure Raw/Tare/Net")
    def measure(self):
        try:
            measure = self._i5.weights()
            values = list()
            for key in sorted(measure):
                values.append(measure[key])
        except Exception as e:
            raise e

        return values

    @attribute(dtype=("str"))
    def unit(self):
        return self._i5.unit


def main(args=None, **kwargs):
    from tango.server import run

    kwargs["green_mode"] = kwargs.get("green_mode", GreenMode.Gevent)
    return run((PreciamolenI5,), args=args, **kwargs)


if __name__ == "__main__":
    main()
