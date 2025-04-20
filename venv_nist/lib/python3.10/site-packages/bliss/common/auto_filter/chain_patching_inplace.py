"""Acquisition chain that allows retrying points
"""

from collections.abc import Callable
from louie import dispatcher
import logging
import gevent
import numpy
from gevent.event import Event
from gevent.queue import Queue

from bliss.scanning.chain import AcquisitionMaster
from bliss.scanning.chain import AcquisitionObject
from bliss.scanning.chain import AcquisitionSlave
from bliss.scanning.chain import AcquisitionChain
from bliss.scanning.chain import duplicate_channel
from bliss.scanning.channel import AcquisitionChannel
from bliss.scanning.acquisition.motor import VariableStepTriggerMaster
from bliss.scanning.acquisition.timer import SoftwareTimerMaster
from bliss.scanning.acquisition.lima import LimaAcquisitionMaster
from bliss.scanning.acquisition.mca import McaAcquisitionSlave
from bliss.common.auto_filter.filterset import FilterSet
from bliss.common.auto_filter.base_controller import AutoFilter

logger = logging.getLogger(__name__)


class LimitEvent:
    """Like `Event` but needs to be set N times before the
    event is considered to be "set".
    """

    def __init__(self, nb_set=0):
        self.__nb_set = 0
        self.__nb_limit = nb_set
        self.__changed = Event()

    def __repr__(self):
        return f"{self.__class__.__name__}({self.__nb_set}/{self.__nb_limit})"

    def __iadd__(self, increment):
        self.__nb_limit += increment
        return self

    def set(self):
        self.__nb_set += 1
        self.__changed.set()

    def clear(self):
        self.__nb_set = 0
        self.__changed.clear()

    def wait(self, timeout=None):
        while not self.is_set():
            self.__changed.clear()
            if not self.__changed.wait(timeout=timeout):
                return False
        return True

    def is_set(self):
        return self.__nb_set >= self.__nb_limit


class AutoFilterChannelPatch:
    """Patch the `emit` method of an acquistion channel to intercept calls until
    a scan point is validated. The validation channel of the acquisition chain checks
    whether a point is valid or not (see AutoFilterValidationChannelPatch) and
    calls `set_data_is_valid` of this channel.
    """

    def __init__(
        self,
        acq_channel: AcquisitionChannel,
        beam_attenuation_correction=None,
        corr_suffix="_corr",
    ):
        self.__point_buffer = dict()
        self._point_count = 0

        self.__beam_attenuation_correction = beam_attenuation_correction
        self.__corr_channel = None
        if beam_attenuation_correction is not None:
            name = f"{acq_channel.name}{corr_suffix}"
            self.__corr_channel, _, _ = duplicate_channel(acq_channel, name=name)

        self.__acq_channel = acq_channel
        self.__original_emit = acq_channel.emit
        acq_channel.emit = self._patched_emit
        acq_channel.set_data_is_valid = self.__set_data_is_valid

    @property
    def _acq_channel(self):
        return self.__acq_channel

    def debug(self, msg, sformat, *args):
        name = self.__acq_channel.name
        sformat = f"{msg} {name} {sformat}"
        logger.debug(sformat, *args)

    def __set_data_is_valid(
        self,
        point_nb: int,
        point_is_valid: bool,
        point_data_processed: LimitEvent,
        save_first_attempt: bool,
    ):
        """Called from AutoFilterValidationChannelPatch"""
        self.debug("GET VALID", "point_nb %d %s", point_nb, point_is_valid)
        point_data_processed += 1
        self.__set_buffer(point_nb, "point_data_processed", point_data_processed)
        self.__set_buffer(point_nb, "point_is_valid", point_is_valid)
        self.__set_buffer(point_nb, "save_first_attempt", save_first_attempt)
        self.__emit_data(point_nb)

    def _patched_emit(self, data):
        """Either this is called before or after set_data_is_valid.
        In any case, point data is only emitted when both have been
        called for that point.
        """
        # We need a copy of the data to ensure it is not cleared
        # by the caller. We also need to ensure the data type
        # matches the type expected by the channel.
        if not isinstance(data, dict):
            data = numpy.array(data, dtype=self.__acq_channel.dtype, copy=True)
        self.debug("SET DATA", "point_nb %d", self._point_count)
        self.__set_buffer(self._point_count, "data", data)
        self.__emit_data(self._point_count)
        self._increment_point(data)

    def _increment_point(self, data):
        try:
            npts = len(data)
        except TypeError:
            npts = 1
        self._point_count += npts

    def __call_new_data_receivers(self, data):
        """When the `emit` call is denied due to an invalid point, some
        "new_data" receivers need to be called regardless. In particular
        all the receivers that emit data to a channel instead of publishing it.
        It is needed because the patched channel needs to keep track of
        the number of points.
        """
        sender = self.__acq_channel
        signal = "new_data"
        for receiver in dispatcher.get_receivers(sender, signal):
            receiver = receiver()
            if self._is_emit_receiver(receiver):
                payload = self.__acq_channel._check_and_reshape(data)
                if payload is not None:
                    self.debug(" CALL RECEIVER", "%s", receiver)
                    receiver(payload, signal=signal, sender=sender)

    def _is_emit_receiver(self, receiver):
        """`new_data` receivers that emits data to a channel instead of
        publishing it.
        """
        if receiver is None:
            return False
        try:
            return receiver.__qualname__ == "CalcAcquisitionSlaveBase.new_data_received"
        except AttributeError:
            return False

    def __set_buffer(self, point_nb: int, name: str, value):
        self._get_buffer(point_nb)[name] = value

    def _get_buffer(self, point_nb: int):
        return self.__point_buffer.setdefault(point_nb, dict())

    def __pop_buffer(self, point_nb: int):
        return self.__point_buffer.pop(point_nb)

    def _non_point_data(self, point_nb: int):
        return False

    def __emit_data(self, point_nb: int) -> True:
        """Send the data of a point when either of these is true:
            - available point data for a valid point
            - non-point data

        For each `point_nb` either the data or the validation result
        gets set first.
        """
        if self._non_point_data(point_nb):
            buffer = self.__pop_buffer(point_nb)
            self._publish_data(point_nb, buffer)
            return  # published non-point data

        buffer = self._get_buffer(point_nb)
        point_is_not_complete = (
            buffer.get("point_is_valid") is None or buffer.get("data") is None
        )
        if point_is_not_complete:
            return  # keep in buffer, not enough information yet

        buffer = self.__pop_buffer(point_nb)
        try:
            if not buffer["point_is_valid"] and not buffer["save_first_attempt"]:
                self.debug("SKIP", "point_nb %d", point_nb)
                self.__call_new_data_receivers(buffer["data"])
                return  # discarded data of invalid point
            self._publish_data(point_nb, buffer)
            return  # published data of invalid point
        finally:
            point_data_processed = buffer.get("point_data_processed")
            if point_data_processed is not None:
                point_data_processed.set()

    def _publish_data(self, point_nb: int, buffer: dict):
        self.debug("PUBLISH", "point_nb %d", point_nb)
        self.__original_emit(buffer["data"])
        if self.__corr_channel is not None:
            # Instead of a calculation counter, we do it manually here
            corrected_data = self.__beam_attenuation_correction(
                point_nb, self.__acq_channel.name, buffer["data"]
            )
            self.__corr_channel.emit(corrected_data)


class AutoFilterLimaImageChannelPatch(AutoFilterChannelPatch):
    """The Lima image channel needs to be in MANUAL mode (otherwise lima
    saves also invalid points). The patched `emit` method also needs to
    take care of saving images.
    """

    def __init__(
        self, acq_channel: AcquisitionChannel, acq_master: LimaAcquisitionMaster, **kw
    ):
        super().__init__(acq_channel, **kw)
        self.__acq_master = acq_master
        self.__last_image = -1

    def _publish_data(self, point_nb: int, buffer: dict):
        self.__save_image(point_nb, buffer)
        super()._publish_data(point_nb, buffer)

    def _non_point_data(self, point_nb: int):
        return False

    def _increment_point(self, data):
        self._point_count += 1

    def __save_image(self, point_nb: int, buffer: dict):
        data = buffer["data"]
        while data["last_index"] != point_nb:
            data.update(self.__acq_master._get_lima_status())
            gevent.sleep()
        self.__last_image += 1

        if self.__acq_master.acq_params["saving_mode"] == "MANUAL":
            self.__acq_master.device.proxy.writeImage(data["last_index"])
            data["last_index_saved"] = self.__last_image
        else:
            data["last_index_saved"] = -1
        data["last_index"] = self.__last_image


class AutoFilterValidationChannelPatch(AutoFilterChannelPatch):
    """The data of this channel is used by the filter set for validation.
    The patched `emit` method notifies all other acquisition channels
    in the chain of a point being valid or not.
    """

    def __init__(
        self,
        acq_channel: AcquisitionChannel,
        filterset: FilterSet,
        acq_chain: AcquisitionChain,
        count_time,
        save_first_count: bool,
        **kw,
    ):
        super().__init__(acq_channel, **kw)
        self.__filterset = filterset
        self.__acq_chain = acq_chain
        self.__count_time = count_time
        self.__save_first_count = save_first_count

    def _patched_emit(self, data):
        self.__validate_data(data)
        super()._patched_emit(data)

    def __validate_data(self, data):
        """Validate and notify all channels and the master of the chain"""
        data = numpy.atleast_1d(data)
        if data.size != 1:
            raise ValueError("The autofilter detector should generate 0D data")
        data = data[0]
        point_is_valid, new_filter = self.__filterset.adjust_filter(
            self.__count_time, data
        )
        point_data_processed = LimitEvent()

        master = self.__acq_chain.top_masters[0]

        # if validated from autof controller, the first tried count of each scan point will be saved
        save_first_count = self.__save_first_count and master.attempt_nb == 1

        self.debug(
            "SET VALID CHANNELS", "point_nb %d %s", self._point_count, point_is_valid
        )
        for acq_obj in self.__acq_chain.nodes_list:
            for channel in acq_obj.channels:
                try:
                    set_data_is_valid = channel.set_data_is_valid
                except AttributeError:
                    continue  # Correction channel (see AutoFilterChannelPatch)
                set_data_is_valid(
                    self._point_count,
                    point_is_valid,
                    point_data_processed,
                    save_first_count,
                )
        self.debug(
            "SET VALID MASTER", "point_nb %d %s", self._point_count, point_is_valid
        )
        master.set_data_is_valid(
            self._point_count,
            point_is_valid,
            point_data_processed,
            self.__filterset.set_filter,
            new_filter,
        )
        self.debug("SET VALID END", "point_nb %d %s", self._point_count, point_is_valid)


def _ensure_patcheable_iter(cls):
    """The `__iter__` method of a class instance can normally not be
    monkey patched. However by patching `__iter__` of the class itself
    we can monkey patch instances by assigning a method to the
    `__patched_iter__` attribute of the instance.
    """
    if hasattr(cls, "__patched_iter__"):
        return
    __original_iter__ = cls.__iter__

    def __patched_iter__(self):
        yield from self.__original_iter__()

    def __iter__(self):
        yield from self.__patched_iter__()

    cls.__iter__ = __iter__
    cls.__patched_iter__ = __patched_iter__
    cls.__original_iter__ = __original_iter__


class AutoFilterVariableStepTriggerMasterPatch:
    """Repeats each point until it is validated by the AutoFilterValidationChannelPatch
    channel in the acquisition chain.
    """

    def __init__(self, master: VariableStepTriggerMaster):
        self.__master = master
        self.__validation_results = Queue()

        _ensure_patcheable_iter(master.__class__)
        master.__patched_iter__ = self.__patched_iter

        master.set_data_is_valid = self.__set_data_is_valid
        master.attempt_nb = 0

    def __patched_iter(self):
        master = self.__master
        validation_results = self.__validation_results
        point_is_valid = None
        point_data_processed = None
        msg_attempt = "\nAutofilter measure point %d (attempt %d)"
        msg_done = "\nAutofilter completed point %d in %d attempts"
        msg_finished = "\nAutofilter finished with %d points"
        for point_nb, master in enumerate(master.__original_iter__()):
            master.attempt_nb = 1
            logger.debug(msg_attempt, point_nb, master.attempt_nb)
            yield master
            # Repeat this point until it is validated:
            while True:
                (
                    point_is_valid,
                    point_data_processed,
                    set_filter_func,
                    new_filter,
                ) = validation_results.get()
                point_data_processed.wait()
                if point_is_valid:
                    break
                set_filter_func(new_filter)
                master.attempt_nb += 1
                logger.debug(msg_attempt, point_nb, master.attempt_nb)
                yield master
            logger.debug(msg_done, point_nb, master.attempt_nb)
        master.stop_all_slaves()
        logger.debug(msg_finished, point_nb)

    def __set_data_is_valid(
        self,
        point_nb: int,
        point_is_valid: bool,
        point_data_processed: LimitEvent,
        set_filter_func,
        new_filter,
    ):
        """Called from AutoFilterValidationChannelPatch"""
        logger.debug("GET VALID MASTER point_nb %d %s", point_nb, point_is_valid)
        self.__validation_results.put(
            (point_is_valid, point_data_processed, set_filter_func, new_filter)
        )


class AutoFilterLimaAcquisitionMasterPatch:
    """When saving images, force MANUAL mode."""

    def __init__(self, acq_master: LimaAcquisitionMaster):
        self.__acq_master = acq_master
        self.__original_set_device_saving = acq_master.set_device_saving
        acq_master.set_device_saving = self.__set_device_saving

    def __set_device_saving(self, *args, **kw):
        if self.__acq_master.save_flag and not kw.get("force_no_saving"):
            self.__acq_master.acq_params["saving_mode"] = "MANUAL"
        self.__original_set_device_saving(*args, **kw)


class AutoFilterMcaAcquisitionSlavePatch:
    """Force block size to be 1"""

    def __init__(self, acq_slave: McaAcquisitionSlave):
        acq_slave.block_size = 1


def patch_acq_chain(
    acq_chain: AcquisitionChain, auto_filter: AutoFilter, patch_npoints: Callable = None
):
    """Patch the acquisition chain in-place: mainly patches the VariableStepTriggerMaster
    and the `emit` methods of all acquisition channels.

    Optionally the `npoints` of all acquisition objects can be patches as well. The
    VariableStepTriggerMaster keeps the required `npoints` for a fixed-length scan,
    while all other acquisition objects will have more `npoints` that required which
    will serve as a maximum (normal execution stops before reaching that maximum).
    """
    top_masters = acq_chain.top_masters
    if len(top_masters) != 1:
        raise RuntimeError(
            top_masters, "Autofilter scans must be single-top master scans"
        )

    acq_objects = acq_chain.nodes_list
    for acq_obj in acq_objects:
        if isinstance(acq_obj, SoftwareTimerMaster):
            count_time = acq_obj.count_time
            break
    else:
        raise RuntimeError("Autofilter scans need a SoftwareTimerMaster")

    validator_fullname = auto_filter.detector_counter.fullname

    for acq_obj in acq_objects:
        if patch_npoints is not None:
            # Note: this doesn't (and shouldn't) affect VariableStepTriggerMaster
            acq_obj._AcquisitionObject__npoints = patch_npoints(acq_obj.npoints)
        lima_image_channel = None
        if isinstance(acq_obj, AcquisitionMaster):
            logger.debug("PATCH MASTER %s", acq_obj.name)
            _patch_acq_master(acq_obj, is_top_master=acq_obj in top_masters)
            if isinstance(acq_obj, LimaAcquisitionMaster):
                lima_image_channel = acq_obj._image_channel
        elif isinstance(acq_obj, AcquisitionSlave):
            logger.debug("PATCH SLAVE %s", acq_obj.name)
            _patch_acq_slave(acq_obj)
        for acq_channel in list(acq_obj.channels):
            logger.debug("PATCH CHANNEL %s of %s", acq_channel.name, acq_obj.name)
            _patch_acq_channel(
                acq_channel,
                acq_obj=acq_obj,
                acq_chain=acq_chain,
                auto_filter=auto_filter,
                validator_fullname=validator_fullname,
                count_time=count_time,
                is_lima_image=acq_channel is lima_image_channel,
            )


def _patch_acq_master(acq_master: AcquisitionMaster, is_top_master=False):
    if is_top_master:
        if not isinstance(acq_master, VariableStepTriggerMaster):
            raise RuntimeError(
                acq_master,
                "Autofilter scans only support scans with a VariableStepTriggerMaster master",
            )
    if isinstance(acq_master, LimaAcquisitionMaster):
        return AutoFilterLimaAcquisitionMasterPatch(acq_master)
    elif is_top_master:
        return AutoFilterVariableStepTriggerMasterPatch(acq_master)


def _patch_acq_slave(acq_slave: AcquisitionSlave):
    if isinstance(acq_slave, McaAcquisitionSlave):
        return AutoFilterMcaAcquisitionSlavePatch(acq_slave)


def _patch_acq_channel(
    acq_channel: AcquisitionChannel,
    acq_obj: AcquisitionObject,
    acq_chain: AcquisitionChain,
    auto_filter: AutoFilter,
    validator_fullname,
    count_time,
    is_lima_image,
):
    beam_attenuation_correction = None
    corr_suffix = None

    if acq_channel.fullname == validator_fullname:
        if is_lima_image:
            raise RuntimeError(
                "Autofilter scans cannot use the Lima image as a validation detector"
            )
        return AutoFilterValidationChannelPatch(
            acq_channel,
            auto_filter.filterset,
            acq_chain,
            count_time,
            auto_filter.save_first_count,
            beam_attenuation_correction=beam_attenuation_correction,
            corr_suffix=corr_suffix,
        )
    elif is_lima_image:
        return AutoFilterLimaImageChannelPatch(
            acq_channel,
            acq_obj,
            beam_attenuation_correction=beam_attenuation_correction,
            corr_suffix=corr_suffix,
        )
    else:
        return AutoFilterChannelPatch(
            acq_channel,
            beam_attenuation_correction=beam_attenuation_correction,
            corr_suffix=corr_suffix,
        )
