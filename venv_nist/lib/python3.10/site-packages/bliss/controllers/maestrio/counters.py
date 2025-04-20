import weakref
import numpy

from bliss.controllers.counter import CounterController
from bliss.common.counter import Counter as CounterBase
from bliss.common.counter import IntegratingCounter as IntegratingCounterBase
from bliss.scanning.acquisition.maestrio import MaestrioDefaultAcquisitionMaster


class MaestrioSamplingCounter(CounterBase):
    def __init__(self, counter_name, controller, channel_name):
        super().__init__(counter_name, controller, dtype=numpy.uint32)
        self._channel = channel_name

    @property
    def channel(self):
        return self._channel


class MaestrioIntegratingCounter(IntegratingCounterBase):
    def __init__(self, counter_name, controller, channel_name):
        super().__init__(counter_name, controller, dtype=numpy.uint32)
        self._channel = channel_name

    @property
    def channel(self):
        return self._channel


class MaestrioCounterController(CounterController):
    def __init__(self, maestrio_ctrl, config_tree):
        super().__init__(maestrio_ctrl.name, register_counters=True)

        self._maestrio_ctrl = weakref.proxy(maestrio_ctrl)
        SMART_ACC_MODE = self._maestrio_ctrl.SMART_ACC_MODE
        channels_cfg_list = config_tree.get("channels", [])
        if channels_cfg_list:
            smart_acc_config = self._maestrio_ctrl._read_smart_acc_config()
            for channels_cfg in channels_cfg_list:
                cnt_name = channels_cfg.get("counter_name")
                if cnt_name is not None:
                    channel = channels_cfg["channel"]
                    smart_acc_config_channel = smart_acc_config[channel]

                    if smart_acc_config_channel.get("mode") in [
                        SMART_ACC_MODE.COUNT_UP,
                        SMART_ACC_MODE.INTEGR,
                    ]:
                        MaestrioIntegratingCounter(cnt_name, self, channel)
                    else:
                        MaestrioSamplingCounter(cnt_name, self, channel)

    def get_acquisition_object(self, acq_params, ctrl_params, parent_acq_params):
        return MaestrioDefaultAcquisitionMaster(self, self._maestrio_ctrl, **acq_params)

    def get_default_chain_parameters(self, scan_params, acq_params):
        return {"count_time": acq_params.get("count_time", scan_params["count_time"])}
