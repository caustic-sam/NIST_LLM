"""Writer configuration to be published in Redis
"""

import logging

from bliss.common.counter import SamplingMode
from bliss.common.counter import SamplingCounter

from bliss.common.auto_filter.counters import AutoFilterCalcCounter

from bliss.controllers.mca.counter import SpectrumMcaCounter as McaSpectrum
from bliss.controllers.mca.counter import StatisticsMcaCounter as McaStat
from bliss.controllers.mca.counter import RoiMcaCounter as McaRoi

from bliss.controllers.mca.mythen import MythenCounter as MythenSpectrum
from bliss.controllers.mca.mythen import RoiMythenCounter as MythenRoi

from bliss.controllers.mosca.counters import SpectrumCounter as MoscaSpectrum
from bliss.controllers.mosca.counters import StatCounter as MoscaStat
from bliss.controllers.mosca.counters import ROICounter as MoscaRoi

from bliss.controllers.lima.bpm import LimaBpmCounter as LimaBpm
from bliss.controllers.lima.image import ImageCounter as LimaImage
from bliss.controllers.lima.roi import RoiStatCounter as LimaRoi
from bliss.controllers.lima.roi import RoiProfileCounter as LimaProfile
from bliss.controllers.lima.roi import RoiCollectionCounter as LimaCollection

from ..utils import config_utils
from ..utils import session_utils
from ..utils import scan_utils


logger = logging.getLogger(__name__)


CATEGORIES = ["NEXUSWRITER"]


def register_metadata_generators(user_scan_meta):
    """Create the metadata generators for the configurable writer

    :param bliss.scanning.scan_meta.ScanMeta user_scan_meta:
    """
    user_scan_meta.add_categories(CATEGORIES)
    nexuswriter = user_scan_meta.nexuswriter
    nexuswriter.set("instrument_info", fill_instrument_info)
    nexuswriter.set("device_info", fill_device_info)
    nexuswriter.set("technique", fill_technique_info)
    nexuswriter.set("masterfiles", fill_masterfiles)


def fill_instrument_info(scan):
    """
    :param bliss.scanning.scan.Scan scan:
    """
    logger.debug("fill instrument info")
    short_name = config_utils.beamline(default="")
    short_name = session_utils.scan_saving_get(
        "beamline", scan_saving=scan.scan_saving, default=short_name
    )
    name = config_utils.instrument(default=short_name)
    return {"instrument_info": {"name": name, "name@short_name": short_name}}


def fill_technique_info(scan):
    """
    :param bliss.scanning.scan.Scan scan:
    """
    logger.debug("fill technique info")
    return {"technique": current_technique_definition(scan.scan_saving)}


def fill_masterfiles(scan):
    """
    :param bliss.scanning.scan.Scan scan:
    """
    logger.debug("fill master filenames")
    if scan.scan_info["save"]:
        return {"masterfiles": scan_utils.session_master_filenames()}
    return dict()


def fill_device_info(scan):
    """
    :param bliss.scanning.scan.Scan scan:
    """
    logger.debug("fill device info")
    return {"devices": device_info(scan)}


def _mca_device_info(ctr):
    """
    :param BaseMcaCounter ctr:
    :returns str:
    """
    description = (
        ctr._counter_controller.detector_brand.name
        + "/"
        + ctr._counter_controller.detector_type.name
    )
    return {"type": "mca", "description": description}


def _samplingcounter_device_info(ctr):
    """
    :param SamplingCounter ctr:
    :returns str:
    """
    return {"type": "samplingcounter", "mode": ctr.mode.name}


def device_info(scan):
    """
    Publish information on devices (defines types and groups counters).
    Bliss has the concept of controllers and data nodes, but the intermediate
    device level is missing so we need to create it here.

    :param bliss.scanning.scan.Scan scan:
    :returns dict:
    """
    from bliss import global_map  # avoid bliss patching on import

    devices = {}
    for ctr in global_map.get_counters_iter():
        _device_info_add_ctr(devices, ctr)
    return devices


def _device_info_add_ctr(devices, ctr):
    """
    :param dict devices: str -> dict
    :param ctr:
    """
    from bliss import global_map  # avoid bliss patching on import

    try:
        fullname = ctr.fullname.replace(".", ":")  # Redis name
    except AttributeError:
        logger.info(
            "%s does not have a fullname (most likely not a channel)", repr(ctr)
        )
        return
    alias = global_map.aliases.get_alias(ctr)
    if isinstance(ctr, MythenSpectrum):
        device_info = {"type": "mythen"}
        device = {"device_info": device_info, "device_type": "mythen"}
        devices[fullname] = device
    elif isinstance(ctr, MythenRoi):
        device_info = {"type": "mythen"}
        device = {"device_info": device_info, "device_type": "mythen"}
        devices[fullname] = device
    elif isinstance(ctr, McaSpectrum):
        device_info = {"type": "mca"}
        device = {"device_info": device_info, "device_type": "mca"}
        devices[fullname] = device
    elif isinstance(ctr, McaStat):
        device_info = {"type": "mca"}
        device = {"device_info": device_info, "device_type": "mca"}
        devices[fullname] = device
    elif isinstance(ctr, McaRoi):
        device_info = {"type": "mca"}
        device = {"device_info": device_info, "device_type": "mca"}
        devices[fullname] = device
    elif isinstance(ctr, MoscaSpectrum):
        # "falconx:spectrum" -> "falconx:spectrum:det_00", "falconx:spectrum:det_01", ...
        device_info = {"type": "mca"}
        device = {"device_info": device_info, "device_type": "mosca"}
        for i in ctr._counter_controller._mca.active_channels.values():
            devices[f"{fullname}:det{i:02d}"] = device
    elif isinstance(ctr, MoscaStat):
        # "falconx:xxxxx" -> "falconx:stat:xxxxx_det00", "falconx:stat:xxxxx_det01", ...
        device_info = {"type": "mca"}
        device = {"device_info": device_info, "device_type": "mosca"}
        parts = fullname.split(":")
        statname = parts[-1]
        prefix = ":".join(parts[:-1])
        for i in ctr._counter_controller._mca.active_channels.values():
            devices[f"{prefix}:stat:{statname}_det{i:02d}"] = device
    elif isinstance(ctr, MoscaRoi):
        # "falconx:roi1" -> "falconx:roi:roi1"
        device_info = {"type": "mca"}
        device = {
            "device_info": device_info,
            "device_type": "mosca",
            "channel": ctr.roi.channel,
        }
        parts = fullname.split(":")
        parts = parts[:-1] + ["roi", parts[-1]]
        devices[":".join(parts)] = device
    elif isinstance(ctr, LimaBpm):
        device_info = {"type": "lima"}
        device = {"device_info": device_info, "device_type": "lima"}
        devices[fullname] = device
    elif isinstance(ctr, LimaImage):
        device_info = {"type": "lima"}
        device = {"device_info": device_info, "device_type": "lima"}
        devices[fullname] = device
    elif isinstance(ctr, (LimaRoi, LimaProfile, LimaCollection)):
        device_info = {"type": "lima"}
        device = {"device_info": device_info, "device_type": "lima"}
        devices[fullname] = device
    elif isinstance(ctr, AutoFilterCalcCounter):
        device_info = {"type": "autofilter"}
        device = {"device_info": device_info, "device_type": "autofilter"}
        devices[fullname] = device
    elif isinstance(ctr, SamplingCounter):
        device_info = _samplingcounter_device_info(ctr)
        device = {
            "device_info": device_info,
            "device_type": "samplingcounter",
            "data_type": "signal",
        }
        devices[fullname] = device
        if ctr.mode == SamplingMode.SAMPLES:
            device = {"device_info": device_info, "device_type": "samplingcounter"}
            devices[fullname + "_samples"] = device
            if alias:
                devices[fullname + "_samples"]["alias"] = alias + "_samples"
        elif ctr.mode == SamplingMode.STATS:
            for stat in "N", "std", "var", "min", "max", "p2v":
                device = {"device_info": device_info, "device_type": "samplingcounter"}
                devices[fullname + "_" + stat] = device
                if alias:
                    devices[fullname + "_" + stat]["alias"] = alias + "_" + stat
    else:
        logger.info(
            "Counter %s %s published as generic detector",
            fullname,
            type(ctr).__qualname__,
        )
        devices[fullname] = {}
    if alias:
        devices[fullname]["alias"] = alias


def writer_config():
    """
    Get writer configuration from the static session configuration

    :returns dict:
    """
    cfgnode = config_utils.static_root_find("nexus_definitions")
    if cfgnode is None:
        return dict()
    else:
        return cfgnode.to_dict()


def writer_config_get(name, default=None):
    """
    Get attribute from the writer configuration

    :returns str:
    """
    return writer_config().get(name, default)


def technique_info():
    """
    Information on techniques from the static writer configuration

    :returns dict:
    """
    return writer_config_get("technique", {})


def technique_info_get(name, default=None):
    """
    Get attribute from the technique info

    :returns str:
    """
    return technique_info().get(name, default)


def default_technique():
    """
    Default technique from the technique info

    :returns str:
    """
    return technique_info_get("default", "undefined")


def current_technique(scan_saving=None):
    """
    Active technique from the session's scan saving object

    :returns str:
    """
    return session_utils.dataset_get(
        "definition", default_technique(), scan_saving=scan_saving
    )


def techniques():
    """
    List of available techniques from the technique info

    :returns list:
    """
    return list(technique_info_get("techniques", {}).keys())


def technique_definition(technique):
    """
    Technique definition from the technique info

    :param str techique:
    :returns dict: {'name': str,
                    'applications': dict(dict),
                    'plots': dict(list),
                    'plotselect': str}
    """
    applications = {}
    plots = {}
    ret = {
        "name": technique,
        "applications": applications,
        "plots": plots,
        "plotselect": "",
    }
    info = technique_info_get("techniques", {}).get(technique, {})
    if info is None:
        info = {}

    # Get the application definitions selected for this technique
    applicationdict = technique_info_get("applications", {})
    for name in info.get("applications", []):
        definition = applicationdict.get(name, {})
        # for example {'xrf':{'I0': 'iodet',
        #                     'It': 'idet',
        #                     'mca': [...]}, ...}
        if definition:
            name = definition.pop("personal_name", name)
            applications[name] = definition

    # Get the plots selected for this technique
    plotdict = technique_info_get("plots", {})
    plotselect = ""  # this first one is the default
    for name in info.get("plots", []):
        plotdefinition = plotdict.get(name, {})
        # for examples:
        #   {'personal_name': 'counters', 'items': ['iodet', 'xmap1:deadtime_det2', ...]}
        #   {'personal_name': 'counters', 'ndim': 2, 'grid': True}
        items = plotdefinition.get("items", [])
        ndim = plotdefinition.get("ndim", -1)
        grid = plotdefinition.get("grid", False)
        if not items and ndim < 0:
            continue
        name = plotdefinition.get("personal_name", name)
        if name in applications:
            name = name + "_plot"
        if not plotselect:
            plotselect = name
        plots[name] = {"items": items, "grid": grid, "ndim": ndim}
    ret["plotselect"] = plotselect
    return ret


def current_technique_definition(scan_saving=None):
    """
    Current technique definition from the technique info

    :returns dict(dict): technique:definition (str:dict)
    """
    return technique_definition(current_technique(scan_saving=scan_saving))
