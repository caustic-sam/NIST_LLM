# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.
"""
Scan meta is a way to add metadata for any scans.

Categories will be represent by groups underneath the scan
group except from POSITIONERS.
"""
__all__ = ["get_user_scan_meta"]

import time
import enum
import pprint
import copy as copy_module
from functools import partial
from typing import Union, Optional
from collections.abc import Sequence, Callable

import gevent

from bliss import global_map, current_session, is_bliss_shell
from bliss.common.proxy import Proxy
from bliss.common.protocols import HasMetadataForScan, HasMetadataForScanExclusive
from bliss.common.utils import deep_update
from bliss.common.logtools import log_exception, log_warning, log_error
from bliss.scanning.scan_state import ScanState


class META_TIMING(enum.Flag):
    START = enum.auto()
    END = enum.auto()
    PREPARED = enum.auto()


class _ScanMetaCategory:
    """Provides an API part of the metadata belonging to one category"""

    def __init__(
        self,
        name: str,
        metadata: dict[str, Union[dict, Callable]],
        timing: dict[str, META_TIMING],
    ):
        self._name = name
        self._metadata = metadata
        self._timing = timing

    @property
    def name(self):
        return self._name

    @property
    def metadata(self):
        return self._metadata.setdefault(self.name, dict())

    @property
    def timing(self):
        return self._timing.setdefault(self.name, META_TIMING.START)

    @timing.setter
    def timing(self, timing):
        self._timing[self.name] = timing

    def _parse_metadata_name(self, name_or_device):
        """
        :param name_or_device: string or an object with a name property
        :returns str or None:
        """
        if isinstance(name_or_device, str):
            if not name_or_device:
                log_error(self, "a name is required to publish scan metadata")
                return
            return name_or_device
        else:
            try:
                name = name_or_device.name
                if name:
                    return name
            except AttributeError:
                pass
            log_error(
                self, f"{repr(name_or_device)} needs a name to publish scan metadata"
            )
            return None

    def set(self, name_or_device, values):
        """
        :param name_or_device: string or an object with a name property
        :param callable or dict values: callable needs to return a dictionary
        """
        name = self._parse_metadata_name(name_or_device)
        if name:
            self.metadata[name] = values

    def is_set(self, name_or_device) -> bool:
        """
        :param name_or_device: string or an object with a name property
        :returns bool:
        """
        name = self._parse_metadata_name(name_or_device)
        return name in self.metadata

    def remove(self, name_or_device):
        """
        :param name_or_device: string or an object with a name property
        """
        name = self._parse_metadata_name(name_or_device)
        metadata = self.metadata
        metadata.pop(name, None)
        if not metadata:
            self._metadata.pop(self.category, None)

    @property
    def keys(self):
        return list(self.metadata.keys())

    def __info__(self):
        s = pprint.pformat(self.metadata, indent=2)
        return f"{self.__class__.__name__}{self.name}: \n " + s


class ScanMeta:
    """Register metadata for all scans. The `Scan` object will call `ScanMeta.to_dict`
    to generate the metadata.

    To add static metadata for a particular scan you pass it to the scan as an argument:

        scan_info={"instrument": "mydetector":{"@NX_class": "NXdetector", "myparam": 1}}
        s = loopscan(..., scan_info={"instrument": "mydetector":{"myparam": 1}})
    """

    _CATEGORIES = {"instrument", "positioners", "technique"}

    def __init__(
        self,
        metadata: Optional[dict[str, Union[dict, Callable]]] = None,
        timing: dict[str, META_TIMING] = None,
    ):
        if metadata is None:
            self._metadata = dict()
        else:
            self._metadata = metadata
        if timing is None:
            self._timing = dict()
        else:
            self._timing = timing

    @classmethod
    def categories_names(cls):
        return sorted(cls._CATEGORIES)

    @classmethod
    def add_categories(cls, names: Sequence[str]):
        cls._CATEGORIES.update(s.lower() for s in names)

    @classmethod
    def remove_categories(cls, names: Sequence[str]):
        cls._CATEGORIES -= {s.lower() for s in names}

    def __getattr__(self, attname):
        cat = self._scan_meta_category(attname)
        if cat is None:
            raise AttributeError(attname)
        return cat

    def _scan_meta_category(self, category: str) -> Optional[_ScanMetaCategory]:
        if category in self._CATEGORIES:
            return _ScanMetaCategory(category, self._metadata, self._timing)

    def _metadata_keys(self):
        for category in list(self._metadata):
            if category in self._CATEGORIES:
                yield category
            else:
                # Category was removed by `remove_categories`
                self._metadata.pop(category, None)

    def _iter_metadata(
        self, timing: Optional[META_TIMING] = None
    ) -> tuple[str, str, Union[Callable, dict]]:
        for category in self._metadata_keys():
            smcategory = self._scan_meta_category(category)
            if smcategory is None:
                continue
            if timing is not None and timing not in smcategory.timing:
                # Category metadata should not be generated at this time
                continue
            for key, values in smcategory.metadata.items():
                if callable(values):
                    yield key, category, partial(
                        self._evaluate_metadata_values, category, key, values
                    )
                else:
                    yield key, category, values

    def _evaluate_metadata_values(self, category, key, func, scan):
        try:
            return func(scan)
        except Exception:
            err_msg = f"cannot generate metadata from controller {repr(key)} for metadata category {repr(category)}"
            log_exception(self, err_msg)

    def _profile_metadata_gathering(
        self, scan=None, timing: Optional[META_TIMING] = None
    ) -> tuple[str, str, float]:
        """Generate metadata for profiling (mimics self.to_dict but not parallelized).
        When no scan is provided, not all metadata can be profiled.
        Return a tuple (key, category, dt).
        """
        result = []
        for key, category, values_or_func in self._iter_metadata(timing):
            t0 = time.perf_counter()
            if callable(values_or_func):
                values = values_or_func(scan)
                if values is None:
                    result.append((key, category, float("nan")))
                    continue
            dt = time.perf_counter() - t0
            result.append((key, category, dt))
        return result

    def to_dict(self, scan, timing: Optional[META_TIMING] = None) -> dict:
        """Generate metadata (parallelized)"""
        result = dict()
        categories = list()
        tasks = list()

        try:
            t0 = time.perf_counter()
            for _, category, values_or_func in self._iter_metadata(timing):
                if callable(values_or_func):
                    categories.append(category)
                    tasks.append(gevent.spawn(values_or_func, scan))
                elif values_or_func is not None:
                    cat_dict = result.setdefault(category, {})
                    deep_update(cat_dict, values_or_func)

            for category, task in zip(categories, tasks):
                values = task.get()
                if values is not None:
                    cat_dict = result.setdefault(category, {})
                    deep_update(cat_dict, values)
        finally:
            gevent.killall(tasks)

        dt = time.perf_counter() - t0
        if is_bliss_shell() and dt > 0.15:
            log_warning(
                self,
                f"metadata gathering took {dt*1000:.3f}ms, type 'metadata_profiling()' for more details",
            )
        return result

    def clear(self):
        """Clear all metadata"""
        self._metadata.clear()

    def _metadata_copy(self):
        mdcopy = dict()
        for category in self._metadata_keys():
            mdcat = mdcopy[category] = dict()
            for key, values in self._metadata[category].items():
                # A deep copy of an object method appears to copy
                # the object itself
                if not callable(values):
                    values = copy_module.deepcopy(values)
                mdcat[key] = values
        return mdcopy

    def copy(self):
        return self.__class__(
            metadata=self._metadata_copy(), timing=copy_module.copy(self._timing)
        )

    def used_categories_names(self):
        return sorted(self._metadata)

    def __info__(self):
        s = pprint.pformat(self._metadata, indent=2)
        return f"{self.__class__.__name__}: \n " + s


def fill_positioners(scan):
    suffix = "_start"
    # scan can be None if called from metadata_profiling (see standard.py)
    if scan is not None and scan.state >= ScanState.STOPPING:
        suffix = "_end"
    positioners = dict()
    positioners_dial = dict()
    units = dict()
    for (
        axis,
        disabled,
        error,
        axis_pos,
        axis_dial_pos,
        unit,
    ) in global_map.get_axes_positions_iter(on_error="ERR"):
        if error:
            positioners[axis.name] = error
            positioners_dial[axis.name] = error
            units[axis.name] = unit
        elif not disabled:
            positioners[axis.name] = axis_pos
            positioners_dial[axis.name] = axis_dial_pos
            units[axis.name] = unit

    rd = {
        "positioners" + suffix: positioners,
        "positioners_dial" + suffix: positioners_dial,
    }

    if scan is not None and scan.state != ScanState.STOPPING:
        rd["positioners_units"] = units

    return rd


def create_user_scan_meta():
    usm = ScanMeta()
    usm.instrument.set("@NX_class", {"@NX_class": "NXinstrument"})
    usm.instrument.timing = META_TIMING.END
    usm.technique.set("@NX_class", {"@NX_class": "NXcollection"})
    return usm


def get_user_scan_meta(default_scan_meta=create_user_scan_meta()):
    """A single instance is used for the session"""
    try:
        return current_session.user_scan_meta
    except AttributeError:
        # no session
        return default_scan_meta


USER_SCAN_META = Proxy(get_user_scan_meta)


def get_controllers_scan_meta():
    """A new instance is created for every scan."""
    scan_meta = ScanMeta()
    scan_meta.instrument.set("@NX_class", {"@NX_class": "NXinstrument"})
    scan_meta.positioners.set("positioners", fill_positioners)
    scan_meta.positioners.timing = META_TIMING.START | META_TIMING.END

    for obj in global_map.instance_iter("controllers"):
        if isinstance(obj, HasMetadataForScan):
            if isinstance(obj, HasMetadataForScanExclusive):
                # metadata for this controller has to be gathered by acq. chain
                continue
            if not obj.scan_metadata_enabled:
                continue
            scan_meta.instrument.set(obj, obj._generate_metadata)
    return scan_meta
