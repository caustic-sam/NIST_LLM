# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

import importlib
from contextlib import contextmanager
import gevent
import numpy
import typing
from pathlib import PurePath, Path, PureWindowsPath, PurePosixPath
from packaging.version import Version

from bliss import global_map
from bliss.common.utils import autocomplete_property
from bliss.common.tango import (
    DeviceProxy,
    DevFailed,
    Database,
    DevState,
    AsynReplyNotArrived,
    get_tango_device_name_from_url,
    get_tango_host_from_url,
)
import bliss.common.plot as plot_module
from bliss.config import settings
from bliss.common.logtools import log_debug
from bliss.common.protocols import HasMetadataForDataset, HasMetadataForScanExclusive
from bliss.common.scans import ct, sct
from bliss.controllers.counter import CounterController, counter_namespace
from bliss import current_session

from bliss.config.channels import Cache, clear_cache

from bliss.controllers.lima.properties import LimaAttributesAsProperties
from bliss.controllers.lima.properties import LimaAttributesAsDeferredWriteProperties
from bliss.controllers.lima.bpm import Bpm
from bliss.controllers.lima import roi as lima_roi
from bliss.controllers.lima.counters import (
    RoiCounters,
    RoiProfileController,
    RoiCollectionController,
)
from bliss.controllers.lima.image import ImageCounter
from bliss.controllers.lima.shutter import Shutter
from bliss.controllers.lima.debug import LimaDebug
from bliss.controllers.lima.saving import LimaSavingParameters
from bliss.controllers.lima.processing import LimaProcessing
from blissdata.lima import image_utils


class CameraBase(LimaAttributesAsProperties):
    def __init__(self, name, lima_device, proxy):
        pass

    @property
    def synchro_mode(self):
        """
        Camera synchronization capability
        Acquisition can either check that the camera is ready for next image with
        **ready_for_next_image** method or waiting to received the image data.

        synchro_mode can be either "TRIGGER" => synchronization with **ready_for_next_image** or
        "IMAGE" => synchronization with **last_image_ready**
        """
        return "TRIGGER"


class ChangeTangoTimeout:
    def __init__(self, device, timeout):
        self.__timeout = timeout
        self.__device = device

    def __enter__(self):
        self.__back_timeout = int(self.__device.get_timeout_millis())
        self.__device.set_timeout_millis(int(1000 * self.__timeout))

    def __exit__(self, type_, value, traceback):
        self.__device.set_timeout_millis(self.__back_timeout)


class Lima(CounterController, HasMetadataForScanExclusive, HasMetadataForDataset):
    """
    Lima controller.
    Basic configuration:
        name: seb_test
        class: Lima
        tango_url: id00/limaccds/simulator1

        directories_mapping:
          default:              # Mapping name
            - path: /data/inhouse
              replace-with: /hz
            - path: /data/visitor
              replace-with: Z:/
          local:
            - path: /data/inhouse
              replace-with: L:/
    """

    DEVICE_TYPE = "lima"
    """Normalized device type exposed in the scan info"""

    _ROI_COUNTERS = "roicounter"
    _ROI_PROFILES = "roi2spectrum"
    _ROI_COLLECTION = "roicollection"
    _BPM = "bpm"
    _BG_SUB = "backgroundsubstraction"
    _MINIMAL_LIMA_VERSION = Version("1.9.23")
    # backward compatibility for old pickled objects in redis,
    # since classes definition moved
    LimaSavingParameters = LimaSavingParameters
    LimaProcessing = LimaProcessing

    def __init__(self, name, config_node):
        """Lima controller.

        name -- the controller's name
        config_node -- controller configuration
        in this dictionary we need to have:
        tango_url -- tango main device url (from class LimaCCDs)
        optional:
        tango_timeout -- tango timeout (s)
        """
        self.__tg_url = config_node.get("tango_url")
        self.__tg_timeout = config_node.get("tango_timeout", 3)
        self.__prepare_timeout = config_node.get("prepare_timeout", self.__tg_timeout)
        self.__start_waittime = config_node.get("start_waittime", 0.005)
        self.__bpm = None
        self.__roi_counters = None
        self.__roi_profiles = None
        self.__roi_collection = None
        self._instrument_name = config_node.root.get("instrument", "")
        self.__last = None
        self._config_node = config_node
        self._camera = None
        self._disable_bpm = config_node.get("disable_bpm", False)
        self._image = None
        self._shutter = None
        self._acquisition = None
        self._accumulation = None
        self._saving = None
        self._processing = None
        self._debug = None
        self._device_proxies = {}
        self._cached_ctrl_params = {}

        self._proxy = self._get_proxy()
        try:
            self._proxy.ping()
        except DevFailed:
            pass
        else:
            self.ensure_minimal_version()

        super().__init__(name)

        self._directories_mapping = config_node.get("directories_mapping", dict())
        self._active_dir_mapping = settings.SimpleSetting(
            "%s:directories_mapping" % name
        )

        global_map.register("lima", parents_list=["global"])
        global_map.register(
            self, parents_list=["lima", "controllers"], children_list=[self._proxy]
        )

    def dataset_metadata(self) -> dict:
        try:
            self.proxy.ping()
        except DevFailed:
            # Skip the metadata if the detector is offline (since if it is used it failed later and if it is not used we dont care about the metadata)
            return None

        description = f"{self.proxy.lima_type}, {self.proxy.camera_model}"
        px, py = self.proxy.camera_pixelsize

        return {
            "name": self.name,
            "description": description,
            "x_pixel_size": px,
            "y_pixel_size": py,
        }

    def scan_metadata(self) -> dict:
        lima_type = self.proxy.lima_type
        description = f"{lima_type}, {self.proxy.camera_model}"
        camera_settings = {}
        for k, v in self.camera.to_dict().items():
            if k == "state":
                # The state of the tango controller is pointless
                continue
            if isinstance(v, numpy.ndarray):
                camera_settings[k] = v.tolist()
            else:
                camera_settings[k] = v

        px, py = self.proxy.camera_pixelsize
        result = {
            "type": "lima",
            "description": description,
            "x_pixel_size": float(px),
            "y_pixel_size": float(py),
            "x_pixel_size@units": "m",
            "y_pixel_size@units": "m",
            "camera_settings": camera_settings,
        }
        if lima_type == "Xh":
            result["representation"] = "mca"
        return result

    @property
    def disable_bpm(self):
        return self._disable_bpm

    def set_bliss_device_name(self):
        try:
            try:
                self.proxy.user_instrument_name = self._instrument_name
            except DevFailed:
                pass
            try:
                self.proxy.user_detector_name = self.name
            except DevFailed:
                pass
        except (RuntimeError, DevFailed):
            pass

    @property
    def _name_prefix(self):
        try:
            return f"{current_session.name}:{self.name}"
        except AttributeError:
            return self.name

    def get_acquisition_object(self, acq_params, ctrl_params, parent_acq_params):
        # avoid cyclic import
        from bliss.scanning.acquisition.lima import LimaAcquisitionMaster

        return LimaAcquisitionMaster(self, ctrl_params=ctrl_params, **acq_params)

    def get_default_chain_parameters(self, scan_params, acq_params):
        # INTERNAL_TRIGGER: software trigger, one trigger for the acquisition of 'acq_nb_frames'
        # INTERNAL_TRIGGER_MULTI: software trigger, one trigger per frame (to be repeated 'acq_nb_frames' times)
        # EXTERNAL_TRIGGER: hardware trigger, one trigger for the acquisition of 'acq_nb_frames'
        # EXTERNAL_TRIGGER_MULTI: hardware trigger, one trigger per frame (to be repeated 'acq_nb_frames' times)
        # EXTERNAL_GATE: hardware trigger, one gate signal per frame, the gate period is the exposure time
        # EXTERNAL_START_STOP: hardware trigger, two pulse per frame, first pulse starts exposure, second pulse ends exposure

        # DECIDE DEFAULT TRIGGER MODE
        if "INTERNAL_TRIGGER_MULTI" in self.available_triggers:
            default_trigger_mode = "INTERNAL_TRIGGER_MULTI"
        else:
            default_trigger_mode = "INTERNAL_TRIGGER"

        # USE PROVIDED TRIGGER MODE ELSE USE DEFAULT VALUE
        acq_trigger_mode = acq_params.get("acq_trigger_mode", default_trigger_mode)

        # DECIDE DEFAULT PREPARE_ONCE AND START_ONCE IN FUNCTION OF TRIGGER MODE
        if acq_trigger_mode in ["INTERNAL_TRIGGER", "EXTERNAL_TRIGGER"]:
            default_prepare_once = False
            default_start_once = False
        elif acq_trigger_mode == "INTERNAL_TRIGGER_MULTI":
            default_prepare_once = True
            default_start_once = False
        elif acq_trigger_mode in [
            "EXTERNAL_TRIGGER_MULTI",
            "EXTERNAL_GATE",
            "EXTERNAL_START_STOP",
        ]:
            default_prepare_once = True
            default_start_once = True
        else:
            raise ValueError(f"Unknown trigger mode '{acq_trigger_mode}'")

        # USE PROVIDED ACQ PARAMS ELSE USE DEFAULT VALUES
        acq_mode = acq_params.get("acq_mode", "SINGLE")
        prepare_once = acq_params.get("prepare_once", default_prepare_once)
        start_once = acq_params.get("start_once", default_start_once)

        acq_expo_time = acq_params.get("acq_expo_time")
        if acq_expo_time is None:
            acq_expo_time = scan_params["count_time"]

        acq_nb_frames = acq_params.get("acq_nb_frames")
        npoints = scan_params.get("npoints", 1)
        if acq_nb_frames is None:
            acq_nb_frames = npoints if prepare_once else 1

        # RETURN MINIMUM DEFAULT ACQUISITION PARAMETERS
        params = acq_params.copy()
        params["acq_nb_frames"] = acq_nb_frames
        params["acq_expo_time"] = acq_expo_time
        params["acq_trigger_mode"] = acq_trigger_mode
        params["acq_mode"] = acq_mode
        params["prepare_once"] = prepare_once
        params["start_once"] = start_once

        return params

    @property
    def _lima_hash(self):
        """
        returns a string that is used to describe the tango device state
        """
        return f"{self._proxy.image_sizes}{self._proxy.image_roi}{self._proxy.image_flip}{self._proxy.image_bin}{self._proxy.image_rotation}"

    def _needs_update(self, key, new_value, proxy=None):
        try:
            cached_value = self._cached_ctrl_params[key].value
        except KeyError:
            self._cached_ctrl_params[key] = Cache(self, key)
            self._cached_ctrl_params[key].value = str(new_value)
            if proxy:
                # check if new value is different from Lima value
                try:
                    lima_value = getattr(proxy, key)
                except AttributeError:
                    return True
                if isinstance(lima_value, numpy.ndarray):
                    return str(lima_value) != str(new_value)
                try:
                    return lima_value != new_value
                except ValueError:
                    return str(lima_value) != str(new_value)
            return True
        else:
            if cached_value != str(new_value):
                self._cached_ctrl_params[key].value = str(new_value)
                return True
        return False

    def apply_parameters(self, ctrl_params):
        self.set_bliss_device_name()

        # -----------------------------------------------------------------------------------
        device_name = get_tango_device_name_from_url(self.__tg_url)
        tango_host = get_tango_host_from_url(self.__tg_url)
        server_started_date = (
            Database(tango_host).get_device_info(device_name).started_date
        )
        server_start_timestamp_cache = Cache(self, "server_start_timestamp")
        server_restarted = server_start_timestamp_cache.value != server_started_date
        if server_restarted:
            server_start_timestamp_cache.value = server_started_date
        last_session_cache = Cache(self, "last_session")
        other_session_started = last_session_cache.value != current_session.name
        if other_session_started:
            last_session_cache.value = current_session.name
        lima_hash_different = Cache(self, "lima_hash").value != self._lima_hash

        update_all = server_restarted or other_session_started or lima_hash_different
        if update_all:
            log_debug(self, "All parameters will be refreshed on %s", self.name)
            self._cached_ctrl_params.clear()

        assert ctrl_params["saving_format"] in self.saving.available_saving_formats
        ctrl_params["saving_suffix"] = self.saving.suffix_dict[
            ctrl_params["saving_format"]
        ]

        use_mask = ctrl_params.pop("use_mask")
        assert isinstance(use_mask, bool)
        if self.processing._mask_changed or self._needs_update("use_mask", use_mask):
            maskp = self._get_proxy("mask")
            global_map.register(maskp, parents_list=[self], tag="mask")
            maskp.Stop()
            if use_mask:
                log_debug(self, " uploading new mask on %s", self.name)
                maskp.setMaskImage(self.processing.mask)
                self.processing._mask_changed = False
                maskp.RunLevel = self.processing.runlevel_mask
                maskp.Start()
                maskp.type = "STANDARD"

        use_flatfield = ctrl_params.pop("use_flatfield")
        assert isinstance(use_flatfield, bool)
        flatfield_normalize = ctrl_params.pop("flatfield_normalize")
        assert isinstance(flatfield_normalize, bool)
        if self.processing._flatfield_changed or self._needs_update(
            "use_flatfield", use_flatfield
        ):
            ff_proxy = self._get_proxy("flatfield")
            global_map.register(ff_proxy, parents_list=[self], tag="flatfield")
            ff_proxy.Stop()
            if use_flatfield:
                log_debug(self, " uploading flatfield on %s", self.name)
                ff_proxy.setFlatFieldImage(self.processing.flatfield)
                ff_proxy.RunLevel = self.processing.runlevel_flatfield
                ff_proxy.normalize = True
                if not flatfield_normalize:
                    ff_proxy.normalize = False
                self.processing._flatfield_changed = False
                ff_proxy.Start()

        use_bg_sub = ctrl_params.pop("use_background")
        assert isinstance(use_bg_sub, bool)
        # assert use_bg_sub in self.processing.BG_SUB_MODES.keys()
        if self.processing._background_changed or self._needs_update(
            "use_background", use_bg_sub
        ):
            bg_proxy = self._get_proxy("backgroundsubstraction")
            global_map.register(bg_proxy, parents_list=[self], tag="bg_sub")
            log_debug(
                self,
                " stopping background sub proxy on %s and setting runlevel to %s",
                self.name,
                self.processing.runlevel_background,
            )
            bg_proxy.Stop()
            bg_proxy.RunLevel = self.processing.runlevel_background
            if use_bg_sub:
                if self.processing.background_source == "file":
                    log_debug(self, " uploading background on %s", self.name)
                    log_debug(self, " background file = %s", self.processing.background)
                    bg_proxy.setbackgroundimage(self.processing.background)
                log_debug(self, " starting background sub proxy of %s", self.name)
                bg_proxy.Start()

        if self._needs_update(
            "runlevel_roicounter",
            self.processing.runlevel_roicounter,
            proxy=self.roi_counters._proxy,
        ):
            proxy = self.roi_counters._proxy
            state = proxy.State()
            if state == DevState.ON:
                log_debug(
                    self, "stop, runlevel, start on roi_counter proxy of %s", self.name
                )
                proxy.Stop()
                proxy.RunLevel = self.processing.runlevel_roicounter
                proxy.Start()
            else:
                log_debug(self, "set runlevel on roi_counter proxy of %s", self.name)
                proxy.RunLevel = self.processing.runlevel_roicounter

        if (
            self.roi_collection is not None
        ):  # CHECK IF LIMA SERVER COLLECTION PLUGIN IS AVAILABLE (see lima server version)
            if self._needs_update(
                "runlevel_roicollection",
                self.processing.runlevel_roicollection,
                proxy=self.roi_collection._proxy,
            ):
                proxy = self.roi_collection._proxy
                state = proxy.State()
                if state == DevState.ON:
                    log_debug(
                        self,
                        "stop, runlevel, start on roi_collection proxy of %s",
                        self.name,
                    )
                    proxy.Stop()
                    proxy.RunLevel = self.processing.runlevel_roicollection
                    proxy.Start()
                else:
                    log_debug(
                        self, "set runlevel on roi_collection proxy of %s", self.name
                    )
                    proxy.RunLevel = self.processing.runlevel_roicollection

        if self._needs_update(
            "runlevel_roiprofiles",
            self.processing.runlevel_roiprofiles,
            proxy=self.roi_profiles._proxy,
        ):
            proxy = self.roi_profiles._proxy
            state = proxy.State()
            if state == DevState.ON:
                log_debug(
                    self, "stop, runlevel, start on roi_profiles proxy of %s", self.name
                )
                proxy.Stop()
                proxy.RunLevel = self.processing.runlevel_roiprofiles
                proxy.Start()
            else:
                log_debug(self, "set runlevel on roi_profiles proxy of %s", self.name)
                proxy.RunLevel = self.processing.runlevel_roiprofiles

        if self._needs_update(
            "runlevel_bpm", self.processing.runlevel_bpm, proxy=self.bpm._proxy
        ):
            proxy = self.bpm._proxy
            state = proxy.State()
            if state == DevState.ON:
                log_debug(self, "stop, runlevel, start on bpm proxy of %s", self.name)
                proxy.Stop()
                proxy.RunLevel = self.processing.runlevel_bpm
                proxy.Start()
            else:
                log_debug(self, "set runlevel on bpm proxy of %s", self.name)
                proxy.RunLevel = self.processing.runlevel_bpm

        # ------- send the params to tango-lima ---------------------------------------------

        # Lima rules and order of image transformations:
        # 1) binning
        # 2) flip [Left-Right, Up-Down]
        # 3) rotation (clockwise!)
        # 4) roi (expressed in the current state f(bin, flip, rot))

        # --- Extract special params from ctrl_params and sort them -----------
        special_params = {}

        if "image_bin" in ctrl_params:
            special_params["image_bin"] = numpy.array(ctrl_params.pop("image_bin"))

        if "image_flip" in ctrl_params:
            special_params["image_flip"] = numpy.array(ctrl_params.pop("image_flip"))

        if "image_rotation" in ctrl_params:
            special_params["image_rotation"] = ctrl_params.pop("image_rotation")

        if "image_roi" in ctrl_params:
            # make sure that image_roi is applied last
            special_params["image_roi"] = numpy.array(ctrl_params.pop("image_roi"))

        # --- Apply standard params (special_params excluded/removed)
        for key, value in ctrl_params.items():
            if self._needs_update(key, value, self.proxy):
                log_debug(self, "apply parameter %s on %s to %s", key, self.name, value)
                setattr(self.proxy, key, value)

        # --- Select special params that must be updated (caching/filtering)
        _tmp = {}
        for key, value in special_params.items():
            if self._needs_update(key, value, self.proxy):
                _tmp[key] = value
        special_params = _tmp

        # be sure to apply the roi as last operation
        if "image_roi" in special_params:
            special_params["image_roi"] = special_params.pop("image_roi")

        # --- Apply special params -----------------------

        for key, value in special_params.items():
            log_debug(self, "apply parameter %s on %s to %s", key, self.name, value)
            setattr(self.proxy, key, value)

        # update lima_hash with last set of parameters
        Cache(self, "lima_hash").value = self._lima_hash

    def get_current_parameters(self):
        return {
            **self.saving.to_dict(),
            **self.processing.to_dict(),
            **self.image.to_dict(),
            **self.accumulation.dict_for_tango_update(),
        }

    def clear_cache(self):
        clear_cache(self)

    @autocomplete_property
    def debug(self):
        if self._debug is None:
            self._debug = LimaDebug(self.name, self._proxy)
        return self._debug

    @autocomplete_property
    def processing(self):
        if self._processing is None:
            self._processing = LimaProcessing(
                self._config_node, self._proxy, f"{self._name_prefix}:processing"
            )
        return self._processing

    @autocomplete_property
    def saving(self):
        if self._saving is None:
            self._saving = LimaSavingParameters(
                self._config_node, self._proxy, f"{self._name_prefix}:saving"
            )
        return self._saving

    @property
    def directories_mapping_names(self):
        return list(self._directories_mapping.keys())

    @property
    def current_directories_mapping(self):
        mapping_name = self._active_dir_mapping.get()
        if mapping_name and mapping_name not in self._directories_mapping:
            self._active_dir_mapping.clear()
            mapping_name = None

        if mapping_name is None:
            # first mapping is selected
            try:
                mapping_name = self.directories_mapping_names[0]
            except IndexError:
                # no mapping
                pass

        return mapping_name

    @property
    def directories_mapping(self):
        mapping_name = self.current_directories_mapping
        return self._directories_mapping.get(mapping_name, [])

    def select_directories_mapping(self, name):
        if name in self._directories_mapping:
            self._active_dir_mapping.set(name)
        else:
            msg = "%s: dir. mapping '%s` does not exist. Should be one of: %s" % (
                self.name,
                name,
                ",".join(self.directories_mapping_names),
            )
            raise ValueError(msg)

    def get_mapped_path(self, path, check_validity=False):
        """Return mapped path depending on camera configuration.

        Without configured mapping it returns the path argument unmodified.
        If check_validity is True, it checks if the mapped path exists
        and returns the answer as an extra boolean flag.
        """
        valid = True
        for mapping in reversed(self.directories_mapping):
            base_path = PurePath(mapping["path"])
            try:
                rel_path = PurePath(path).relative_to(base_path)
            except ValueError:
                continue
            else:
                replace_with = mapping["replace-with"]
                valid = Path(replace_with).exists()
                if "\\" in replace_with or ":" in replace_with:
                    new_path = PureWindowsPath(replace_with) / rel_path
                else:
                    new_path = PurePosixPath(replace_with) / rel_path
                break
        else:
            new_path = path

        if check_validity:
            return str(new_path), valid
        else:
            return str(new_path)

    @autocomplete_property
    def proxy(self):
        return self._proxy

    @autocomplete_property
    def image(self):
        if self._image is None:
            self._image = ImageCounter(self)
            global_map.register(
                self._image, parents_list=[self], children_list=[self._proxy]
            )
        return self._image

    @autocomplete_property
    def shutter(self):
        if self._shutter is None:

            class LimaShutter(
                LimaAttributesAsProperties,
                Shutter,
                proxy=self.proxy,
                prefix="shutter_",
                strip_prefix=True,
            ):
                pass

            self._shutter = LimaShutter(self, self._proxy)
        return self._shutter

    @autocomplete_property
    def last(self):
        if self.__last is None:

            class LimaImageStatus(
                LimaAttributesAsProperties,
                proxy=self.proxy,
                prefix="last_",
                strip_prefix=True,
            ):
                pass

            self.__last = LimaImageStatus()
        return self.__last

    @autocomplete_property
    def acquisition(self):
        if self._acquisition is None:

            class LimaAcquisition(
                LimaAttributesAsProperties,
                proxy=self.proxy,
                prefix="acq_",
                strip_prefix=True,
            ):
                pass

            self._acquisition = LimaAcquisition()
        return self._acquisition

    @autocomplete_property
    def accumulation(self):
        if self._accumulation is None:

            class LimaAccumulation(
                LimaAttributesAsDeferredWriteProperties,
                proxy=self.proxy,
                prefix="acc_",
                strip_prefix=True,
            ):
                pass

            self._accumulation = LimaAccumulation(
                self._config_node,
                name=f"{self._name_prefix}:accumulation",
                path=["accumulation"],
                share_hardware=False,
            )

        return self._accumulation

    @autocomplete_property
    def roi_counters(self):
        if self.__roi_counters is None:
            roi_counters_proxy = self._get_proxy(self._ROI_COUNTERS)
            self.__roi_counters = RoiCounters(roi_counters_proxy, self)

            global_map.register(
                self.__roi_counters,
                parents_list=[self],
                children_list=[roi_counters_proxy],
            )
        return self.__roi_counters

    @autocomplete_property
    def roi_collection(self):
        if self.__roi_collection is None:
            try:
                roi_collection_proxy = self._get_proxy(self._ROI_COLLECTION)
            except (RuntimeError, DevFailed):
                # Lima server doesnt have the roi_collection plugin installed/activated
                return

            else:
                self.__roi_collection = RoiCollectionController(
                    roi_collection_proxy, self
                )
                global_map.register(
                    self.__roi_collection,
                    parents_list=[self],
                    children_list=[roi_collection_proxy],
                )
        return self.__roi_collection

    @autocomplete_property
    def roi_profiles(self):
        if self.__roi_profiles is None:
            roi_profiles_proxy = self._get_proxy(self._ROI_PROFILES)
            self.__roi_profiles = RoiProfileController(roi_profiles_proxy, self)
            global_map.register(
                self.__roi_profiles,
                parents_list=[self],
                children_list=[roi_profiles_proxy],
            )
        return self.__roi_profiles

    @autocomplete_property
    def camera(self):
        if self._camera is None:
            camera_type = self._proxy.lima_type
            proxy = self._get_proxy(camera_type)
            camera_type = camera_type.lower()
            try:
                camera_module = importlib.import_module(
                    ".%s" % camera_type, __package__
                )
            except ImportError:
                camera_class = CameraBase
            else:
                camera_class = camera_module.Camera

            class LimaCamera(camera_class, proxy=proxy):
                pass

            self._camera = LimaCamera(self.name, self, proxy)

            global_map.register(
                self._camera, parents_list=[self], children_list=[proxy]
            )
        return self._camera

    @property
    def camera_type(self):
        return self._proxy.camera_type

    @autocomplete_property
    def bpm(self):
        if self.__bpm is None:
            bpm_proxy = self._get_proxy(Lima._BPM)
            self.__bpm = Bpm(self.name, bpm_proxy, self)
            global_map.register(
                self.__bpm, parents_list=[self], children_list=[bpm_proxy]
            )

        return self.__bpm

    @property
    def available_triggers(self):
        """
        Return all available trigger modes for the camera
        """
        return [v.name for v in self.acquisition.trigger_mode_enum]

    @contextmanager
    def _bg_sub_proxy_context(self, start=True):
        bg_sub_proxy = self._get_proxy(Lima._BG_SUB)
        prev_run_level = bg_sub_proxy.runLevel
        bg_sub_proxy.stop()
        if start:
            bg_sub_proxy.runlevel = 0
            bg_sub_proxy.start()
        try:
            yield bg_sub_proxy
        finally:
            if start:
                bg_sub_proxy.stop()
                bg_sub_proxy.runlevel = prev_run_level

    def _take_background(self, exposure_time):
        with self._bg_sub_proxy_context() as bg_sub:
            bg_sub.takeNextAcquisitionAsBackground()
            self.acquisition.expo_time = exposure_time
            self.acquisition.nb_frames = 1
            self._proxy.prepareAcq()
            try:
                self._proxy.startAcq()
                with gevent.Timeout(exposure_time + 1):
                    while self.acquisition.status.lower() == "running":
                        gevent.sleep(0.1)
            finally:
                self._proxy.stopAcq()
                self.processing.background_source = "image"

    def take_dark(self, exposure_time: float, shutter=None, save: bool = True):
        title = f"{self.name}_background {exposure_time:g}"
        if not save:
            scan = ct(exposure_time, self, title=title)
            self.processing.use_background = False
            with self._bg_sub_proxy_context() as bg_sub_proxy:
                bg_sub_proxy.takeNextAcquisitionAsBackground()
                print("Take background image ...")
                if shutter is None:
                    scan = ct(exposure_time, self, title=title)
                else:
                    with shutter.closed_context:
                        scan = ct(exposure_time, self, title=title)
            print("Activate background correction")
            self.processing.background_source = "image"
            self.processing.use_background = True
        else:
            old_format = self.saving.file_format
            old_prefix = current_session.scan_saving.images_prefix
            old_template = current_session.scan_saving.images_path_template

            with self._bg_sub_proxy_context():
                new_prefix = f"background_{old_prefix}"

                try:
                    current_session.scan_saving.images_prefix = new_prefix
                    current_session.scan_saving.images_path_template = (
                        "background{scan_number}"
                    )

                    self.saving.file_format = "EDF"
                    self.processing.use_background = False

                    print("Take background image ...")
                    if shutter is None:
                        scan = sct(exposure_time, self, title=title)
                    else:
                        with shutter.closed_context:
                            scan = sct(exposure_time, self, title=title)

                    lima_stream = scan.get_channels_dict[f"{self.name}:image"].stream
                    img_file = lima_stream.get_references(0).file_path

                    print("Activate background correction using file:")
                    print(img_file)

                    self.processing.background = self.get_mapped_path(img_file)
                    self.processing.use_background = True
                finally:
                    self.saving.file_format = old_format
                    current_session.scan_saving.images_prefix = old_prefix
                    current_session.scan_saving.images_path_template = old_template

    def _execute_tg_cmd_with_timeout(self, cmd_name, timeout=None, proxy=None):
        """
        Set timeout on Tango device to execute command called 'cmd_name', with given timeout on given proxy

        By default, timeout is None (no timeout) and proxy is the main Lima device

        Workaround Tango issue #859, by using 'command_inout_asynch'
        """
        if proxy is None:
            proxy = self._proxy

        try:
            with gevent.Timeout(timeout):
                with ChangeTangoTimeout(proxy, timeout):
                    reply_id = proxy.command_inout_asynch(cmd_name)
                    while True:
                        try:
                            return proxy.command_inout_reply(reply_id)
                        except AsynReplyNotArrived:
                            gevent.sleep(0.2)
                            continue
        except gevent.Timeout:
            # transform timeout exception into DevFailed
            raise DevFailed(f"Timeout calling '{cmd_name}` on device {self.__tg_url}")

    def prepareAcq(self):
        return self._execute_tg_cmd_with_timeout(
            "prepareAcq", timeout=self.__prepare_timeout
        )

    def startAcq(self):
        self._proxy.startAcq()
        gevent.sleep(self.__start_waittime)
        # When startAcq returns, the camera is not really ready to receive the trigger (#3368)

    def stopAcq(self):
        self._proxy.stopAcq()

    def _get_proxy(self, type_name="LimaCCDs"):
        device_proxy = self._device_proxies.get(type_name)
        if device_proxy is None:
            if type_name == "LimaCCDs":
                device_name = self.__tg_url
            else:
                main_proxy = self.proxy
                device_name = main_proxy.command_inout(
                    "getPluginDeviceNameFromType", type_name.lower()
                )
                if not device_name:
                    raise RuntimeError(
                        "%s: '%s` proxy cannot be found" % (self.name, type_name)
                    )
                if not device_name.startswith("//"):
                    # build 'fully qualified domain' name
                    # '.get_fqdn()' doesn't work
                    db_host = main_proxy.get_db_host()
                    db_port = main_proxy.get_db_port()
                    device_name = "//%s:%s/%s" % (db_host, db_port, device_name)

            device_proxy = DeviceProxy(device_name)
            device_proxy.set_timeout_millis(1000 * self.__tg_timeout)

            self._device_proxies[type_name] = device_proxy

        return device_proxy

    def ensure_minimal_version(self):
        try:
            lima_version = self.proxy.lima_version
        except AttributeError:
            lima_version = "<1.9.1"

        if (
            lima_version == "<1.9.1"
            or Version(lima_version) < self._MINIMAL_LIMA_VERSION
        ):
            raise RuntimeError(
                f"Lima update required on {self.proxy.dev_name()}. "
                f"Found version {lima_version}, but >={self._MINIMAL_LIMA_VERSION} needed."
            )

    def __info__(self):
        attr_list = ("user_detector_name", "camera_model", "camera_type", "lima_type")
        try:
            data = {
                attr.name: ("?" if attr.has_failed else attr.value)
                for attr in self._proxy.read_attributes(attr_list)
            }
        except DevFailed:
            return "Lima {} (Communication error with {!r})".format(
                self.name, self._proxy.dev_name()
            )

        info_str = (
            f"{data['user_detector_name']} - "
            f"{data['camera_model']} ({data['camera_type']}) - Lima {data['lima_type']}\n\n"
            f"Image:\n{self.image.__info__()}\n\n"
            f"Acquisition:\n{self.acquisition.__info__()}\n\n"
            f"{self.roi_counters.__info__()}\n\n"
            f"{self.roi_profiles.__info__()}\n\n"
            f"{self.roi_collection.__info__() if self.roi_collection is not None else 'Roi Collection: server plugin not found!'}\n\n"
            f"{self.bpm.__info__()}\n\n"
            f"{self.saving.__info__()}\n\n"
            f"{self.processing.__info__()}\n"
        )

        return info_str

    def _update_lima_rois(self):
        # do not use the property to avoid recursive calls
        if self.__roi_counters is not None:
            self.__roi_counters._needs_update = True
            self.__roi_counters._restore_rois_from_settings()  # remove this line to post pone the update at next scan

        if self.__roi_profiles is not None:
            self.__roi_profiles._needs_update = True
            self.__roi_profiles._restore_rois_from_settings()  # remove this line to post pone the update at next scan

        if self.__roi_collection is not None:
            self.__roi_collection._needs_update = True
            self.__roi_collection._restore_rois_from_settings()  # remove this line to post pone the update at next scan

    # Expose counters

    @autocomplete_property
    def counters(self):
        counter_groups = self.counter_groups
        counters = list(counter_groups.images)
        if not self.disable_bpm:
            counters += list(counter_groups.bpm)
        counters += list(counter_groups.roi_counters)
        counters += list(counter_groups.roi_profiles)
        counters += list(counter_groups.roi_collection)
        return counter_namespace(counters)

    @autocomplete_property
    def counter_groups(self):
        dct = {}

        # Image counter
        try:
            dct["images"] = counter_namespace([self.image])
        except (RuntimeError, DevFailed):
            dct["images"] = counter_namespace([])

        # BPM counters
        if not self.disable_bpm:
            try:
                dct["bpm"] = counter_namespace(self.bpm.counters)
            except (RuntimeError, DevFailed):
                dct["bpm"] = counter_namespace([])

        # All ROI counters ( => cnt = cam.counter_groups['roi_counters']['r1_sum'], i.e all counters of all rois)
        try:
            dct["roi_counters"] = counter_namespace(self.roi_counters.counters)
        except (RuntimeError, DevFailed):
            dct["roi_counters"] = counter_namespace([])
        else:
            # Specific ROI counters  ( => cnt = cam.counter_groups['r1']['r1_sum'], i.e counters per roi)
            for single_roi_counters in self.roi_counters.iter_single_roi_counters():
                dct[single_roi_counters.name] = counter_namespace(single_roi_counters)

        # All roi_profiles counters
        try:
            dct["roi_profiles"] = counter_namespace(self.roi_profiles.counters)
        except (RuntimeError, DevFailed):
            dct["roi_profiles"] = counter_namespace([])
        else:
            # Specific roi_profiles counters
            for counter in self.roi_profiles.counters:
                dct[counter.name] = counter

        # All roi_collection counters
        if self.roi_collection is not None:
            try:
                dct["roi_collection"] = counter_namespace(self.roi_collection.counters)
            except (RuntimeError, DevFailed):
                dct["roi_collection"] = counter_namespace([])
            else:
                # Specific roi_collection counters
                for counter in self.roi_collection.counters:
                    dct[counter.name] = counter
        else:
            dct["roi_collection"] = counter_namespace([])

        # Default grouped
        default_counters = (
            list(dct["images"])
            + list(dct["roi_counters"])
            + list(dct["roi_profiles"])
            + list(dct["roi_collection"])
        )

        dct["default"] = counter_namespace(default_counters)

        # Return namespace
        return counter_namespace(dct)

    def edit_rois(self, acq_time: typing.Optional[float] = None):
        """
        Edit this detector ROI counters with Flint.

        When called without arguments, it will use the image from specified detector
        from the last scan/ct as a reference. If `acq_time` is specified,
        it will do a `ct()` with the given count time to acquire a new image.

        .. code-block:: python

            # Flint will be open if it is not yet the case
            pilatus1.edit_rois(0.1)

            # Flint must already be open
            ct(0.1, pilatus1)
            pilatus1.edit_rois()
        """
        if acq_time is not None:
            # Open flint before doing the ct
            from bliss.common import scans

            plot_module.get_flint()
            scans.ct(acq_time, self.image)

        # Check that Flint is already there
        flint = plot_module.get_flint()

        def update_image_in_plot():
            """Create a single frame from detector data if available
            else use a placeholder.
            """
            try:
                image_data = image_utils.image_from_server(self._proxy, -1)
                data = image_data.array
            except Exception:
                # Else create a checker board place holder
                y, x = numpy.mgrid[0 : self.image.height, 0 : self.image.width]
                data = ((y // 16 + x // 16) % 2).astype(numpy.uint8) + 2
                data[0, 0] = 0
                data[-1, -1] = 5

            channel_name = f"{self.name}:image"
            flint.set_static_image(channel_name, data)

        # That it contains an image displayed for this detector
        plot_proxy = flint.get_live_plot(self)
        ranges = plot_proxy.get_data_range()
        if ranges[0] is None:
            update_image_in_plot()
        plot_proxy.focus()

        roi_counters = self.roi_counters
        roi_profiles = self.roi_profiles

        # Retrieve all the ROIs
        selections = []
        selections.extend(roi_counters.get_rois())
        selections.extend(roi_profiles.get_rois())

        deviceName = (
            f"{self.name} [{roi_counters.config_name}, {roi_profiles.config_name}]"
        )
        print(f"Waiting for ROI edition to finish on {deviceName}...")
        selections = plot_proxy.select_shapes(
            selections,
            kinds=[
                "lima-rectangle",
                "lima-arc",
                "lima-vertical-profile",
                "lima-horizontal-profile",
            ],
        )

        roi_counters.clear()
        roi_profiles.clear()
        for roi in selections:
            if isinstance(roi, lima_roi.RoiProfile):
                roi_profiles[roi.name] = roi
            else:
                roi_counters[roi.name] = roi

        roi_string = ", ".join(sorted([s.name for s in selections]))
        print(f"Applied ROIS {roi_string} to {deviceName}")

    def start_live(self, acq_time: typing.Optional[float] = 0.1):
        """Start live video of a Lima detector.

        This will also be displayed inside Flint.
        """
        flint = plot_module.get_flint()
        proxy = self.proxy
        flint.start_image_monitoring(self.image.fullname, proxy.name())
        if proxy.video_live:
            proxy.video_live = False
        proxy.acq_trigger_mode = "INTERNAL_TRIGGER"
        proxy.acq_expo_time = acq_time
        proxy.video_source = "BASE_IMAGE"
        proxy.video_exposure = acq_time
        proxy.video_live = True

    def stop_live(self):
        """Stop live video of a Lima detector

        This will also be stopped inside Flint.
        """
        proxy = self.proxy
        proxy.video_live = False
        flint = plot_module.get_flint(creation_allowed=False, mandatory=False)
        if flint is not None:
            flint.stop_image_monitoring(self.image.fullname)
