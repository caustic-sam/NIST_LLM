import gevent
from bliss.config.beacon_object import BeaconObject


class RestTestObject(BeaconObject):
    """
    BLISS object which provide features for REST API testability.
    """

    def __init__(self, name, config_tree):
        self._config = config_tree.get("config")
        self._name = name

        super().__init__(config_tree)

        self._string = "abcd"
        self._number = 123.456
        self._number_positive = 1
        self._option = "one"
        self._options = ["one", "two", "three"]
        self._read_only = 42

    def __info__(self):
        info_str = f"{self._name}:\n"
        for k in ["string", "number", "option", "read_only"]:
            info_str += f"   {k}: {getattr(self, '_' + k)}\n"
        return info_str

    @property
    def state(self):
        return 1

    @property
    def read_only(self):
        return self._read_only

    @BeaconObject.property()
    def string(self):
        return self._string

    @string.setter
    def string(self, value):
        self._string = value

    @BeaconObject.property()
    def number(self):
        return self._number

    @number.setter
    def number(self, value):
        self._number = value

    @BeaconObject.property()
    def number_positive(self):
        return self._number_positive

    @number_positive.setter
    def number_positive(self, value):
        if value < 0:
            raise AttributeError("`number_positive` must be greater than zero")
        self._number_positive = value

    @BeaconObject.property()
    def option(self):
        return self._option

    @option.setter
    def option(self, value):
        self._option = value

    def options(self):
        return self._options

    def func0(self):
        print("Hey!")

    def func1(self, value: str):
        print(f"Hey {value}!")

    def func_mul(self, a: float, b: float) -> float:
        return a * b

    def long_process(self):
        gevent.sleep(20.0)
