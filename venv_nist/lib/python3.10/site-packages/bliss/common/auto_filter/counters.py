import numpy
from bliss.common.measurementgroup import _get_counters_from_measurement_group
from bliss.controllers.counter import CalcCounterController
from bliss.controllers.counter import CalcCounter
from bliss.common.protocols import counter_namespace
from bliss.common.protocols import HasMetadataForScanExclusive


class AutoFilterCalcCounter(CalcCounter):
    pass


class AutoFilterAllCalcCounterController(
    CalcCounterController, HasMetadataForScanExclusive
):
    """Manages extra auto filter calculation counters."""

    def __init__(
        self,
        name,
        config,
        monitor,
        detector,
        transmission_funct,
        filter_funct,
        extra_correction_counters_mg,
        corr_cnt_name_suffix,
        scan_metadata_funct,
    ):

        self._optional_cnt_tags = ["filteridx", "transmission", "ratio"]
        self._corr_cnt_name_suffix = corr_cnt_name_suffix
        self._filter_funct = filter_funct
        self._scan_metadata_funct = scan_metadata_funct
        self._transmission_funct = transmission_funct

        self._mg_counters = _get_counters_from_measurement_group(
            extra_correction_counters_mg
        )
        self._calc_config = {
            "inputs": [{"counter": cnt, "tags": cnt.name} for cnt in self._mg_counters],
            "outputs": [
                {
                    "name": f"{cnt.name}{corr_cnt_name_suffix}",
                    "tags": f"{cnt.name}{corr_cnt_name_suffix}",
                }
                for cnt in self._mg_counters
            ],
        }

        # mon,det and so on...
        self.monitor = monitor
        mon_tag = "monitor"
        self.detector = detector
        det_tag = "detector"
        self._calc_config["inputs"].append(
            {"counter": self.detector, "tags": det_tag},
        )
        if self.monitor:
            self._calc_config["inputs"].append(
                {"counter": self.monitor, "tags": mon_tag},
            )

        self._detector_corr_name = f"{self.detector.name}{corr_cnt_name_suffix}"
        self._calc_config["outputs"].append(
            {
                "name": self._detector_corr_name,
                "tags": self._detector_corr_name,
            }
        )

        # finally the optional calc counters
        self._optional_cnts = {}
        for conf in config.get("counters", list()):
            tag = conf["tag"].strip()
            counter_name = conf["counter_name"].strip()
            if tag in self._optional_cnt_tags:
                self._calc_config["outputs"].append(
                    {
                        "name": counter_name,
                        "tags": tag,
                    }
                )
                self._optional_cnts[tag] = counter_name

        super().__init__(name, config, register_counters=False)

    def build_counters(self, config):
        pass

    @property
    def inputs(self):
        self._input_counters = []
        for cnt in self._calc_config["inputs"]:
            self.tags[cnt["counter"].name] = cnt["tags"]
            self._input_counters.append(cnt["counter"])

        return counter_namespace(self._input_counters)

    @property
    def outputs(self):
        self.__calc_counters = []
        for cnt in self._calc_config["outputs"]:
            if cnt["tags"] == "filteridx":
                dtype = int
            else:
                dtype = float
            counter = AutoFilterCalcCounter(cnt["name"], self, dtype=dtype)
            self.tags[counter.name] = cnt["tags"]
            self.__calc_counters.append(counter)
        return counter_namespace(self.__calc_counters)

    def calc_function(self, input_dict):
        # suppose here this calc will never be called with bunch of input data
        # so the transmission is read once from the filterset

        detector_values = input_dict.get("detector", [])
        transmission = self._transmission_funct()
        filteridx = self._filter_funct()

        transmission_values = numpy.array(
            [transmission for i in range(len(detector_values))]
        )
        filteridx_values = numpy.array([filteridx for i in range(len(detector_values))])

        output_dict = {}
        for cnt in self._mg_counters:
            cnt_name = cnt.name
            cnt_values = input_dict[cnt_name]
            output_dict[f"{cnt_name}{self._corr_cnt_name_suffix}"] = (
                cnt_values / transmission_values
            )

        monitor_values = input_dict.get("monitor", [])
        detector_corr_values = detector_values / transmission_values
        # use numpy divider to not get exception with division by zero
        # will result x/0 = Inf
        ratio_values = numpy.divide(detector_corr_values, monitor_values)
        output_dict[self._detector_corr_name] = detector_corr_values

        if "filteridx" in self._optional_cnts.keys():
            output_dict["filteridx"] = filteridx_values
        if "transmission" in self._optional_cnts.keys():
            output_dict["transmission"] = transmission_values
        if "ratio" in self._optional_cnts.keys():
            output_dict["ratio"] = ratio_values

        return output_dict

    def scan_metadata(self):
        return self._scan_metadata_funct()
