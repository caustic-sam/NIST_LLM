import datetime
import tabulate
import logging
import numbers

from bliss.scanning.chain import AcquisitionChain
from bliss.scanning.scan import Scan
from bliss.scanning.scan_progress import ScanProgress
from bliss.scanning.toolbox import ChainBuilder
from bliss.controllers.lima.lima_base import Lima
from bliss.controllers.lima2.controller import DetectorController as Lima2
from bliss.common.utils import BOLD, shorten_signature, typecheck
from bliss.common.tango import DevFailed
from bliss.common.image_tools import array_to_file
from blissdata.lima import image_utils


_log = logging.getLogger("bliss.scans")


__all__ = ("limastat", "limatake")


def limastat(*limadevs):
    """Perform and print test on saving performances for given lima devices.

    If no lima device is given in parameter, use active lima devices in current
    measurement group.
    """
    if not len(limadevs):
        limaused = list()

        builder = ChainBuilder([])
        for node in builder.get_nodes_by_controller_type(Lima):
            limaused.append(node.controller)

        if not limaused:
            raise RuntimeError("No active LIMA device in the current measurement group")
        limadevs = limaused

    stats = list()
    for lima in limadevs:
        name = lima.name
        hlen = lima._proxy.saving_statistics_history
        if hlen:
            sval = lima._proxy.saving_statistics
            cam_stat = [BOLD(name), hlen]
            cam_stat.append("{0:.2f} MB/sec".format(sval[3] / 1024.0 / 1024.0))
            cam_stat.append("{0:.3f}".format(sval[2]))
            cam_stat.append("{0:.2f} MB/sec".format(sval[1] / 1024.0 / 1024.0))
            cam_stat.append("{0:.2f} MB/sec".format(sval[0] / 1024.0 / 1024.0))
            stats.append(cam_stat)
        else:
            stats.append([name, 0, "---", "---", "---", "---"])
    heads = [
        "camera",
        "history\nsize",
        "incoming\nspeed",
        "compression\nratio",
        "compression\nspeed",
        "write\nspeed",
    ]
    print("\n" + tabulate.tabulate(stats, heads, stralign="right") + "\n")


def _limatake_parse_args(args):
    lima_acq_params = [
        "acq_nb_frames",
        "acq_expo_time",
        "acq_trigger_mode",
        "latency_time",
        "wait_frame_id",
        "saving_statistics_history",
        "saving_mode",
        "acq_mode",
        "acc_time_mode",
    ]
    alldict = dict(acq_params=dict(), ctrl_params=dict())
    devdict = dict()
    for name, value in args.items():
        if isinstance(value, dict):
            if name not in devdict.keys():
                devdict[name] = dict(acq_params=dict(), ctrl_params=dict())
            for parname, parvalue in value.items():
                if parname in lima_acq_params:
                    devdict[name]["acq_params"][parname] = parvalue
                else:
                    devdict[name]["ctrl_params"][parname] = parvalue
        else:
            if name in lima_acq_params:
                alldict["acq_params"][name] = value
            else:
                alldict["ctrl_params"][name] = value
    return alldict, devdict


@typecheck
@shorten_signature(hidden_kwargs=[])
def limatake(
    expotime: numbers.Real,
    nbframes: numbers.Integral = 1,
    *limadevs,
    save: bool = False,
    run: bool = True,
    **kwargs,
):
    """Perform an acquisition with lima devices active in current measurement
    group.

    - <expotime>: time in second to use for acquisition.
    - [<nbframes>]: number of frames to acquire (1 if not specified)

    Supplementary parameters can be given one by one or as a dictionary with the
    name of a Lima object.

    Scan parameters a fixed list.  ???

    All parameters not specified under a Lima device name are added to the list
    of acquisition parameters and passed to all cameras.

    Dictionaries for individual lima devices are only added as acquisition
    parameres to the corresponding camera.

    Example:
        ccd1={'saving_suffix': '.edf', 'saving_format': 'EDF'}
        limatake (0.1, 10, saving_frame_per_file=5, basler1=ccd1, save=True)

    """
    limadevs = list(limadevs)
    showtree = len(limadevs) == 0
    title = kwargs.pop("title", "limatake")
    full_title = title + " {0:.4f} {1}".format(expotime, nbframes)
    (all_args, dev_args) = _limatake_parse_args(kwargs)

    # default acq parameters
    lima_params = {
        "acq_nb_frames": nbframes,
        "acq_expo_time": expotime,
        "acq_mode": "SINGLE",
        "acq_trigger_mode": "INTERNAL_TRIGGER",
        "prepare_once": True,
        "start_once": True,
    }
    if nbframes > 0:
        lima_params["wait_frame_id"] = [
            nbframes - 1,
        ]

    lima2_params = {
        "nb_frames": nbframes,
        "expo_time": expotime,
        "latency_time": all_args["acq_params"].get("latency_time", 0.0),
        "trigger_mode": "internal",
        "prepare_once": True,
        "start_once": True,
    }

    # merge all other non Lima device related parameters
    lima_params.update(all_args["acq_params"])

    chain = AcquisitionChain(parallel_prepare=True)
    builder = ChainBuilder(limadevs)

    limaused = list()
    limadevs = list()
    for node in builder.get_nodes_by_controller_type((Lima, Lima2)):
        limaused.append(node)
        limadevs.append(node.controller)

        # get the parameters for every Lima device
        dev_params = dev_args.get(node.controller.name, {})
        if isinstance(node.controller, Lima):
            acq_params = dict(lima_params)
        elif isinstance(node.controller, Lima2):
            acq_params = dict(lima2_params)
        else:
            raise RuntimeError("Only LIMA devices are supported with limatake")

        acq_params.update(dev_params.get("acq_params", {}))
        ctrl_params = dict(all_args["ctrl_params"])
        ctrl_params.update(dev_params.get("ctrl_params", {}))

        node.set_parameters(acq_params=acq_params, ctrl_params=ctrl_params)
        chain.add(node)

    # raise an exception if no detector was found in the measurement group
    if not limaused:
        raise RuntimeError("No active LIMA device in the current measurement group")

    # todo to be changed in Bliss
    # Today, the first top master that finishes, stops all others!!!!!
    top_masters = [x.identifier for x in chain._tree.children("root")]
    for top_master in top_masters:
        top_master.terminator = False

    scan_info = lima_params
    scan_info.update(acq_params)
    scan_info["title"] = full_title
    scan_info["type"] = "limatake"
    scan_info["npoints"] = nbframes
    scan_info["count_time"] = expotime

    if isinstance(node.controller, (Lima, Lima2)):
        display = LimaTakeDisplay()

    scan = Scan(
        chain,
        scan_info=scan_info,
        name=title,
        save=save,
        scan_progress=display,
    )
    if showtree:
        print(scan.acq_chain._tree)
    if run:
        scan.run()

    return scan


class LimaTakeDisplay(ScanProgress):
    """Callback used by limatake to print acquisition status."""

    USE_TEXTBLOCK = True

    HEADER = (
        "Scan {scan_nb} {start_time} {filename} "
        + "{session_name} user = {user_name}\n"
        + "{title}\n"
    )

    def build_progress_message(self):
        from bliss.shell.formatters import tabulate
        from prompt_toolkit.formatted_text import merge_formatted_text

        table: list[list[tuple[str, str]]] = []
        for cam in self.__lima_devs:
            row: list[tuple[str, str]] = []
            table.append(row)
            row.append(("class:header", cam.name))

            style = ""
            last_status = self.data.get(f"{cam.name}:acq_state")
            if last_status == "running":
                style = ""
            elif last_status == "ready":
                style = ""
            elif last_status == "fault":
                style = "class:danger"

            if isinstance(cam, Lima):
                last_ready = self.data.get(f"{cam.name}:last_image_ready", -1) + 1
                row.append((style, f"acq #{last_ready}"))
                if self.scan_info.get("save", False):
                    last_saved = self.data.get(f"{cam.name}:last_image_saved", -1) + 1
                    row.append((style, f"save #{last_saved}"))
            elif isinstance(cam, Lima2):
                nb_frames_acquired = self.data.get(f"{cam.name}:nb_frames_acquired", 0)
                nb_frames_xferred = self.data.get(f"{cam.name}:nb_frames_xferred", 0)
                row.append((style, f"acq #{nb_frames_acquired}"))
                row.append((style, f"xfer #{nb_frames_xferred}"))

        # Make sure meta are initialized
        scan_info = {
            "scan_nb": "?",
            "start_time": "?",
            "filename": "?",
            **self.scan_info,
        }

        formatted_text = merge_formatted_text(
            [
                [("", self.HEADER.format(**scan_info))],
                tabulate.tabulate(table),
            ]
        )
        return len(self.__lima_devs) + 2, formatted_text

    def scan_init_callback(self):
        self.__lima_devs = []
        for acq in self.acq_objects:
            if isinstance(acq.device, (Lima, Lima2)):
                self.__lima_devs.append(acq.device)

    def scan_end_callback(self):
        start_time = self.scan_info.get("start_time")
        if start_time is not None:
            start = datetime.datetime.fromisoformat(start_time)
            end = datetime.datetime.now().astimezone()
            print(f"Finished (took {end - start})\n")


def load_simulator_frames(simulator, nframes, files_pattern):
    """Load file-images into a Lima simulator.

    Arguments:
        simulator: a Lima object of the type simulator
        nframes: number of frames to load
        files_pattern: a pattern for the files pathes (e.g: '/home/beam_images/*.edf' )
    """

    sim = simulator._get_proxy("simulator")
    sim.mode = "LOADER_PREFETCH"
    sim.file_pattern = files_pattern
    sim.nb_prefetched_frames = nframes
    # update the camera max_size after loading new images
    simulator.image.update_max_size()
    reset_cam(simulator, roi=[0, 0, 0, 0])


def load_simulator_with_image_data(simulator, arry):
    """Load an image (array) into a Lima simulator.

    Arguments:
        simulator: a Lima object of the type simulator
        arry: the data array
    """

    img_path = "bliss/tests/images/test_img.edf"
    array_to_file(arry.astype("uint32"), img_path)
    load_simulator_frames(simulator, 1, img_path)
    # os.remove(img_path)


def reset_cam(cam, roi=None):
    """reset lima image parameters and align tango proxy"""

    # --- reset the proxy params

    try:  # tmp fix for lima-core <= 1.9.6
        cam.proxy.image_rotation = "NONE"  # this applies rotation but then proxy fails while updating its roi internally
    except DevFailed:
        cam.proxy.image_rotation = "NONE"  # retry as now rotation is NONE so it will update its roi successfully

    cam.proxy.image_flip = [False, False]
    cam.proxy.image_bin = [1, 1]

    # --- reset bliss image params
    cam.image.binning = [1, 1]
    cam.image.flip = [False, False]
    cam.image.rotation = 0

    if roi:
        cam.proxy.image_roi = roi
        cam.image.roi = roi


def get_last_image(src):
    # before lima-core 1.9.6rc3 request the image after the scan end maybe problematic because lima reapply the postporc
    # on top of the base image but forgets to reapply an evantual rotation.
    # read_video_last_image has a copy of the 'good' final image but is not a good way to retreive one frame among a mutliple images acquisition.
    # read_video_last_image is not synchronized with the acq, it stores the last image it was able to catch (i.e acq_fps != display_fps).
    # for a ct(cam) it is ok because just one image is acquired
    return image_utils.read_video_last_image(src.proxy).array.astype(
        "uint32"
    )  # src.get_data('image').as_array()
