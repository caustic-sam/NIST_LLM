# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.


from bliss.scanning.chain import AcquisitionMaster
from bliss.controllers.counter import CounterController


class IntermediateController(CounterController):
    """Software controller to create an intermediate master between masters and slaves.

    This can be used to custom the master of the default chain for testing.

    .. code-block::

        from bliss.controllers.test.intermediate_controller import IntermediateController
        mymaster = IntermediateController("mymaster", {})
        DEFAULT_CHAIN.set_settings([{"device": lima_simulator, "master": mymaster}])
        loopscan(10, 0.1, lima_simulator)

    For some slaves like legacy MCA controllers, the acquisition parameters also have to be tuned.

    .. code-block::

        from bliss.controllers.test.intermediate_controller import IntermediateController
        mymaster = IntermediateController("mymaster", {})
        DEFAULT_CHAIN.set_settings([{
            "device": mca1,
            "acquisition_settings": {"start_once": False, "prepare_once": False, "npoints": 1},
            "master": mymaster}]
        )
        loopscan(10, 0.1, mca1)
    """

    def __init__(self, name, master_controller=None, register_counters=True):
        super().__init__(name, master_controller, register_counters)

    def get_acquisition_object(self, acq_params, ctrl_params, parent_acq_params):
        return IntermediateAcquisitionMaster(
            self, ctrl_params=ctrl_params, **acq_params
        )

    def get_default_chain_parameters(self, scan_params, acq_params):
        params = {}
        params["npoints"] = acq_params.get("npoints", scan_params.get("npoints", 1))
        return params


class IntermediateAcquisitionMaster(AcquisitionMaster):
    """
    Acquisition object which only propagate the triggers.

    This can be used to custom the master of the default chain for testing.
    """

    def __init__(self, device, ctrl_params=None, **acq_params):
        self.acq_params = acq_params
        npoints = self.acq_params["npoints"]

        AcquisitionMaster.__init__(
            self,
            device,
            name=device.name,
            npoints=npoints,
            trigger_type=AcquisitionMaster.SOFTWARE,
            prepare_once=False,
            start_once=False,
            ctrl_params=ctrl_params,
        )

    def prepare(self):
        pass

    def start(self):
        pass

    def stop(self):
        pass

    def trigger(self):
        self.trigger_slaves()
