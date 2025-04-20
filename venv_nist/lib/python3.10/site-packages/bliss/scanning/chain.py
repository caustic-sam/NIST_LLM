# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

import logging
import weakref
import enum
import collections
from contextlib import contextmanager
from collections.abc import Iterator, Generator

import gevent
from treelib import Tree

from bliss.common.protocols import HasMetadataForScan
from bliss.common.event import dispatcher
from bliss.common.alias import CounterAlias
from bliss.common.cleanup import capture_exceptions
from bliss.common.greenlet_utils import KillMask
from bliss.scanning.channel import AcquisitionChannelList, AcquisitionChannel
from bliss.scanning.channel import duplicate_channel, attach_channels
from bliss.common.validator import BlissValidator
from bliss.scanning.scan_meta import META_TIMING
from bliss.scanning import scan_debug

TRIGGER_MODE_ENUM = enum.IntEnum("TriggerMode", "HARDWARE SOFTWARE")


# Running task for a specific device
#
_running_task_on_device = weakref.WeakValueDictionary()
_logger = logging.getLogger("bliss.scanning.chain")


# Used to stop a greenlet and avoid logging this exception
class StopTask(gevent.GreenletExit):
    pass


# Normal chain stop
class StopChain(StopTask):
    pass


@contextmanager
def join_tasks(
    caller: str, raise_error: bool = True, raise_on_first: bool = True
) -> Generator[list[gevent.Greenlet], None, None]:
    """Wraps a context that creates multiple greenlets.
    When the context does not raise an exception, join all greenlets.

    When the context does not raise an exception, join all greenlets. Optionally
    the exception from the first finished greenlet can be re-raised (the default) and
    optionally this re-raising happens after all greenlets have finished (the default).

    When the context does raise an exception (e.g. caused by a CTRL-C), kill all greenlets.
    """
    greenlets = list()
    _logger.debug("Chain tasks '%s': enter", caller)
    try:
        yield greenlets
        if not greenlets:
            return
        _logger.debug("Chain tasks '%s': join %s", caller, greenlets)
        if raise_error:
            if not raise_on_first:
                gevent.joinall(greenlets)
            gevent.joinall(greenlets, raise_error=True)
        else:
            gevent.joinall(greenlets, raise_error=False)
    except StopTask as e:
        with KillMask(masked_kill_nb=1):
            if any(greenlets):
                _logger.debug(
                    "Chain tasks '%s': kill %s because of %s",
                    caller,
                    greenlets,
                    type(e).__name__,
                )
                gevent.killall(greenlets, exception=type(e))
        raise
    except BaseException as e:
        with KillMask(masked_kill_nb=1):
            if any(greenlets):
                _logger.debug(
                    "Chain tasks '%s': kill %s because of %s",
                    caller,
                    greenlets,
                    type(e).__name__,
                )
                gevent.killall(greenlets)
        raise
    finally:
        _logger.debug("Chain tasks '%s': exit", caller)


class AbstractAcquisitionObjectIterator:
    """
    Iterate over an AcquisitionObject, yielding self.
    """

    @property
    def acquisition_object(self):
        raise NotImplementedError

    def __next__(self):
        """Returns self"""
        raise NotImplementedError

    def __getattr__(self, name):
        """Get attribute from the acquisition object"""
        if name.startswith("__"):
            raise AttributeError(name)
        return getattr(self.acquisition_object, name)


class AcquisitionObjectIteratorObsolete(AbstractAcquisitionObjectIterator):
    """Use for acquisition objects that are not iterable."""

    def __init__(self, acquisition_object):
        super().__init__()
        self.__acquisition_object_ref = weakref.ref(acquisition_object)
        self.__sequence_index = 0

    @property
    def acquisition_object(self):
        return self.__acquisition_object_ref()

    def __next__(self):
        if not self.acquisition_object.parent:
            raise StopIteration
        once = (
            self.acquisition_object.prepare_once or self.acquisition_object.start_once
        )
        if not once:
            self.acquisition_object.acq_wait_reading()
        self.__sequence_index += 1
        return self

    def acq_prepare(self):
        if self.__sequence_index > 0 and self.acquisition_object.prepare_once:
            return
        self.acquisition_object.acq_prepare()

    def acq_start(self):
        if self.__sequence_index > 0 and self.acquisition_object.start_once:
            return
        self.acquisition_object.acq_start()


class AcquisitionObjectIterator(AbstractAcquisitionObjectIterator):
    """Use for acquisition objects that are iterable."""

    def __init__(self, acquisition_object):
        super().__init__()
        self.__acquisition_object = weakref.proxy(acquisition_object)
        self.__iterator = iter(acquisition_object)
        self.__current_acq_object = None
        next(self)

    @property
    def acquisition_object(self):
        return self.__current_acq_object

    def __next__(self):
        try:
            self.__current_acq_object = next(self.__iterator)
        except StopIteration:
            if not self.__acquisition_object.parent:
                # master iterator has finished iterating
                raise
            self.__acquisition_object.acq_wait_reading()
            # Restart iterating:
            self.__iterator = iter(self.__acquisition_object)
            self.__current_acq_object = next(self.__iterator)
        except Exception as e:
            e.args = (self.__acquisition_object.name, *e.args)
            raise
        return self


class ChainPreset:
    """
    This class interface will be called by the chain object
    at the beginning and at the end of a chain iteration.

    A typical usage of this class is to manage the opening/closing
    by software or to control beamline multiplexer(s)
    """

    def get_iterator(self, chain):
        """Yield ChainIterationPreset instances, if needed"""
        pass

    def prepare(self, chain):
        """
        Called on the preparation phase of the chain iteration.
        """
        pass

    def start(self, chain):
        """
        Called on the starting phase of the chain iteration.
        """
        pass

    def before_stop(self, chain):
        """
        Called at the end of the scan just before calling **stop** on detectors
        """
        pass

    def stop(self, chain):
        """
        Called at the end of the chain iteration.
        """
        pass


class ChainIterationPreset:
    """
    Same usage of the Preset object except that it will be called
    before and at the end of each iteration of the scan.
    """

    def prepare(self):
        """
        Called on the preparation phase of each scan iteration
        """
        pass

    def start(self):
        """
        called on the starting phase of each scan iteration
        """
        pass

    def stop(self):
        """
        Called at the end of each scan iteration
        """
        pass


class CompletedCtrlParamsDict(dict):
    """Subclass dict to convay the message to AcqObj
    that ctrl_params have already be treated
    """

    pass


def update_ctrl_params(controller, scan_specific_ctrl_params):
    from bliss.controllers.counter import CounterController

    if isinstance(controller, CounterController):
        parameters = controller.get_current_parameters()
        if parameters and isinstance(parameters, dict):
            parameters = parameters.copy()
            if not scan_specific_ctrl_params:
                return CompletedCtrlParamsDict(parameters)
            else:
                parameters.update(scan_specific_ctrl_params)
                return CompletedCtrlParamsDict(parameters)

    return CompletedCtrlParamsDict({})


class AcquisitionObject:
    def __init__(
        self,
        *devices,
        name=None,
        npoints=1,
        trigger_type=TRIGGER_MODE_ENUM.SOFTWARE,
        prepare_once=False,
        start_once=False,
        ctrl_params=None,
    ):
        self.__name = name
        self.__parent = None
        self.__channels = AcquisitionChannelList()
        self.__npoints = npoints
        self.__trigger_type = trigger_type
        self.__prepare_once = prepare_once
        self.__start_once = start_once
        self.__acq_chain_iter = None
        self._reading_task = None
        self._counters = collections.defaultdict(list)
        self._init(devices)

        if not isinstance(ctrl_params, CompletedCtrlParamsDict):
            self._ctrl_params = self.init_ctrl_params(self.device, ctrl_params)
        else:
            self._ctrl_params = ctrl_params

    def init_ctrl_params(self, device, ctrl_params):
        """ensure that ctrl-params have been completed"""
        if isinstance(ctrl_params, CompletedCtrlParamsDict):
            return ctrl_params
        else:
            return update_ctrl_params(device, ctrl_params)

    @classmethod
    def validate_params(cls, acq_params, ctrl_params=None):

        params = {"acq_params": acq_params}

        if ctrl_params:
            assert isinstance(ctrl_params, CompletedCtrlParamsDict)
            params.update({"ctrl_params": ctrl_params})

        validator = BlissValidator(cls.get_param_validation_schema())

        if validator(params):
            return validator.normalized(params)["acq_params"]
        else:
            raise RuntimeError(str(validator.errors))

    @classmethod
    def get_default_acq_params(cls):
        return cls.validate_acq_params({})  # GNI ?

    def _init(self, devices):
        self._device, counters = self.init(devices)

        for cnt in counters:
            self.add_counter(cnt)

    def init(self, devices):
        """Return the device and counters list"""
        if devices:
            from bliss.common.counter import Counter  # beware of circular import
            from bliss.common.motor_group import Group
            from bliss.common.axis.axis import Axis

            if all(isinstance(dev, Counter) for dev in devices):
                return devices[0]._counter_controller, devices
            elif all(isinstance(dev, Axis) for dev in devices):
                return Group(*devices), []
            else:
                if len(devices) == 1:
                    return devices[0], []
        else:
            return None, []
        raise TypeError(
            "Cannot handle devices which are not all Counter or Axis objects, or a single object",
            devices,
        )

    @property
    def acq_chain_iter(self):
        if self.__acq_chain_iter is not None:
            return self.__acq_chain_iter()

    @acq_chain_iter.setter
    def acq_chain_iter(self, value):
        self.__acq_chain_iter = weakref.ref(value)

    @property
    def parent(self):
        return self.__parent

    @parent.setter
    def parent(self, p):
        self.__parent = p
        if p.device == self.device and self.device is not None:
            self.__start_once = p.start_once
            self.__prepare_once = p.prepare_once

    @property
    def trigger_type(self):
        return self.__trigger_type

    @property
    def prepare_once(self):
        return self.__prepare_once

    @property
    def start_once(self):
        return self.__start_once

    @property
    def device(self):
        return self._device

    @property
    def ctrl_params(self):
        return self._ctrl_params

    @property
    def _device_name(self):
        from bliss.common.motor_group import is_motor_group
        from bliss.common.axis.axis import Axis

        if self.device is None:
            return None
        if is_motor_group(self.device) or isinstance(self.device, Axis):
            return "axis"
        return self.device.name

    @property
    def name(self):
        if self.__name:
            return self.__name
        else:
            return self._device_name

    @property
    def channels(self):
        return self.__channels

    @property
    def npoints(self):
        return self.__npoints

    def _do_add_counter(self, counter):
        if isinstance(counter, CounterAlias):
            controller_fullname, _, _ = counter.fullname.rpartition(":")
            chan_name = f"{controller_fullname}:{counter.name}"
        else:
            chan_name = counter.fullname

        try:
            unit = counter.unit
        except AttributeError:
            unit = None

        self.channels.append(
            AcquisitionChannel(chan_name, counter.data_dtype, counter.shape, unit=unit)
        )
        self._counters[counter].append(self.channels[-1])

    def add_counter(self, counter):
        if counter in self._counters:
            return

        if counter._counter_controller == self.device:
            self._do_add_counter(counter)
        else:
            raise RuntimeError(
                f"Cannot add counter {counter.name}: acquisition controller"
                f" mismatch {counter._counter_controller} != {self.device}"
            )

    def get_iterator(self):
        try:
            iter(self)
        except (NotImplementedError, TypeError):
            return AcquisitionObjectIteratorObsolete(self)
        else:
            return AcquisitionObjectIterator(self)

    # --------------------------- ACQ. CHAIN METHODS ------------------------------------------

    def spawn_reading_task(self, rawlink_event=None):
        if not self._reading_task:
            self._reading_task = gevent.spawn(self.reading)
            self._reading_task.name = f"{self.name}_reading_task"
            if rawlink_event:
                self._reading_task.rawlink(lambda _: rawlink_event.set())
            self._reading_task.link_exception(self._on_reading_exception)

    def _on_reading_exception(self, task):
        """Called when the _reading_task dies on a error.
        It tries to pass the error to the scan interruption system.
        """
        if self.acq_chain_iter is not None:
            try:
                etype, err, tb = task.exc_info
                error = err.with_traceback(tb)
                scan = self.acq_chain_iter.acquisition_chain.scan
            except Exception:
                pass
            else:
                scan.stop_scan_on_error(error)

    def wait_reading(self):
        if self._reading_task is not None:
            self._reading_task.get()

    def acq_wait_reading(self):
        """Wait until reading task has finished"""
        with scan_debug.chain_debug("acq_wait_reading", self):
            self.wait_reading()

    def acq_wait_ready(self):
        """Wait until ready for next acquisition"""
        with join_tasks(f"{self.name} (acq_wait_ready)") as tasks:
            tasks.append(gevent.spawn(self.wait_reading))
            tasks.append(gevent.spawn(self.wait_ready))
            # The acquistion object is also considered to be
            # ready when the reading task (if any) is not running.
            # Hence join with count=1 and raise_error=True.
            gevent.joinall(tasks, count=1, raise_error=True)
            try:
                return tasks[-1].get()
            finally:
                gevent.killall(tasks, exception=StopTask)

    def emit_progress_signal(self, data, description=None):
        try:
            prefix = self.device.name
        except AttributeError:
            prefix = self.name

        for k, v in data.items():
            fullname = f"{prefix}:{k}"

            _description = {"reference": False}
            if description is not None:
                if description.get(k) is not None:
                    _description.update(description.get(k))

            payload = {"name": fullname, "description": _description, "data": v}
            dispatcher.send("scan_progress", self, payload)

    # --------------------- NOT IMPLEMENTED METHODS  ------------------------------------

    @staticmethod
    def get_param_validation_schema():
        """returns a schema dict for validation"""
        raise NotImplementedError

    def __iter__(self):
        """Needs to yield AcquisitionObject instances when implemented"""
        raise NotImplementedError

    def acq_prepare(self):
        raise NotImplementedError

    def acq_start(self):
        raise NotImplementedError

    def acq_stop(self):
        raise NotImplementedError

    def acq_trigger(self):
        raise NotImplementedError

    def prepare(self):
        raise NotImplementedError

    def start(self):
        raise NotImplementedError

    def stop(self):
        raise NotImplementedError

    def trigger(self):
        raise NotImplementedError

    # --------------------- CUSTOMIZABLE METHODS  ----------------------------------------

    def apply_parameters(self):
        """Load controller parameters into hardware controller at the beginning of each scan"""
        from bliss.controllers.counter import CounterController

        if isinstance(self.device, CounterController):
            self.device.apply_parameters(self._ctrl_params)

    META_TIMING = META_TIMING

    def get_acquisition_metadata(self, timing=None):
        """
        In this method, acquisition device should collect time-dependent
        any meta data related to this device.
        """
        if timing == META_TIMING.PREPARED:
            device = self.device
            if isinstance(device, HasMetadataForScan):
                return device.scan_metadata()
        return None

    def wait_ready(self):
        """implement this method to wait until being ready for next scan iteration"""
        pass

    def reading(self):
        pass

    def trigger_ready(self):
        return True

    def set_device_saving(self, directory, prefix, force_no_saving=False):
        pass


class AcquisitionMaster(AcquisitionObject):

    HARDWARE, SOFTWARE = TRIGGER_MODE_ENUM.HARDWARE, TRIGGER_MODE_ENUM.SOFTWARE

    def __init__(
        self,
        *devices,
        name=None,
        npoints=1,
        trigger_type=TRIGGER_MODE_ENUM.SOFTWARE,
        prepare_once=False,
        start_once=False,
        ctrl_params=None,
    ):

        super().__init__(
            *devices,
            name=name,
            npoints=npoints,
            trigger_type=trigger_type,
            prepare_once=prepare_once,
            start_once=start_once,
            ctrl_params=ctrl_params,
        )

        self.__slaves = list()
        self.__triggers = list()
        self.__duplicated_channels = {}
        self.__prepared = False
        self.__terminator = True

    @property
    def slaves(self):
        return self.__slaves

    @property
    def terminator(self):
        """bool: flag to specify if the whole scan should terminate when the acquisition under control of the master is done.

        Only taken into account if the acquisition master is a top master in the acquisition chain.
        Defaults to True: any top master ends a scan when done.
        """
        return self.__terminator

    @terminator.setter
    def terminator(self, terminator):
        self.__terminator = bool(terminator)

    def acq_prepare(self):
        if not self.__prepared:

            for connect, _ in self.__duplicated_channels.values():
                connect()
            self.__prepared = True

        return self.prepare()

    def acq_start(self):
        dispatcher.send("start", self)
        return_value = self.start()
        return return_value

    def acq_stop(self):
        if self.__prepared:
            for _, cleanup in self.__duplicated_channels.values():
                cleanup()
            self.__prepared = False
        return self.stop()

    def acq_trigger(self):
        with scan_debug.chain_debug("acq_trigger", self):
            return self.trigger()

    def trigger_slaves(self):
        invalid_slaves = list()
        for slave, task in self.__triggers:
            if not slave.trigger_ready() or not task.successful():
                invalid_slaves.append(slave)
                if task.ready():
                    task.get()  # raise task exception, if any
                # otherwise, kill the task with RuntimeError
                task.kill(
                    RuntimeError(
                        "%s: Previous trigger is not done, aborting" % self.name
                    )
                )

        self.__triggers = []

        if invalid_slaves:
            raise RuntimeError(
                "%s: Aborted due to bad triggering on slaves: %s"
                % (self.name, invalid_slaves)
            )
        else:
            for slave in self.slaves:
                if slave.trigger_type == TRIGGER_MODE_ENUM.SOFTWARE:
                    self.__triggers.append((slave, gevent.spawn(slave.acq_trigger)))

    def wait_slaves(self):
        with join_tasks(f"{self.name} (wait_slaves)") as slave_tasks:
            slave_tasks.extend(task for _, task in self.__triggers)

    def add_external_channel(
        self, device, name, rename=None, conversion=None, dtype=None
    ):
        """Add a channel from an external source."""
        try:
            source = next(
                channel for channel in device.channels if channel.short_name == name
            )
        except StopIteration:
            raise ValueError(
                "The device {} does not have a channel called {}".format(device, name)
            )
        new_channel, connect, cleanup = duplicate_channel(
            source, name=rename, conversion=conversion, dtype=dtype
        )
        self.__duplicated_channels[new_channel] = connect, cleanup
        self.channels.append(new_channel)

    def attach_channels(self, master, to_channel_name):
        """Attaching all channels from a topper master means that this master
        data channels will be captured and re-emit when the
        **to_channel_name** will emit its data.
        in a case of this kind of chain i.e a mesh:
        m0 (channel: pos_m0)
        └── m1 (channel: pos_m1)
            └── timer (channel: elapsed_time)
        pos_m0 will be emit when pos_m1 will be emit => same amount of values

        Note: this can only work if topper master emit data one by one and before
        this master
        """
        # check if master is a topper master
        parent = self.parent
        while parent is not None and parent != master:
            parent = parent.parent
        if parent is None:  # master is not a parent
            raise RuntimeError(
                "Could only work with a master device (%s) is not a master of (%s)"
                % (master.name, self.name)
            )

        try:
            to_channel = next(
                channel for channel in self.channels if channel.name == to_channel_name
            )
        except StopIteration:
            raise ValueError(
                f"The device {master} does not have a channel called {to_channel_name}"
            )

        attach_channels(master.channels, to_channel)

    def wait_slaves_prepare(self):
        """
        This method will wait the end of the **prepare**
        one slaves.
        """
        with join_tasks(f"{self.name} (wait_slaves_prepare)") as all_tasks:
            for dev in self.slaves:
                task = _running_task_on_device.get(dev)
                if task:
                    all_tasks.append(task)

    def wait_slaves_ready(self):
        """
        This method will wait that all slaves are **ready** to take an other trigger
        """
        for slave in self.slaves:
            if isinstance(slave, AcquisitionMaster):
                slave.wait_slaves_ready()
        with join_tasks(f"{self.name} (wait_slaves_ready)") as tasks:
            for dev in self.slaves:
                tasks.append(gevent.spawn(dev.wait_ready))

    def stop_all_slaves(self):
        """
        This method will stop all slaves depending of this master
        """
        for slave in self.slaves:
            if isinstance(slave, AcquisitionMaster):
                slave.stop_all_slaves()
        with join_tasks(f"{self.name} (stop_all_slaves)") as tasks:
            for dev in self.slaves:
                tasks.append(gevent.spawn(dev.stop))


class AcquisitionSlave(AcquisitionObject):
    HARDWARE, SOFTWARE = TRIGGER_MODE_ENUM.HARDWARE, TRIGGER_MODE_ENUM.SOFTWARE

    def __init__(
        self,
        *devices,
        name=None,
        npoints=1,
        trigger_type=TRIGGER_MODE_ENUM.SOFTWARE,
        prepare_once=False,
        start_once=False,
        ctrl_params=None,
    ):

        super().__init__(
            *devices,
            name=name,
            npoints=npoints,
            trigger_type=trigger_type,
            prepare_once=prepare_once,
            start_once=start_once,
            ctrl_params=ctrl_params,
        )

    def acq_prepare(self):
        if self._reading_task:
            raise RuntimeError("%s: Last reading task is not finished." % self.name)
        return self.prepare()

    def acq_start(self):
        dispatcher.send("start", self)
        self.start()
        self.spawn_reading_task()

    def acq_stop(self):
        self.stop()

    def acq_trigger(self):
        with scan_debug.chain_debug("acq_trigger", self):
            self.spawn_reading_task()
            self.trigger()


class AcquisitionChainIter:
    def __init__(
        self, acquisition_chain, sub_tree, presets_list, parallel_prepare=True
    ):
        self.__expanded_tree = None
        self.__tree_node_cache = weakref.WeakKeyDictionary()
        self.__node_depth_cache = weakref.WeakKeyDictionary()
        self.__sequence_index = -1
        self._parallel_prepare = parallel_prepare
        self.__acquisition_chain_ref = weakref.ref(acquisition_chain)
        self._preset_iterators_list = list()
        self._current_preset_iterators_list = list()
        self._presets_list = presets_list

        # create iterators tree
        self._tree = Tree()
        self._root_node = self._tree.create_node("acquisition chain", "root")
        acqobj2iter = dict()
        for acq_obj in sub_tree.expand_tree():
            if not isinstance(acq_obj, AcquisitionObject):
                continue
            node = acquisition_chain._tree.get_node(acq_obj)
            parent_acq_obj_iter = acqobj2iter.get(node.bpointer, "root")
            acqobj2iter[acq_obj] = acq_obj_iter = acq_obj.get_iterator()
            acq_obj.acq_chain_iter = self  # hold as weakref
            self._tree.create_node(
                tag=acq_obj.name, identifier=acq_obj_iter, parent=parent_acq_obj_iter
            )

    @property
    def acquisition_chain(self):
        return self.__acquisition_chain_ref()

    @property
    def top_master(self):
        return self._tree.children("root")[0].identifier.acquisition_object

    @property
    def sequence_index(self):
        return self.__sequence_index

    def apply_parameters(self):
        with join_tasks("chain iterator (apply_parameters)") as all_tasks:
            for this_level_tasks in self._iter_acq_obj_tasks(
                all_tasks, "apply_parameters", by_level=False
            ):
                gevent.joinall(this_level_tasks, raise_error=True)

    def prepare(self, scan, scan_info):
        with join_tasks("chain iterator (prepare: presets)") as preset_tasks:
            if self.__sequence_index == 0:
                for preset in self._presets_list:
                    task = gevent.spawn(preset.prepare, self.acquisition_chain)
                    task.name = "acq-chain-prepare"
                    task.spawn_tree_locals[
                        id(task), "textblock_context_greenlet"
                    ] = True
                    preset_tasks.append(task)

                self._preset_iterators_list = list()
                for preset in self._presets_list:
                    iterator = preset.get_iterator(self.acquisition_chain)
                    if isinstance(iterator, collections.abc.Iterable):
                        self._preset_iterators_list.append(iterator)

            self._current_preset_iterators_list = list()
            for iterator in list(self._preset_iterators_list):
                try:
                    preset = next(iterator)
                    assert isinstance(preset, ChainIterationPreset)
                except StopIteration:
                    self._preset_iterators_list.remove(iterator)
                else:
                    self._current_preset_iterators_list.append(preset)
                    task = gevent.spawn(preset.prepare)
                    task.name = "acq-chain-prepare"
                    task.spawn_tree_locals[
                        id(task), "textblock_context_greenlet"
                    ] = True
                    preset_tasks.append(task)

        with join_tasks("chain iterator (prepare: acq_prepare)") as all_tasks:
            for this_level_tasks in self._iter_acq_obj_tasks(
                all_tasks, "acq_prepare", by_level=not self._parallel_prepare
            ):
                gevent.joinall(this_level_tasks, raise_error=True)

    def start(self):
        with join_tasks("chain iterator (start: presets)") as preset_tasks:
            if self.__sequence_index == 0:
                for preset in self._presets_list:
                    task = gevent.spawn(preset.start, self.acquisition_chain)
                    task.name = "acq-chain-start"
                    task.spawn_tree_locals[
                        id(task), "textblock_context_greenlet"
                    ] = True
                    preset_tasks.append(task)
            for i in self._current_preset_iterators_list:
                task = gevent.spawn(i.start)
                task.name = "acq-chain-start"
                task.spawn_tree_locals[id(task), "textblock_context_greenlet"] = True
                preset_tasks.append(task)
        with join_tasks("chain iterator (start: acq_start)") as all_tasks:
            for this_level_tasks in self._iter_acq_obj_tasks(all_tasks, "acq_start"):
                gevent.joinall(this_level_tasks, raise_error=True)

    def _acquisition_object_iterators(self):
        for acq_obj_iter in self._tree.expand_tree():
            if not isinstance(acq_obj_iter, AbstractAcquisitionObjectIterator):
                continue
            yield acq_obj_iter

    def wait_all_devices(self):
        for acq_obj_iter in self._acquisition_object_iterators():
            acq_obj_iter.acq_wait_reading()
            if isinstance(acq_obj_iter.acquisition_object, AcquisitionMaster):
                acq_obj_iter.wait_slaves()
            dispatcher.send("end", acq_obj_iter.acquisition_object)

    def stop(self):
        with capture_exceptions(raise_index=0) as capture:
            with capture():
                with join_tasks(
                    "chain iterator (stop: presets before_stop)", raise_on_first=False
                ) as preset_tasks:
                    for preset in self._presets_list:
                        task = gevent.spawn(preset.before_stop, self.acquisition_chain)
                        task.name = "acq-chain-stop"
                        task.spawn_tree_locals[
                            id(task), "textblock_context_greenlet"
                        ] = True
                        preset_tasks.append(task)

            with capture():
                with join_tasks("chain iterator (stop: acq_stop)") as all_tasks:
                    for this_level_tasks in self._iter_acq_obj_tasks(
                        all_tasks, "acq_stop", master_to_slave=True
                    ):
                        with KillMask(masked_kill_nb=1):
                            gevent.joinall(this_level_tasks)

            with capture():
                # Ensure that all reading tasks stopped: this is bad design
                reading_tasks = list()
                try:
                    for acq_objs in self._iter_acq_objs(master_to_slave=True):
                        for acq_obj in acq_objs:
                            _reading_task = getattr(acq_obj, "_reading_task", None)
                            if _reading_task:
                                # Reading task exists and is still running
                                reading_tasks.append(_reading_task)
                finally:
                    with KillMask(masked_kill_nb=1):
                        gevent.killall(reading_tasks)

            with capture():
                self.wait_all_devices()

            with capture():
                with join_tasks(
                    "chain iterator (stop: presets stop)", raise_on_first=False
                ) as preset_tasks:
                    for preset in self._presets_list:
                        task = gevent.spawn(preset.stop, self.acquisition_chain)
                        task.name = "acq-chain-stop"
                        task.spawn_tree_locals[
                            id(task), "textblock_context_greenlet"
                        ] = True
                        preset_tasks.append(task)
                    for i in self._current_preset_iterators_list:
                        task = gevent.spawn(i.stop)
                        task.name = "acq-chain-stop"
                        task.spawn_tree_locals[
                            id(task), "textblock_context_greenlet"
                        ] = True
                        preset_tasks.append(task)

    def __next__(self):
        self.__sequence_index += 1
        dispatcher.send(
            scan_debug.SIGNAL_NEXT_ITER, self.acquisition_chain, self.sequence_index
        )
        with join_tasks(
            f"chain iterator (next: acq_wait_ready, sequence index = {self.__sequence_index})"
        ) as all_tasks:

            for this_level_tasks in self._iter_acq_obj_tasks(
                all_tasks, "acq_wait_ready", master_to_slave=True
            ):

                gevent.joinall(this_level_tasks, raise_error=True)

        try:
            if self.__sequence_index:
                for acq_obj_iter in self._acquisition_object_iterators():
                    next(acq_obj_iter)
            with join_tasks(
                f"chain iterator (next: stop, sequence index = {self.__sequence_index})",
                raise_on_first=False,
            ) as preset_tasks:
                for i in self._current_preset_iterators_list:
                    task = gevent.spawn(i.stop)
                    task.name = "acq-chain-next"
                    task.spawn_tree_locals[
                        id(task), "textblock_context_greenlet"
                    ] = True
                    preset_tasks.append(task)
        except StopIteration:  # should we stop all devices?
            self.wait_all_devices()
            raise
        return self

    @property
    def _expanded_tree(self):
        """Return expanded tree as a list

        It has to be a list, an iterator won't work once it is exhausted

        This can be called many times, and is quite costly => can result in wasting CPU time,
        so it is important to cache it
        """
        if self.__expanded_tree is None:
            self.__expanded_tree = list(self._tree.expand_tree(mode=Tree.WIDTH))[1:]
        return self.__expanded_tree

    def _get_tree_node(self, acq_obj_iter):
        """Return tree node corresponding to acq_obj_iter

        This can be called many times, and is quite costly => can result in wasting CPU time,
        so it is important to cache it
        """
        try:
            return self.__tree_node_cache[acq_obj_iter]
        except KeyError:
            return self.__tree_node_cache.setdefault(
                acq_obj_iter, self._tree.get_node(acq_obj_iter)
            )

    def _get_node_depth(self, node):
        """Return node depth

        This can be called many times, and is quite costly => can result in wasting CPU time,
        so it is important to cache it
        """
        try:
            return self.__node_depth_cache[node]
        except KeyError:
            return self.__node_depth_cache.setdefault(node, self._tree.depth(node))

    def _iter_acq_obj_tasks(
        self,
        tasks: list[gevent.Greenlet],
        func_name: str,
        master_to_slave: bool = False,
        by_level: bool = True,
    ) -> Generator[list[gevent.Greenlet], None, None]:
        """This generator spawns and yields the `func_name` as greenlets belonging to each acquisition chain level when `by_level=True`,
        else it yields all greenlets at once.

        When yielding by level, it can yield the lowest level first (`master_to_slave=False`) or the highest level first (`master_to_slave=True`).

        All greenlets spawned while iterating will be added to `tasks` for cleanup.
        """

        def call_with_debug(func_name, acq_obj_iter, *args, **kwargs):
            with scan_debug.chain_debug(func_name, acq_obj_iter.acquisition_object):
                return getattr(acq_obj_iter, func_name)(*args, **kwargs)

        for acq_obj_iterators in self._iter_acq_obj_iterators(
            master_to_slave=master_to_slave, by_level=by_level
        ):
            this_level_tasks = list()
            for acq_obj_iter in acq_obj_iterators:
                task = gevent.spawn(call_with_debug, func_name, acq_obj_iter)
                task.name = (
                    f"acq_obj_task_{func_name}_{acq_obj_iter.acquisition_object.name}"
                )
                tasks.append(task)
                this_level_tasks.append(task)
                _running_task_on_device[acq_obj_iter.acquisition_object] = task
            yield this_level_tasks

    def _iter_acq_objs(
        self, master_to_slave: bool = False, by_level: bool = True
    ) -> Generator[list[AcquisitionObject], None, None]:
        """This generator yields the acquisition objects belonging to each acquisition chain level when `by_level=True`,
        else it yields all acquisition objects at once.

        When yielding by level, it can yield the lowest level first (`master_to_slave=False`) or the highest level first (`master_to_slave=True`).
        """
        for acq_obj_iterators in self._iter_acq_obj_iterators(
            master_to_slave=master_to_slave, by_level=by_level
        ):
            yield [
                acq_obj_iter.acquisition_object for acq_obj_iter in acq_obj_iterators
            ]

    def _iter_acq_obj_iterators(
        self, master_to_slave: bool = False, by_level: bool = True
    ) -> Generator[list[Iterator[AcquisitionObject]], None, None]:
        """This generator yields the acquisition object iterators belonging to each acquisition chain level when `by_level=True`,
        else it yields all acquisition object iterators at once.

        When yielding by level, it can yield the lowest level first (`master_to_slave=False`) or the highest level first (`master_to_slave=True`).
        """
        acq_obj_iterators = list()
        prev_level = None
        if master_to_slave:
            acq_obj_iters = self._expanded_tree
        else:
            acq_obj_iters = reversed(self._expanded_tree)

        for acq_obj_iter in acq_obj_iters:
            node = self._get_tree_node(acq_obj_iter)
            level = self._get_node_depth(node)
            if by_level and prev_level != level:
                yield acq_obj_iterators
                acq_obj_iterators.clear()
                prev_level = level

            acq_obj_iterators.append(acq_obj_iter)

        yield acq_obj_iterators

    def __iter__(self):
        return self


class AcquisitionChain:
    def __init__(self, parallel_prepare=False):
        self._tree = Tree()
        self._root_node = self._tree.create_node("acquisition chain", "root")
        self._presets_master_list = weakref.WeakKeyDictionary()
        self._parallel_prepare = parallel_prepare
        self.__iterators = []
        self._scan = None

    @property
    def scan(self):
        return self._scan

    @scan.setter
    def scan(self, scan):
        self._scan = weakref.proxy(scan)

    @property
    def tree(self) -> Tree:
        """Return the acquisition chain tree"""
        return self._tree

    @property
    def top_masters(self):
        return [x.identifier for x in self._tree.children("root")]

    @property
    def nodes_list(self):
        nodes_gen = self._tree.expand_tree()
        next(nodes_gen)  # first node is 'root'
        return list(nodes_gen)

    @property
    def iterators(self):
        return self.__iterators

    def get_node_from_devices(self, *devices):
        """
        Helper method to get AcquisitionObject
        from countroller and/or counter, motor.
        This will return a list of nodes in the same order
        as the devices. Node will be None if not found.
        """
        from bliss.common.motor_group import _Group

        looking_device = {d: None for d in devices}
        nb_device = len(devices)
        for node in self.nodes_list:
            if isinstance(node.device, _Group):
                for axis in node.device.axes.values():
                    if axis in looking_device:
                        looking_device[axis] = node
                        nb_device -= 1
                if not nb_device:
                    break
            if node.device in looking_device:
                looking_device[node.device] = node
                nb_device -= 1
                if not nb_device:
                    break
            else:
                for cnt in node._counters:
                    if cnt in looking_device:
                        looking_device[cnt] = node
                        nb_device -= 1
                if not nb_device:
                    break
        return looking_device.values()

    def add(self, master, slave=None):

        # --- handle ChainNodes --------------------------------------
        if isinstance(master, ChainNode):
            master.create_acquisition_object(force=False)
            if slave is None:
                self.add(master.acquisition_obj)
                for node in master.children:
                    node.create_acquisition_object(force=False)
                    self.add(master.acquisition_obj, node.acquisition_obj)
                return
            master = master.acquisition_obj

        if isinstance(slave, ChainNode):
            slave.create_acquisition_object(force=False)
            self.add(master, slave.acquisition_obj)

            for node in slave.children:
                node.create_acquisition_object(force=False)
                self.add(slave.acquisition_obj, node.acquisition_obj)

            return

        # print(f"===== ADD SLAVE {slave}({slave.name}) in MASTER {master} ({master.name})")
        if not isinstance(master, AcquisitionMaster):
            raise TypeError(f"object {master} is not an AcquisitionMaster")

        slave_node = self._tree.get_node(slave)
        master_node = self._tree.get_node(master)

        # --- if slave already exist in chain and new slave is an AcquisitionSlave
        if slave_node is not None and isinstance(slave, AcquisitionSlave):

            # --- if {new master is not the master of the current_slave} and {current_master of current_slave is not root}
            # --- => try to put the same slave under a different master => raise error !
            if (
                self._tree.get_node(slave_node.bpointer) is not self._root_node
                and master is not slave_node.bpointer
            ):
                raise RuntimeError(
                    "Cannot add acquisition device %s to multiple masters, current master is %s"
                    % (slave, slave_node._bpointer)
                )
            else:  # --- if {new master is the master of the current_slave} => same allocation => ignore ok
                # --- if {new master is not the master of the current_slave}
                # ---   and {current_master of current_slave is root} => try to re-allocate a top-level AcqDevice under a new master
                # ---     => it should never append because an AcqDev is never given as a master.

                # user error, multiple add, ignore for now
                return

        # --- if slave already exist in chain and new slave is not an AcquisitionSlave => existing AcqMaster slave under new or existing master
        # --- if slave not already in chain   and new slave is not an AcquisitionSlave => new      AcqMaster slave under new or existing master
        # --- if slave not already in chain   and new slave is     an AcquisitionSlave => new      AcqDevice slave under new or existing master

        if master_node is None:  # --- if new master not in chain
            for node in self.nodes_list:
                if (
                    node.name == master.name
                ):  # --- forribde new master with a name already in use
                    raise RuntimeError(
                        f"Cannot add acquisition master with name '{node.name}`: duplicated name"
                    )

            # --- create a new master node
            master_node = self._tree.create_node(
                tag=master.name, identifier=master, parent="root"
            )
        if slave is not None:
            if slave_node is None:  # --- create a new slave node
                slave_node = self._tree.create_node(
                    tag=slave.name, identifier=slave, parent=master
                )
            else:  # --- move an existing AcqMaster under a different master
                self._tree.move_node(slave, master)

            slave.parent = master

    def add_preset(self, preset, master=None):
        """
        Add a preset on a top-master.

        Args:
            preset: a ChainPreset object
            master: if None, take the first top-master of the chain
        """
        if not isinstance(preset, ChainPreset):
            raise ValueError("Expected ChainPreset instance")
        top_masters = self.top_masters
        if master is not None and master not in top_masters:
            raise ValueError(f"master {master} not in {top_masters}")

        # set the preset on the chain itself if master is None
        # this is to manage the case where the chain tree is still empty.
        presets_list = self._presets_master_list.setdefault(master or self, list())
        presets_list.append(preset)

    def get_iter_list(self):
        if len(self._tree) > 1:
            # set all slaves into master
            for master in (
                x for x in self._tree.expand_tree() if isinstance(x, AcquisitionMaster)
            ):
                del master.slaves[:]
                master.slaves.extend(self._tree.get_node(master).fpointer)

            top_masters = self.top_masters
            sub_trees = [self._tree.subtree(x) for x in top_masters]

            first_top_master = top_masters.pop(0)
            first_tree = sub_trees.pop(0)
            # default => first top master is also store in self
            presets_list = self._presets_master_list.get(self, list())
            presets_list += self._presets_master_list.get(first_top_master, list())
            iterators = [
                AcquisitionChainIter(
                    self,
                    first_tree,
                    presets_list,
                    parallel_prepare=self._parallel_prepare,
                )
            ]
            iterators.extend(
                [
                    AcquisitionChainIter(
                        self,
                        sub_tree,
                        self._presets_master_list.get(master, list()),
                        parallel_prepare=self._parallel_prepare,
                    )
                    for master, sub_tree in zip(top_masters, sub_trees)
                ]
            )

            self.__iterators = iterators

            return iterators
        else:
            return []

    def append(self, chain, add_presets=False):
        """Append another chain"""
        for master in (
            x for x in chain._tree.expand_tree() if isinstance(x, AcquisitionMaster)
        ):
            for slave in chain._tree.get_node(master).fpointer:
                self.add(master, slave)
        self._tree.show()
        if add_presets:
            for preset in chain._presets_list:
                self.add_preset(preset)


class ChainNode:
    def __init__(self, controller):
        self._controller = controller

        self._counters = []
        self._child_nodes = []

        self._is_master = False
        self._is_top_level = True
        self._acquisition_obj = None

        self._acq_obj_params = None
        self._ctrl_params = None
        self._parent_acq_params = None

        self._calc_dep_nodes = {}  # to store CalcCounterController dependent nodes

    @property
    def controller(self):
        return self._controller

    @property
    def is_master(self):
        return self._is_master

    @property
    def is_top_level(self):
        return self._is_top_level

    @property
    def children(self):
        return self._child_nodes

    @property
    def counters(self):
        return self._counters

    @property
    def acquisition_obj(self):
        return self._acquisition_obj

    @property
    def acquisition_parameters(self):
        return self._acq_obj_params

    @property
    def controller_parameters(self):
        return self._ctrl_params

    def set_parent_parameters(self, parent_acq_params, force=False):
        if parent_acq_params is not None:
            if (
                self._parent_acq_params is not None
                and self._parent_acq_params != parent_acq_params
            ):
                print(
                    f"=== ChainNode WARNING: try to set PARENT_ACQ_PARAMS again: \n"
                    f"Current {self._parent_acq_params} \n New     {parent_acq_params} "
                )

            if force or self._parent_acq_params is None:
                self._parent_acq_params = parent_acq_params

    def set_parameters(self, acq_params=None, ctrl_params=None, force=False):
        """
        Store the scan and/or acquisition parameters into the node.
        These parameters will be used when the acquisition object
        is instantiated (see self.create_acquisition_object )
        If the parameters have been set already, new parameters will
        be ignored (except if Force==True).
        """

        if acq_params is not None:
            if self._acq_obj_params is not None and self._acq_obj_params != acq_params:
                print(
                    f"=== ChainNode WARNING: try to set ACQ_PARAMS again: \n"
                    f"Current {self._acq_obj_params} \n New     {acq_params} "
                )

            if force or self._acq_obj_params is None:
                self._acq_obj_params = acq_params

        if ctrl_params is not None:
            if self._ctrl_params is not None and self._ctrl_params != ctrl_params:
                print(
                    f"=== ChainNode WARNING: try to set CTRL_PARAMS again: \n"
                    f"Current {self._ctrl_params} \n New     {ctrl_params} "
                )

            if force or self._ctrl_params is None:
                self._ctrl_params = ctrl_params

            # --- transform scan specific ctrl_params into full set of ctrl_param
            self._ctrl_params = update_ctrl_params(self.controller, self._ctrl_params)

    def add_child(self, chain_node):
        if chain_node not in self._child_nodes:
            self._child_nodes.append(chain_node)
            self._is_master = True
            chain_node._is_top_level = False

    def add_counter(self, counter):
        self._counters.append(counter)

    def _get_default_chain_parameters(self, scan_params, acq_params):
        """
        Obtain the full acquisition parameters set from scan_params
        in the context of the default chain
        """

        return self.controller.get_default_chain_parameters(scan_params, acq_params)

    def get_acquisition_object(self, acq_params, ctrl_params, parent_acq_params):
        """
        Return the acquisition object associated to this node
        acq_params, ctrl_params and parent_acq_params have to be
        dicts (None not supported)
        """

        return self.controller.get_acquisition_object(
            acq_params, ctrl_params=ctrl_params, parent_acq_params=parent_acq_params
        )

    def create_acquisition_object(self, force=False):
        """
        Create the acquisition object using the current
        parameters (stored in 'self._acq_obj_params').
        Create the children acquisition objects if any are attached to this node.
        - 'force' (bool): if False, it won't instanciate the acquisition
           object if it already exists, else it will overwrite it.
        """

        # --- Return acquisition object if it already exist and Force is False ----------------
        if not force and self._acquisition_obj is not None:
            return self._acquisition_obj

        # --- Prepare parameters --------------------------------------------------------------
        if self._acq_obj_params is None:
            acq_params = {}
        else:
            acq_params = (
                self._acq_obj_params.copy()
            )  # <= IMPORTANT: pass a copy because the acq obj may pop on that dict!

        if self._ctrl_params is None:
            ctrl_params = update_ctrl_params(self.controller, {})
        else:
            ctrl_params = self._ctrl_params

        if self._parent_acq_params is None:
            parent_acq_params = {}
        else:
            parent_acq_params = (
                self._parent_acq_params.copy()
            )  # <= IMPORTANT: pass a copy because the acq obj may pop on that dict!

        # --- Create the acquisition object ---------------------------------------------------
        acq_obj = self.get_acquisition_object(
            acq_params, ctrl_params=ctrl_params, parent_acq_params=parent_acq_params
        )

        if not isinstance(acq_obj, AcquisitionObject):
            raise TypeError(f"Object: {acq_obj} is not an AcquisitionObject")
        else:
            self._acquisition_obj = acq_obj

        # --- Add the counters to the acquisition object ---------------
        for counter in self._counters:
            self._acquisition_obj.add_counter(counter)

        # --- Deal with children acquisition objects ------------------
        self.create_children_acq_obj(force)

        return self._acquisition_obj

    def create_children_acq_obj(self, force=False):
        for node in self.children:

            if node._acq_obj_params is None:
                node.set_parent_parameters(self._acq_obj_params)

            node.create_acquisition_object(force)

    def get_repr_str(self):
        if self._acquisition_obj is None:
            txt = f"|__ !* {self._controller.name} *! "
        else:
            txt = f"|__ {self._acquisition_obj.__class__.__name__}( {self._controller.name} ) "

        if len(self._counters) > 0:
            txt += "("
            for cnt in self._counters:
                txt += f" {cnt.name},"
            txt = txt[:-1]
            txt += " ) "

        txt += "|"

        return txt
