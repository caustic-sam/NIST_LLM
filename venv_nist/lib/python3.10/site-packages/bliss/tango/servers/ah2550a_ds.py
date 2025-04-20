from tango import DevState, GreenMode
from tango.server import Device, device_property, attribute
from bliss.config import static
from bliss.controllers.andeen_hagerling_2550a import AH2550A


"""
property
beacon_name --> ah
"""


class AndeenHagerling2550A(Device):
    beacon_name = device_property(dtype="str")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def init_device(self):
        """Initialise the tango device"""
        self._ah2550a = None
        super().init_device()
        self.set_state(DevState.FAULT)
        self.get_device_properties(self.get_device_class())
        config = static.get_config()
        self.__ah2550a = config.get_config(self.beacon_name)
        # remove tango url, otherwise get_comm will complain about 2 channels found
        self.__ah2550a.pop("tango")
        self._ah2550a = AH2550A(self.beacon_name, self.__ah2550a)
        self.set_state(DevState.ON)

    @attribute(dtype=(str,), max_dim_x=4)
    def idn(self):
        return self._ah2550a.idn()

    @attribute(dtype=str, label="Raw measure as a string C/L/V")
    def measure_raw(self):
        measure = self._ah2550a.measure()
        return measure

    @attribute(dtype=("float",), max_dim_x=3, label="Measure C/L/V")
    def measure(self):
        measure = self._ah2550a.measure()
        values = list()
        try:
            res = measure.split("=")
            assert len(res) == 4
            assert res.pop(0) == "C"
        except Exception as e:
            print(measure)
            raise e

        values = [float(meas.split()[0]) for meas in res]

        return values

    @attribute(dtype=("str",), max_dim_x=3, label="Units C/L/V")
    def units(self):
        measure = self._ah2550a.measure()
        values = list()
        try:
            res = measure.split("=")
            assert len(res) == 4
            assert res.pop(0) == "C"
        except Exception as e:
            print(measure)
            raise e

        values = [meas.split()[1] for meas in res]

        return values


def main(args=None, **kwargs):
    from tango.server import run

    kwargs["green_mode"] = kwargs.get("green_mode", GreenMode.Gevent)
    return run((AndeenHagerling2550A,), args=args, **kwargs)


if __name__ == "__main__":
    main()
