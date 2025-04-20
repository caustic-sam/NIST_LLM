# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

from __future__ import annotations

import functools
import gevent
import gevent.lock
import gevent.queue
import gevent.event
import os
import weakref
import datetime
import collections
import typing
from typing import Any, Optional
from collections.abc import Callable
import logging
import numpy
from copy import deepcopy
from collections import deque
from importlib.metadata import version
from contextlib import contextmanager, ExitStack

from bliss.common.types import _countable
from bliss import current_session, is_bliss_shell
from bliss.common.axis.axis import Axis, CalcAxis
from bliss.common.motor_group import is_motor_group
from bliss.common.hook import group_hooks, execute_pre_scan_hooks
from bliss.common import event
from bliss.common.cleanup import capture_exceptions
from bliss.common.greenlet_utils import KillMask
from bliss.common.utils import deep_update, typecheck
from bliss.common.data_store import get_default_data_store
from bliss.common.deprecation import deprecated_warning
from bliss.scanning.scan_meta import (
    META_TIMING,
    ScanMeta,
    get_user_scan_meta,
    get_controllers_scan_meta,
)
from bliss.scanning.scan_state import ScanState
from bliss.controllers.motor import get_real_axes
from bliss.common.utils import get_matching_names
from bliss.scanning.chain import AcquisitionSlave, AcquisitionMaster, StopChain
from bliss.scanning.channel import AcquisitionChannel
from bliss.scanning.writer import get_writer_class
from bliss.scanning.scan_debug import ScanDebugger, DEBUG_MODES
from bliss.scanning.scan_info import ScanInfo
from bliss.common.counter import Counter

from blisswriter.nexus.devices import device_info
from blisswriter.nexus.mapping import get_primary_dataset_path

logger = logging.getLogger("bliss.scans")

if typing.TYPE_CHECKING:
    from bliss.scanning.scan_progress import ScanProgress
    from blissdata.scan import Scan as BlissDataScan


_scan_progress_class: type[ScanProgress] | None = None


def get_default_scan_progress():
    if _scan_progress_class:
        return _scan_progress_class()


class ScanAbort(BaseException):
    pass


class WatchdogCallback:
    """
    This class is a watchdog for scan class.  It's role is to follow
    if detectors involved in the scan have the right behavior. If not
    the callback may raise an exception.
    All exception will bubble-up except StopIteration which will just stop
    the scan.
    """

    def __init__(self, watchdog_timeout=1.0):
        """
        watchdog_timeout -- is the maximum calling frequency of **on_timeout**
        method.
        """
        self.__watchdog_timeout = watchdog_timeout

    @property
    def timeout(self):
        return self.__watchdog_timeout

    def on_timeout(self):
        """
        This method is called when **watchdog_timeout** elapsed it means
        that no data event is received for the time specified by
        **watchdog_timeout**
        """
        pass

    def on_scan_new(self, scan, scan_info):
        """
        Called when scan is starting
        """
        pass

    def on_scan_data(self, data_events, scan_info):
        """
        Called when new data are emitted by the scan.  This method should
        raise en exception to stop the scan.  All exception will
        bubble-up exception the **StopIteration**.  This one will just
        stop the scan.
        """
        pass

    def on_scan_end(self, scan_info):
        """
        Called at the end of the scan
        """
        pass


class _WatchDogTask(gevent.Greenlet):
    def __init__(self, scan, callback):
        super().__init__()
        self._scan = weakref.proxy(scan, self._on_proxy_released)
        self._events = gevent.queue.Queue()
        self._data_events = dict()
        self._callback = callback
        self.__watchdog_timer = None
        self._lock = gevent.lock.Semaphore()
        self._lock_watchdog_reset = gevent.lock.Semaphore()

    def trigger_data_event(self, sender, signal):
        self._reset_watchdog()
        event_set = self._data_events.setdefault(sender, set())
        event_set.add(signal)
        if not len(self._events):
            self._events.put("Data Event")

    def on_scan_new(self, scan, scan_info):
        self._callback.on_scan_new(scan, scan_info)
        self._reset_watchdog()

    def on_scan_end(self, scan_info):
        self.stop()
        self._callback.on_scan_end(scan_info)

    def _on_proxy_released(self, proxy):
        self.stop()

    def stop(self):
        self.clear_queue()
        self._events.put(StopIteration)

    def kill(self):
        super().kill()
        if self.__watchdog_timer is not None:
            self.__watchdog_timer.kill()

    def clear_queue(self):
        while True:
            try:
                self._events.get_nowait()
            except gevent.queue.Empty:
                break

    def _run(self):
        try:
            for ev in self._events:
                if isinstance(ev, BaseException):
                    raise ev
                try:
                    if self._data_events:
                        data_event = self._data_events
                        self._data_events = dict()
                        # disable the watchdog before calling the callback
                        if self.__watchdog_timer is not None:
                            self.__watchdog_timer.kill()
                        with KillMask():
                            with self._lock:
                                self._callback.on_scan_data(
                                    data_event, self._scan.scan_info
                                )
                        # reset watchdog if it wasn't restarted in between
                        if not self.__watchdog_timer:
                            self._reset_watchdog()

                except StopIteration:
                    break
        finally:
            if self.__watchdog_timer is not None:
                self.__watchdog_timer.kill()

    def _reset_watchdog(self):
        with self._lock_watchdog_reset:
            if self.__watchdog_timer:
                self.__watchdog_timer.kill()

            if self.ready():
                return

            def loop(timeout):
                while True:
                    gevent.sleep(timeout)
                    try:
                        with KillMask():
                            with self._lock:
                                self._callback.on_timeout()
                    except StopIteration:
                        self.stop()
                        break
                    except BaseException as e:
                        self.clear_queue()
                        self._events.put(e)
                        break

            task = gevent.spawn(loop, self._callback.timeout)
            task.name = "watchdog-timer"
            task.spawn_tree_locals[id(task), "textblock_context_greenlet"] = True
            self.__watchdog_timer = task


class ScanPreset:
    def __init__(self):
        self.__acq_chain = None

    @property
    def acq_chain(self):
        return self.__acq_chain

    def _prepare(self, scan):
        """
        Called on the preparation phase of a scan.
        """
        self.__acq_chain = scan.acq_chain
        self.__new_channel_data = {}
        self.__new_data_callback = None
        return self.prepare(scan)

    def prepare(self, scan):
        """
        Called on the preparation phase of a scan.
        To be overwritten in user scan presets
        """
        pass

    def start(self, scan):
        """
        Called on the starting phase of a scan.
        """
        pass

    def _stop(self, scan):
        if self.__new_channel_data:
            for data_chan in self.__new_channel_data.keys():
                event.disconnect(data_chan, "new_data", self.__new_channel_data_cb)
        self.__new_data_callback = None
        self.__new_channel_data = {}
        return self.stop(scan)

    def stop(self, scan):
        """
        Called at the end of a scan.
        """
        pass

    def __new_channel_data_cb(self, data, sender=None):
        if data is None:
            return
        counter = self.__new_channel_data[sender]
        return self.__new_data_callback(counter, sender.fullname, data)

    def connect_data_channels(self, counters_list, callback):
        """
        Associate a callback to the data emission by the channels of a list of counters.

        Args:
            counters_list: the list of counters to connect data channels to
            callback: a callback function
        """
        nodes = self.acq_chain.get_node_from_devices(*counters_list)
        for i, node in enumerate(nodes):
            try:
                channels = node.channels
            except AttributeError:
                continue
            else:
                self.__new_data_callback = callback
                cnt = counters_list[i]
                for data_chan in channels:
                    self.__new_channel_data[data_chan] = cnt
                    event.connect(data_chan, "new_data", self.__new_channel_data_cb)


class _ScanIterationsRunner:
    """Helper class to execute iterations of a scan

    Uses a generator to execute the different steps, as it receives tasks via 'send'
    """

    def __init__(self):
        self.runner = self._run()  # make generator
        next(self.runner)  # "prime" runner: go to first yield

    def _gwait(self, greenlets, masked_kill_nb=0):
        """Wait until given greenlets are all done

        In case of error, greenlets are all killed and exception is raised

        If a kill happens (GreenletExit or KeyboardInterrupt exception)
        while waiting for greenlets, wait is retried - 'masked_kill_nb'
        allow to specify a number of 'kills' to mask to really kill only
        if it insists.
        """
        try:
            gevent.joinall(greenlets, raise_error=True)
        except (gevent.GreenletExit, KeyboardInterrupt):
            # in case of kill: give a chance to finish the task,
            # but if it insists => let it kill
            if masked_kill_nb > 0:
                with KillMask(masked_kill_nb=masked_kill_nb - 1):
                    gevent.joinall(greenlets)
            raise
        finally:
            if any(
                greenlets
            ):  # only kill if some greenlets are still running, as killall takes time
                gevent.killall(greenlets)

    def _run_next(self, scan, acq_chain_iter):
        acq_chain_iter.start()
        for (
            i
        ) in (
            acq_chain_iter
        ):  # calls acq_chain_iter.__next__() and i is the acq_chain_iter object
            if scan._stop_scan_request:
                break
            i.prepare(scan, scan.scan_info)
            i.start()

    def send(self, arg):
        """Delegate 'arg' to generator"""
        try:
            return self.runner.send(arg)
        except StopIteration:
            pass

    def _run(self):
        """Generator that runs a scan: from applying parameters to acq. objects then preparing and up to stopping

        Goes through the different steps by receiving tasks from the caller Scan object
        """
        apply_parameters_tasks = yield

        # apply parameters in parallel on all iterators
        self._gwait(apply_parameters_tasks)

        # execute prepare tasks in parallel
        prepare_tasks = yield
        self._gwait(prepare_tasks)

        # scan tasks
        scan, chain_iterators, watchdog_task, check_scan_error_task = yield

        tasks = {}
        for iter in chain_iterators:
            task = gevent.spawn(self._run_next, scan, iter)
            task.name = "chain-iterator-run"
            task.spawn_tree_locals[id(task), "textblock_context_greenlet"] = True
            tasks[task] = iter

        if watchdog_task is not None:
            # put watchdog task in list, but there is no corresponding iterator
            tasks[watchdog_task] = None

        tasks[check_scan_error_task] = None

        with capture_exceptions(raise_index=0) as capture:
            with capture():
                try:
                    # gevent.iwait iteratively yield objects as they are ready
                    with gevent.iwait(tasks) as task_iter:
                        # loop over ready tasks until all are consumed, or an
                        # exception is raised
                        for t in task_iter:
                            t.get()  # get the task result ; this may raise an exception

                            if t is watchdog_task:
                                # watchdog task ended: stop the scan
                                raise StopChain
                            elif tasks[t].top_master.terminator:
                                # a task with a terminator top master has finished:
                                # scan has to end
                                raise StopChain
                            elif [tsk for tsk in tasks if not tsk.dead] == [
                                check_scan_error_task
                            ]:
                                # only check_scan_error_task remains
                                raise StopChain

                except StopChain:
                    # stop scan:
                    # kill all tasks, but do not raise an exception
                    gevent.killall(tasks, exception=StopChain)
                except (gevent.GreenletExit, KeyboardInterrupt):
                    # scan gets killed:
                    # kill all tasks, re-raise exception
                    gevent.killall(tasks, exception=gevent.GreenletExit)
                    raise
                except BaseException:
                    # an error occurred: kill all tasks, re-raise exception
                    gevent.killall(tasks, exception=StopChain)
                    raise

            stop_tasks = yield
            with capture():
                self._gwait(stop_tasks, masked_kill_nb=1)


class Scan:
    UNSAVED_SCAN_NUMBER = 0

    def __init__(
        self,
        chain,
        name="scan",
        scan_info=None,
        save=True,
        save_images=None,
        scan_saving=None,
        watchdog_callback=None,
        scan_progress=None,
    ):
        """
        Scan class to publish data and trigger the writer if any.

        Arguments:
            chain: Acquisition chain you want to use for this scan.
            name: Scan name, if None set default name *scan*
            scan_info: Scan parameters if some, as a dict (or as ScanInfo
                       object)
            save: True if this scan have to be saved
            save_images: None means follows "save"
            scan_saving: Object describing how to save the scan, if any
            scan_progress: a ScanProgress instance
        """
        self.__name = name
        self.__scan_number = None
        self.__user_scan_meta = None
        self.__controllers_scan_meta = None
        self._scan_info = ScanInfo()
        self._save = save
        self._scan_data: BlissDataScan | None = None
        self._watchdog_task: _WatchDogTask | None = None
        self._scan_error_queue = gevent.queue.Queue()
        self._check_scan_error_task = None

        self._stop_scan_request = False
        self.__stopped_event = gevent.event.Event()
        self.__stopped_event.set()

        nonsaved_ct = False
        if scan_info:
            nonsaved_ct = scan_info.get("type", None) == "ct" and not save
        self._add_to_scans_queue: bool = not nonsaved_ct
        self._enable_scanmeta: bool = not nonsaved_ct

        self._devices = []
        self._axes_in_scan = []  # for pre_scan, post_scan in axes hooks
        self._restore_motor_positions = False

        self._data_events = dict()
        self.set_watchdog_callback(watchdog_callback)

        self.__state = ScanState.IDLE
        self.__state_change = gevent.event.Event()
        self._preset_list = list()
        self.__comments = list()  # user comments

        # Independent scan initialization (order not important):
        self._init_acq_chain(chain)
        self._init_scan_saving(scan_saving)
        self._init_scan_display()

        # Dependent scan initialization (order is important):
        self._metadata_at_scan_instantiation(scan_info=scan_info, save=save)
        self._init_writer(save=save, save_images=save_images)
        self._init_flint()

        self._scan_progress = scan_progress

        self._scan_debug: ScanDebugger | None = None
        if current_session.scan_debug_mode:
            self._scan_debug = ScanDebugger(self, current_session.scan_debug_mode)
            if self._scan_debug.mode == DEBUG_MODES.CHAIN:
                self._scan_progress = None

        chain.scan = self

    def _init_scan_saving(self, scan_saving):
        if scan_saving is None:
            scan_saving = current_session.scan_saving
        self.__scan_saving = scan_saving.clone()

    @property
    def scan_saving(self):
        return self.__scan_saving

    def _init_scan_display(self):
        self.__scan_display = current_session.scan_display.clone()

    def _init_acq_chain(self, chain):
        """Initialize acquisition chain"""
        self._uniquify_chan_names(chain)
        self._acq_chain = chain

    def _uniquify_chan_names(self, chain):
        channels_names = [c.name for n in chain.nodes_list for c in n.channels]
        seen = set()
        duplicates = [name for name in channels_names if name in seen or seen.add(name)]

        if duplicates:
            nodes = deque(chain._tree.is_branch(chain._tree.root))
            while nodes:
                node = nodes.pop()
                for chan in node.channels:
                    if chan.name in duplicates:
                        try:
                            prefix = chain._tree.get_node(node).bpointer.name
                        except AttributeError:
                            prefix = id(chan)
                        chan._name = f"{prefix}:{chan.name}"
                nodes.extend(chain._tree.is_branch(node))

    def _init_writer(self, save=True, save_images=None):
        """Initialize the data writer"""
        scan_config = self.__scan_saving.get()
        if save:
            self.__writer = scan_config["writer"]
        else:
            self.__writer = get_writer_class("null")(
                scan_config["root_path"],
                scan_config["images_path"],
                os.path.basename(scan_config["data_path"]),
            )
        if save_images is None:
            save_images = save
        self.__writer.enable_device_saving(save_images)

    def _init_flint(self):
        """Initialize flint if needed"""
        if is_bliss_shell():
            scan_display = self.__scan_display
            if scan_display.auto:
                if self.is_flint_recommended():
                    from bliss.common import plot as plot_mdl

                    plot_mdl.get_flint(
                        restart_if_stucked=scan_display.restart_flint_if_stucked,
                        mandatory=False,
                    )

    def is_flint_recommended(self):
        """Return true if flint is recommended for this scan"""
        scan_info = self._scan_info
        kind = scan_info.get("type", None)

        # If there is explicit plots, Flint is helpful
        plots = scan_info.get("plots", [])
        if len(plots) >= 1:
            return True

        # For ct, Flint is only recommended if there is MCAs or images
        if kind == "ct":
            chain = scan_info["acquisition_chain"]
            ndim_data = []
            for _top_master, chain in scan_info["acquisition_chain"].items():
                ndim_data.extend(chain.get("images", []))
                ndim_data.extend(chain.get("spectra", []))
                ndim_data.extend(chain.get("master", {}).get("images", []))
                ndim_data.extend(chain.get("master", {}).get("spectra", []))
            return len(ndim_data) > 0

        return True

    def _init_scan_number(self):
        if self.__scan_number is not None:
            raise RuntimeError("The scan number can be initialized only once")
        self.writer.update_template(
            {
                "scan_name": self.name,
                "session": self.__scan_saving.session,
                "scan_number": "{scan_number}",
                "img_acq_device": "{img_acq_device}",
            }
        )

        if self._save:
            self.__scan_number = self.__scan_saving._incr_scan_number()
        else:
            Scan.UNSAVED_SCAN_NUMBER += 1
            self.__scan_number = Scan.UNSAVED_SCAN_NUMBER

        self.writer.update_template(scan_number=self.scan_number)

    def __repr__(self):
        number = self.__scan_number
        if not self._save:
            number = ""
            path = "'not saved'"
        else:
            number = f"number={self.__scan_number}, "
            path = self.writer.get_filename()

        return f"Scan({number}name={self.name}, path={path})"

    def _check_scan_error_event(self):
        """this will interrupt the scan if an error is found in the queue"""
        for ev in self._scan_error_queue:
            raise ev

    def stop_scan_on_error(self, error):
        self._scan_error_queue.put(error)

    def stop(self, wait=True):
        """ask the scan to finish current iteration and then to properly terminate"""
        self._stop_scan_request = True
        if wait:
            self.__stopped_event.wait()

    def abort(self, wait=True):
        self.stop_scan_on_error(ScanAbort("Abort on request"))
        if wait:
            self.__stopped_event.wait()

    @property
    def name(self):
        return self.__name

    @property
    def state(self) -> ScanState:
        return self.__state

    @property
    def writer(self):
        return self.__writer

    @property
    def acq_chain(self):
        return self._acq_chain

    @property
    def scan_info(self):
        return self._scan_info

    @property
    def scan_number(self):
        if self.__scan_number:
            return self.__scan_saving.scan_number_format % self.__scan_number
        else:
            return "{scan_number}"

    @property
    def identifier(self) -> Optional[str]:
        save = self._save
        scan_nb = self.__scan_number
        if not scan_nb:
            return None
        if save:
            return f"{scan_nb}_{self.name}"
        return f"_{scan_nb}_{self.name}"

    @property
    def restore_motor_positions(self):
        """Weither to restore the initial motor positions at the end of scan run (for dscans)."""
        return self._restore_motor_positions

    @restore_motor_positions.setter
    def restore_motor_positions(self, restore):
        """Weither to restore the initial motor positions at the end of scan run (for dscans)."""
        self._restore_motor_positions = restore

    @property
    def get_channels_dict(self):
        """A dictionary of all channels used in this scan"""
        return {c.name: c for n in self.acq_chain.nodes_list for c in n.channels}

    def add_preset(self, preset):
        """
        Add a preset for this scan
        """
        if not isinstance(preset, ScanPreset):
            raise ValueError("Expected ScanPreset instance")
        self._preset_list.append(preset)

    def set_watchdog_callback(self, callback):
        """
        Set a watchdog callback for this scan
        """
        if callback:
            self._watchdog_task = _WatchDogTask(self, callback)
        else:
            self._watchdog_task = None

    def _get_data_axes(self, include_calc_reals: bool | int = False) -> list[Axis]:
        """
        Return all axes objects in this scan

        Arguments:
            include_calc_reals: It can be:
                - True: get calc axes + real axes,
                - False (default): do not return reals from calc
                - positive integer: real axes from calc ones are returned up to the specified depth
        """
        master_axes = []
        if isinstance(include_calc_reals, bool):
            calc_depth = -1 if include_calc_reals else 0
        else:
            calc_depth = include_calc_reals
        for node in self.acq_chain.nodes_list:
            if not isinstance(node, AcquisitionMaster):
                continue
            if isinstance(node.device, Axis):
                master_axes.append(node.device)
                master_axes += get_real_axes(node.device, depth=calc_depth)
            elif is_motor_group(node.device):
                master_axes += node.device.axes.values()
                master_axes += get_real_axes(
                    *node.device.axes.values(), depth=calc_depth
                )

        return list(dict.fromkeys(master_axes))

    def update_ctrl_params(self, ctrl, new_param_dict):
        if self.state != ScanState.IDLE:
            raise RuntimeError(
                "Scan state is not idle. ctrl_params can only be updated before scan starts running."
            )
        ctrl_acq_dev = None
        for acq_dev in self.acq_chain.nodes_list:
            if ctrl is acq_dev.device:
                ctrl_acq_dev = acq_dev
                break
        if ctrl_acq_dev is None:
            raise RuntimeError(f"Controller {ctrl} not part of this scan!")

        ## for Bliss 2 we have to see how to make acq_params available systematically
        potential_new_ctrl_params = ctrl_acq_dev.ctrl_params.copy()
        potential_new_ctrl_params.update(new_param_dict)

        # invoking the Validator here will only work if we have a
        # copy of initial acq_params in the acq_obj
        # ~ if hasattr(ctrl_acq_dev, "acq_params"):
        # ~ potential_new_ctrl_params = CompletedCtrlParamsDict(
        # ~ potential_new_ctrl_params
        # ~ )
        # ~ ctrl_acq_dev.validate_params(
        # ~ ctrl_acq_dev.acq_params, ctrl_params=potential_new_ctrl_params
        # ~ )

        # at least check that no new keys are added
        if set(potential_new_ctrl_params.keys()) == set(
            ctrl_acq_dev.ctrl_params.keys()
        ):
            ctrl_acq_dev.ctrl_params.update(new_param_dict)
        else:
            raise RuntimeError(f"New keys can not be added to ctrl_params of {ctrl}")

    def _simplify_fit_return(self, res, return_axes: bool):
        """Simplify fit result for users.

        A `ascan` will return a float, a `a2scan` will return a mapping
        containing the each axis and it's position.
        """
        if not return_axes and len(res) == 1:
            return next(iter(res.values()))
        else:
            return res

    def fwhm(self, counter, axis=None, return_axes=False):
        from bliss.scanning import scan_tools

        fits = scan_tools.ScanFits(self)
        res = fits.fwhm(counter=counter, axis=axis)
        return self._simplify_fit_return(res, return_axes=return_axes)

    def peak(self, counter, axis=None, return_axes=False):
        from bliss.scanning import scan_tools

        fits = scan_tools.ScanFits(self)
        res = fits.peak(counter=counter, axis=axis)
        return self._simplify_fit_return(res, return_axes=return_axes)

    def trough(self, counter, axis=None, return_axes=False):
        from bliss.scanning import scan_tools

        fits = scan_tools.ScanFits(self)
        res = fits.trough(counter=counter, axis=axis)
        return self._simplify_fit_return(res, return_axes=return_axes)

    def com(self, counter, axis=None, return_axes=False):
        from bliss.scanning import scan_tools

        fits = scan_tools.ScanFits(self)
        res = fits.com(counter=counter, axis=axis)
        return self._simplify_fit_return(res, return_axes=return_axes)

    def cen(self, counter, axis=None, return_axes=False):
        from bliss.scanning import scan_tools

        fits = scan_tools.ScanFits(self)
        res = fits.cen(counter=counter, axis=axis)
        return self._simplify_fit_return(res, return_axes=return_axes)

    @typecheck
    def find_position(
        self,
        func: Callable[[numpy.ndarray, numpy.ndarray], float],
        counter: _countable,
        axis=None,
        return_axes=False,
    ):
        """Evaluate user supplied scan math function"""
        from bliss.scanning import scan_tools

        fits = scan_tools.ScanFits(self)
        res = fits.find_position(func=func, counter=counter, axis=axis)
        return self._simplify_fit_return(res, return_axes=return_axes)

    def goto_peak(self, counter, axis=None):
        from bliss.scanning import scan_tools

        fits = scan_tools.ScanFits(self)
        fits.goto_peak(counter=counter, axis=axis)

    def goto_min(self, counter, axis=None):
        from bliss.scanning import scan_tools

        fits = scan_tools.ScanFits(self)
        fits.goto_min(counter=counter, axis=axis)

    def goto_com(self, counter, axis=None):
        from bliss.scanning import scan_tools

        fits = scan_tools.ScanFits(self)
        fits.goto_com(counter=counter, axis=axis)

    def goto_cen(self, counter, axis=None):
        from bliss.scanning import scan_tools

        fits = scan_tools.ScanFits(self)
        fits.goto_cen(counter=counter, axis=axis)

    @typecheck
    def goto_custom(
        self,
        func: Callable[[Any, Any], float],
        counter: _countable,
        axis=None,
    ):
        """Goto for custom user supplied scan math function"""
        from bliss.scanning import scan_tools

        fits = scan_tools.ScanFits(self)
        fits.goto_custom(func=func, counter=counter, axis=axis)

    def wait_state(self, state):
        while self.__state < state:
            self.__state_change.clear()
            self.__state_change.wait()

    def __trigger_watchdog_data_event(self, signal, sender):
        if self._watchdog_task is not None:
            self._watchdog_task.trigger_data_event(sender, signal)

    def _channel_event(self, event_dict, signal=None, sender=None):
        self.__trigger_watchdog_data_event(signal, sender)

    def _device_event(self, event_dict=None, signal=None, sender=None):
        if signal == "end":
            self.__trigger_watchdog_data_event(signal, sender)

    def prepare(self, scan_info, devices_tree):
        self._prepare_devices(devices_tree)
        self.writer.prepare(self)

        self._metadata_at_scan_prepared()

        self._axes_in_scan = self._get_data_axes(include_calc_reals=True)
        with execute_pre_scan_hooks(self._axes_in_scan):
            pass

    def _prepare_devices(self, devices_tree):
        # DEPTH expand without the root node
        self._devices = list(devices_tree.expand_tree())[1:]
        for dev in self._devices:
            if isinstance(dev, (AcquisitionSlave, AcquisitionMaster)):
                event.connect(dev, "start", self._device_event)
                event.connect(dev, "end", self._device_event)

    def _metadata_at_scan_instantiation(self, scan_info=None, save=True):
        """Metadata of an "instantiated" scan. Saved in Redis when creating the scan node."""
        if scan_info is not None:
            self._scan_info.update(scan_info)
        scan_saving = self.__scan_saving
        self._scan_info.setdefault("title", self.__name)
        self._scan_info["session_name"] = scan_saving.session
        self._scan_info["user_name"] = scan_saving.user_name
        self._scan_info["data_writer"] = scan_saving.writer
        self._scan_info["writer_options"] = scan_saving.get_writer_options()
        self._scan_info["data_policy"] = scan_saving.data_policy
        self._scan_info["save"] = save
        self._scan_info["publisher"] = "bliss"
        self._scan_info["publisher_version"] = version("bliss")
        self._scan_info.set_acquisition_chain_info(self._acq_chain)

    def _metadata_at_scan_start(self):
        """Metadata of a "started" scan. Saved in Redis when creating the scan node.
        So this is the first scan_info any subscriber sees.
        """
        self._scan_info["scan_nb"] = self.__scan_number
        self._scan_info["name"] = self.identifier

        # this has to be done when the writer is ready
        self._scan_info["filename"] = self.writer.get_filename()
        self._scan_info["images_path"] = self.writer.get_device_root_path()

        # Use ISO8601 time format with timezone to ensure uniqueness.
        # When switching to winter time, it is 2am twice in the night, but timezone is different:
        #    2022-10-30T02:00:00+02:00
        #    2022-10-30T02:00:00+01:00
        self._scan_info["start_time"] = datetime.datetime.now().astimezone().isoformat()
        self._metadata_of_plot()

        self._metadata_of_acq_controllers(META_TIMING.START)
        self._metadata_of_nonacq_controllers(META_TIMING.START)
        self._metadata_of_user(META_TIMING.START)

    def _metadata_at_scan_prepared(self):
        """Metadata of a "prepared" scan. Saved in Redis by `ScanNode.prepared`"""
        self._metadata_of_acq_controllers(META_TIMING.PREPARED)
        self._metadata_of_nonacq_controllers(META_TIMING.PREPARED)
        self._metadata_of_user(META_TIMING.PREPARED)

    def _metadata_at_scan_end(self):
        """Metadata of a "finished" scan. Saved in Redis by `ScanNode.end`"""
        self._metadata_of_acq_controllers(META_TIMING.END)
        self._metadata_of_nonacq_controllers(META_TIMING.END)
        self._metadata_of_user(META_TIMING.END)
        self._scan_info["end_time"] = datetime.datetime.now().astimezone().isoformat()

    def _metadata_of_plot(self):
        # Plot metadata
        display_extra = {}
        displayed_channels = self.__scan_display.displayed_channels
        if displayed_channels is not None:
            # Contextual display request
            display_extra["plotselect"] = displayed_channels
            if self.__scan_display._displayed_channels_time is not None:
                display_extra[
                    "plotselect_time"
                ] = self.__scan_display._displayed_channels_time
        displayed_channels = self.__scan_display._pop_next_scan_displayed_channels()
        if displayed_channels is not None:
            # Structural display request specified for this scan
            display_extra["displayed_channels"] = displayed_channels
        if len(display_extra) > 0:
            self._scan_info["display_extra"] = display_extra

    def _metadata_of_user(self, timing):
        """Update scan_info with user scan metadata. The metadata will be
        stored in the user metadata categories.
        """
        if not self._enable_scanmeta:
            return
        self._evaluate_scan_meta(self._user_scan_meta, timing)

    def _metadata_of_nonacq_controllers(self, timing):
        """Update scan_info with controller scan metadata. The metadata
        will be stored in the "instrument" metadata category under the
        "scan_metadata_name" which is the controller name by default
        (see HasMetadataForScan).
        """
        if not self._enable_scanmeta:
            return
        self._evaluate_scan_meta(self._controllers_scan_meta, timing)

    def _metadata_of_acq_controllers(self, timing):
        """Update the controller Redis nodes with metadata. Update
        the "devices" section of scan_info. Note that the "instrument"
        metadata category or any other metadata category is not modified.
        """
        # Note: not sure why we disable the others but keep the
        #       metadata of the acquisition controllers.
        # if not self._enable_scanmeta:
        #    return

        if self._controllers_scan_meta:
            instrument = self._controllers_scan_meta.instrument
        else:
            instrument = None

        for acq_obj in self.acq_chain.nodes_list:
            # Controllers which implement the HasScanMetadata interface
            # will have their metadata already in scan_info.
            if instrument:
                if instrument.is_set(acq_obj):
                    continue

            # There is a difference between None and an empty dict.
            # An empty dict shows up as a group in the Nexus file
            # while None does not.
            with KillMask(masked_kill_nb=1):
                metadata = acq_obj.get_acquisition_metadata(timing=timing)
            if metadata is None:
                continue

            # Add to the local scan_info, but in a different
            # place than where _controllers_scan_meta would put it
            self._scan_info._set_device_meta(acq_obj, metadata)

    def _evaluate_scan_meta(self, scan_meta, timing):
        """Evaluate the metadata generators of a ScanMeta instance
        and update scan_info.
        """
        assert isinstance(scan_meta, ScanMeta)
        with KillMask(masked_kill_nb=1):
            metadata = scan_meta.to_dict(self, timing=timing)
            if not metadata:
                return
            deep_update(self._scan_info, metadata)
        original = set(self._scan_info.get("scan_meta_categories", []))
        extra = set(scan_meta.used_categories_names())
        self._scan_info["scan_meta_categories"] = list(original | extra)

    @property
    def _user_scan_meta(self):
        if self.__user_scan_meta is None and self._enable_scanmeta:
            self.__user_scan_meta = get_user_scan_meta().copy()
        return self.__user_scan_meta

    @property
    def _controllers_scan_meta(self):
        if self.__controllers_scan_meta is None and self._enable_scanmeta:
            self.__controllers_scan_meta = get_controllers_scan_meta()
        return self.__controllers_scan_meta

    def disconnect_all(self):
        for dev in self._devices:
            if isinstance(dev, (AcquisitionSlave, AcquisitionMaster)):
                for signal in ("start", "end"):
                    event.disconnect(dev, signal, self._device_event)
        self._devices = []

        for node in self.acq_chain.nodes_list:
            for channel in node.channels:
                event.disconnect(channel, "new_data", self._channel_event)

    def _set_state(self, state):
        """Set the scan state"""
        if self.__state < state:
            self.__state = state
            self.__state_change.set()

    def add_comment(self, comment):
        """
        Adds a comment (string + timestamp) to scan_info that will also be
        saved in the file data file together with the scan
        """
        assert isinstance(comment, str)

        if self.__state < ScanState.DONE:
            self.__comments.append(
                {
                    "date": datetime.datetime.now().astimezone().isoformat(),
                    "message": comment,
                }
            )
        else:
            raise RuntimeError(
                "Comments can only be added to scans that have not terminated!"
            )

    @property
    def comments(self):
        """
        list of comments that have been attacht to this scan by the user
        """
        return self.__comments

    def get_data(self, key=None):
        """Return a dictionary of { channel_name: numpy array }.

        It is a 1D array corresponding to the scan points.
        Each point is a named structure corresponding to the counter names.
        """
        deprecated_warning(
            kind="method",
            name="Scan.get_data()",
            replacement='Scan.streams["channel_name"][:]',
            since_version="2.0.0",
            skip_backtrace_count=1,
        )

        if key:
            from blissdata.streams.lima import LimaStream
            from blissdata.streams.lima2 import Lima2Stream

            if isinstance(self.streams[key], (LimaStream, Lima2Stream)):
                return self.streams[key]
            else:
                return self.streams[key][:]

        class DataContainer(collections.abc.Mapping):
            def __init__(self, scan):
                self._scan = scan

            def __info__(self):
                return f"DataContainer uses a key [counter], [motor] or [name_pattern] matching one of these names:\n {list(self._scan.streams.keys())}"

            def __iter__(self):
                yield from self._scan.streams

            def __len__(self):
                return len(self._scan.streams)

            def __contains__(self, key):
                return key in self._scan.streams

            def __getitem__(self, key):
                return self._scan.streams[key][:]

        return DataContainer(self)

    @property
    def streams(self):
        if self._scan_data is None:
            raise RuntimeError("Scan was not yet started")

        class KeyMatcher(collections.abc.Mapping):
            def __init__(self, streams):
                self._streams = streams

            def __iter__(self):
                yield from self._streams

            def __len__(self):
                return len(self._streams)

            def __contains__(self, key):
                try:
                    self[key]
                except (KeyError, TypeError):
                    return False
                else:
                    return True

            def __repr__(self):
                names = ", ".join([f'"{name}"' for name in self._streams.keys()])
                return f"Streams({names})"

            def __getitem__(self, key):
                if isinstance(key, Counter):
                    return self._streams[key.fullname]
                elif isinstance(key, Axis):
                    return self._streams[key.fullname]
                elif isinstance(key, str):
                    try:
                        # maybe a fullname
                        return self._streams[key]
                    except KeyError:
                        pass

                    try:
                        # maybe an axis (comes from config so name is unique)
                        return self._streams[f"axis:{key}"]
                    except KeyError:
                        pass

                    # finally, use key as a pattern to match existing ones
                    matches = get_matching_names(
                        key, self._streams.keys(), strict_pattern_as_short_name=True
                    )[key]
                    if len(matches) > 1:
                        raise KeyError(
                            f"Ambiguous key '{key}', there are several matches -> {matches}"
                        )
                    elif len(matches) == 1:
                        return self._streams[matches[0]]
                    else:
                        raise KeyError(
                            f"{key} not found, try one of those {[x.split(':')[-1] for x in self._streams.keys()]}"
                        )
                else:
                    raise TypeError(
                        f"Key must be str, Counter or Axis, not {type(key).__name__}"
                    )

        return KeyMatcher(self._scan_data.streams)

    def _execute_preset(self, method_name):
        preset_tasks = []
        for preset in self._preset_list:
            task = gevent.spawn(getattr(preset, method_name), self)
            task.name = "scan-preset"
            preset_tasks.append(task)
        try:
            gevent.joinall(preset_tasks, raise_error=True)
        except BaseException:
            gevent.killall(preset_tasks)
            raise

    def _run(self):
        from bliss.scanning.monitoring import start_all_monitoring, stop_all_monitoring

        if self.state != ScanState.IDLE:
            raise RuntimeError(
                "Scan state is not idle. Scan objects can only be used once."
            )

        if not isinstance(self, MonitoringScan):
            stop_all_monitoring()

        with ExitStack() as cstack:
            # Perpare error capturing. This needs to be the first
            # context in the stack.
            ctx = capture_exceptions(raise_index=0)
            capture = cstack.enter_context(ctx)
            capture = self._capture_with_error_mapping(capture)
            cstack.enter_context(capture())

            def add_context(ctx):
                # Ensures that context enter exceptions are always
                # captured before context exit exceptions
                yielded_value = cstack.enter_context(ctx)
                cstack.enter_context(capture())
                return yielded_value

            # A stack of context managers before running the actual
            # scan loop:

            if self._scan_progress is not None:
                ctx = self._runctx_scan_progress(capture)
                add_context(ctx)

            ctx = self._runctx_before_scan_state(capture)
            add_context(ctx)

            ctx = self._runctx_scan_state(capture)
            add_context(ctx)

            if self.restore_motor_positions:
                ctx = self._runctx_restore_motor_positions(capture)
                add_context(ctx)

            ctx = self._runctx_scan_saving(capture)
            add_context(ctx)

            ctx = self._runctx_scan_data(capture)
            add_context(ctx)

            if self._watchdog_task is not None:
                ctx = self._runctx_watchdog(capture)
                add_context(ctx)

                ctx = self._runctx_watchdog_callback(capture)
                add_context(ctx)

            if self._scan_debug is not None:
                ctx = self._runctx_scan_debug(capture)
                add_context(ctx)

            # The actually scan loop
            ctx = self._runctx_scan_runner(capture)
            runner = add_context(ctx)
            self._execute_scan_runner(runner)

        if not isinstance(self, MonitoringScan):
            start_all_monitoring()

    def run(self):
        try:
            self.__stopped_event.clear()
            self._run()
        finally:
            self.__stopped_event.set()

    def _execute_scan_runner(self, runner):
        # get scan iterators
        # be careful: this has to be done after "scan_new" callback,
        # since it is possible to add presets in the callback...
        scan_chain_iterators = [next(i) for i in self.acq_chain.get_iter_list()]

        # prepare acquisition objects (via AcquisitionChainIter)
        tasks = []
        for i in scan_chain_iterators:
            task = gevent.spawn(i.apply_parameters)
            task.name = "scan-chain-apply-parameters"
            task.spawn_tree_locals[id(task), "textblock_context_greenlet"] = True
            tasks.append(task)
        runner.send(tasks)

        self._set_state(ScanState.PREPARING)

        self._execute_preset("_prepare")

        self.prepare(self.scan_info, self.acq_chain._tree)

        tasks = []
        for i in scan_chain_iterators:
            task = gevent.spawn(i.prepare, self, self.scan_info)
            task.name = "scan-chain-prepare"
            task.spawn_tree_locals[id(task), "textblock_context_greenlet"] = True
            tasks.append(task)
        runner.send(tasks)

        if self.writer.saving_enabled():
            bliss_devices = self.scan_info["nexuswriter"]["devices"]
            writer_devices = device_info(deepcopy(bliss_devices), self.scan_info)

        assert self._scan_data is not None

        for top_master in self.acq_chain.top_masters:
            subtree = self.acq_chain.tree.subtree(top_master)
            for acq_obj in subtree.expand_tree():
                for channel in acq_obj.channels:
                    if self.writer.saving_enabled() and isinstance(
                        channel, AcquisitionChannel
                    ):
                        number = self._scan_data.number
                        sub_path = get_primary_dataset_path(
                            channel.name, writer_devices[top_master.name]
                        )
                        stream_definition = channel.stream_definition(
                            file_path=self._scan_data.path,
                            data_path=f"{number}.1/{sub_path}",
                        )
                    else:
                        stream_definition = channel.stream_definition()

                    stream = self._scan_data.create_stream(stream_definition)
                    channel.set_stream(stream)
                    event.connect(channel, "new_data", self._channel_event)

        if self.__comments:
            self._scan_info.update({"comments": self.__comments.copy()})

        if self.__scan_saving.data_policy == "ESRF":
            # embed current dataset metadata into the scan
            dataset_meta = self.scan_saving.dataset.get_current_icat_metadata()
            self._scan_info["dataset_metadata_snapshot"] = dataset_meta

        self._scan_data.info = self._scan_info
        self._scan_data.prepare()

        self._set_state(ScanState.STARTING)

        self._execute_preset("start")

        self._scan_data.info = self._scan_info
        self._scan_data.start()

        self._check_scan_error_task = gevent.spawn(self._check_scan_error_event)
        self._check_scan_error_task.name = "check_scan_error_task"

        runner.send(
            (
                self,
                scan_chain_iterators,
                self._watchdog_task,
                self._check_scan_error_task,
            )
        )

        self._set_state(ScanState.STOPPING)

        tasks = []
        for i in scan_chain_iterators:
            if i is not None:
                task = gevent.spawn(i.stop)
                task.name = "scan-chain-stop"
                task.spawn_tree_locals[id(task), "textblock_context_greenlet"] = True
                tasks.append(task)
        runner.send(tasks)

    def _capture_with_error_mapping(self, capture):
        """Error mapping:
        - KeyboardInterrupt -> ScanAbort
        """

        @functools.wraps(capture)
        def wrapper():
            with capture():
                try:
                    yield
                except KeyboardInterrupt as e:
                    raise ScanAbort from e

        return contextmanager(wrapper)

    @contextmanager
    def _runctx_scan_state(self, capture):
        try:
            yield
        finally:
            with capture():
                if capture.failed:
                    _, first_error, _ = capture.failed[0]  # sys.exec_info()
                    if isinstance(first_error, (KeyboardInterrupt, ScanAbort)):
                        self._set_state(ScanState.USER_ABORTED)
                    else:
                        self._set_state(ScanState.KILLED)
                else:
                    self._set_state(ScanState.DONE)

    @contextmanager
    def _runctx_restore_motor_positions(self, capture):
        """
        Context to restore the axis involved by the scan at the termination.
        """
        first_level_scan_axes = self._get_data_axes()
        motor_positions: list[Axis | float] = []
        for mot in self._get_data_axes(include_calc_reals=1):
            if mot not in first_level_scan_axes or not isinstance(mot, CalcAxis):
                pos = mot._set_position
                if not numpy.isnan(
                    pos
                ):  # exclude axes with Nan position (see issue #3762)
                    motor_positions.append(mot)
                    motor_positions.append(pos)
        try:
            yield
        finally:
            with capture():
                from bliss.shell.standard import move
                from bliss.shell.standard import text_block

                with text_block() as tb:
                    tb.set_text("Restore motor position")

                    move(*motor_positions)

    @contextmanager
    def _runctx_scan_progress(self, capture):
        with self._scan_progress.exec_context(self, capture):
            yield

    @contextmanager
    def _runctx_scan_debug(self, capture):
        assert self._scan_debug is not None
        self._scan_debug.connect()
        try:
            yield
        finally:
            with capture():
                self._scan_debug.disconnect()

    @contextmanager
    def _runctx_watchdog(self, capture):
        assert self._watchdog_task is not None
        self._watchdog_task.start()
        try:
            yield
        finally:
            with capture():
                self._watchdog_task.kill()

    @contextmanager
    def _runctx_watchdog_callback(self, capture):
        assert self._watchdog_task is not None
        self._watchdog_task.on_scan_new(self, self.scan_info)
        try:
            yield
        finally:
            with capture():
                self._watchdog_task.on_scan_end(self.scan_info)

    @contextmanager
    def _runctx_scan_saving(self, capture):
        self._init_scan_number()
        self.__scan_saving.on_scan_run(self._save)
        try:
            yield
        finally:
            with capture():
                self.writer.finalize(self)
            with capture():
                self.writer.cleanup()

    @contextmanager
    def _runctx_scan_data(self, capture):
        self._metadata_at_scan_start()

        scan_id = {
            "name": self.name,
            "number": self.__scan_number,
            "data_policy": self.__scan_saving.data_policy,
            "session": self.__scan_saving.session,
        }
        if self._save:
            scan_id["path"] = self.__scan_saving.filename
        if self.__scan_saving.data_policy == "ESRF":
            scan_id["proposal"] = self.__scan_saving.proposal_name
            scan_id["collection"] = self.__scan_saving.collection_name
            scan_id["dataset"] = self.__scan_saving.dataset_name

        self._scan_data = get_default_data_store().create_scan(
            identity=scan_id,
            info=self._scan_info,
        )
        try:
            yield
        finally:
            with capture():
                if not capture.failed:
                    self._scan_data.info = self._scan_info
                    self._scan_data.stop()

            with capture():
                if self.__comments:
                    self._scan_info.update({"comments": self.__comments})

            with capture():
                self._metadata_at_scan_end()

            with capture():
                if capture.failed:
                    _, first_error, _ = capture.failed[0]
                    if isinstance(first_error, (KeyboardInterrupt, ScanAbort)):
                        self._scan_info["end_reason"] = "USER_ABORT"
                    else:
                        self._scan_info["end_reason"] = "FAILURE"
                else:
                    self._scan_info["end_reason"] = "SUCCESS"

                self._scan_data.info = self._scan_info

                try:
                    self._scan_data.close()
                except TypeError as e:
                    # restore scan_info from redis to only set 'end_reason' before closing
                    self._scan_data._model = self._scan_data._model.get(
                        self._scan_data._model.pk
                    )
                    self._scan_data.info["end_reason"] = "FAILURE"
                    self._scan_data.close()
                    raise Exception("Uploading of scan info has failed") from e

    @contextmanager
    def _runctx_scan_runner(self, capture):
        try:
            yield _ScanIterationsRunner()
        finally:
            with capture():
                self._set_state(ScanState.STOPPING)

            with capture():
                self.disconnect_all()

            with capture():
                self._execute_preset("_stop")

    @contextmanager
    def _runctx_before_scan_state(self, capture):
        """Pre and post actions that do not affect the Scan state"""
        with capture():
            if self._add_to_scans_queue:
                current_session.scans.append(self)
        try:
            yield
        finally:
            with capture():
                hooks = group_hooks(self._axes_in_scan)
                for hook in reversed(list(hooks)):
                    with capture():
                        hook.post_scan(self._axes_in_scan[:])


class MonitoringScan(Scan):
    pass
