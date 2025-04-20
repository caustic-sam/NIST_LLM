from bliss.common import scans
from bliss.scanning.scan import Scan, get_default_scan_progress
from bliss.scanning.acquisition.motor import VariableStepTriggerMaster
from bliss.common.auto_filter.chain_patching_inplace import patch_acq_chain
from bliss.common.auto_filter.stepscan_controller import AutoFilterStepScan


class AutoFilter(AutoFilterStepScan):
    """Steps scans have an acquisition chain which is patched in-place."""

    def _create_step_scan(
        self, counter_args, motors_positions, scan_name, scan_info, kwargs
    ):
        # npoints of the master is len(motors_positions)
        top_master = VariableStepTriggerMaster(*motors_positions)

        # npoints of the slaves is the maximum number of tries
        npoints = scan_info["npoints"]
        scan_info["npoints"] = self.maximum_number_of_tries(scan_info["npoints"])
        acq_chain = scans.DEFAULT_CHAIN.get(scan_info, counter_args)
        scan_info["npoints"] = npoints

        timer = acq_chain.top_masters.pop(0)
        acq_chain.add(top_master, timer)

        patch_acq_chain(acq_chain=acq_chain, auto_filter=self)

        scan_progress = get_default_scan_progress()
        return Scan(
            acq_chain,
            scan_info=scan_info,
            name=scan_name,
            save=kwargs.get("save", True),
            save_images=kwargs.get("save_images"),
            scan_progress=scan_progress,
        )
