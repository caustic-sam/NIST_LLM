from bliss.common.counter import CalcCounter
from bliss.controllers.counter import CalcCounterController
from bliss.config.beacon_object import BeaconObject
from bliss.config.static import ConfigReference
import numexpr


class ExprCalcParameters(BeaconObject):
    def __new__(cls, name, config):
        cls = type(cls.__name__, (cls,), {})
        return object.__new__(cls)

    def __init__(self, name, config):
        super().__init__(config, name=name, share_hardware=False, path=["constants"])
        self._define_missing_properties()

    def to_dict(self):
        ret = {}
        for key in self.config.keys():
            v = getattr(self, key)
            if isinstance(v, ConfigReference):
                v = v.dereference()
            ret[key] = v
        return ret

    def __info__(self):
        # TODO: make nicer!
        return str(self.to_dict())

    def _define_missing_properties(self):
        for key, value in self.config.to_dict(resolve_references=False).items():
            if not hasattr(self, key):
                if isinstance(value, str) and value.startswith("$"):
                    # this constant is a reference
                    setattr(self.__class__, key, self.config.raw_get(key))
                else:
                    setattr(
                        self.__class__,
                        key,
                        BeaconObject.property_setting(key, default=value),
                    )

    def apply_config(self, reload=False):
        prev_props = set(self.config.keys())
        super().apply_config(reload)

        # define properties for constants that have appeared
        self._define_missing_properties()
        new_props = set(self.config.keys())

        # self._settings.remove(*self.__settings_properties().keys())
        for key in prev_props | new_props:
            self._settings.remove(key)

        # undefine properties of constants that have disappeared
        for key in prev_props - new_props:
            delattr(self.__class__, key)
        self.force_init()


class SingleExpressionCalcCounterController(CalcCounterController):
    def __init__(self, name, config, expression, constants):
        super().__init__(name, config)
        self._expression = expression
        self._constants = constants

    def calc_function(self, input_dict):
        exp_dict = self._constants.to_dict()
        for cnt in self.inputs:
            exp_dict.update({self.tags[cnt.name]: input_dict[self.tags[cnt.name]]})
        return {
            self.tags[self.outputs[0].name]: numexpr.evaluate(
                self._expression, global_dict={}, local_dict=exp_dict
            )
        }


class ExpressionCalcCounter(CalcCounter):
    def __init__(self, name, config):
        super().__init__(name, unit=config.get("unit"))

        self.constants = ExprCalcParameters(name, config)
        self._config = config

        self.apply_config()

    def apply_config(self, reload=False):
        self.constants.apply_config(reload)
        if reload:
            self._config.reload()

        self._unit = self._config.get("unit")
        name = self._config["name"]
        calc_ctrl_config = {
            "inputs": self._config["inputs"],
            "outputs": [{"name": name, "tags": name}],
        }
        self._set_controller(
            SingleExpressionCalcCounterController(
                name + "_ctrl",
                calc_ctrl_config,
                self._config["expression"],
                self.constants,
            )
        )

    def __info__(self):
        txt = super().__info__()
        txt += f"\nexpression: {self._config.get('expression')}\n"

        txt += " inputs:\n"
        for cfg in self._config.get("inputs", []):
            obj = cfg["counter"]
            if hasattr(obj, "name"):
                obj = obj.name
            txt += f"  - {cfg['tags']}: {obj}\n"

        txt += " constants:\n"
        for k, v in self._config.get("constants", {}).items():
            txt += f"  - {k}: {v}\n"

        return txt


class ExpressionCalcCounterController(CalcCounterController):
    def __init__(self, name, config):
        self.constants = ExprCalcParameters(name, config)
        self._expressions = dict()

        for o in config["outputs"]:
            self._expressions[o["name"]] = o["expression"]

        super().__init__(name, config)

    def calc_function(self, input_dict):
        exp_dict = self.constants.to_dict()
        for cnt in self.inputs:
            exp_dict.update({self.tags[cnt.name]: input_dict[self.tags[cnt.name]]})
        return {
            self.tags[out]: numexpr.evaluate(
                expression, global_dict={}, local_dict=exp_dict
            )
            for out, expression in self._expressions.items()
        }

    def apply_config(self, reload=False):
        self.constants.apply_config(reload)

    def __info__(self):
        txt = f"{self.name} ({self.__class__.__name__}):\n"

        txt += " inputs:\n"
        for cfg in self._config.get("inputs", []):
            obj = cfg["counter"]
            if hasattr(obj, "name"):
                obj = obj.name
            txt += f"  - {cfg['tags']}: {obj}\n"

        txt += " constants:\n"
        for k, v in self._config.get("constants", {}).items():
            txt += f"  - {k}: {v}\n"

        txt += " outputs:\n"
        for cfg in self._config.get("outputs", []):
            txt += f"  - {cfg['name']}: {cfg['expression']}\n"

        return txt
