# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

"""
bliss.controller.motor.EncoderCounterController
bliss.controller.motor.Controller
bliss.controller.motor.CalcController
"""
import math
import itertools
import functools
import numpy
import gevent
from gevent import lock
import tabulate

# absolute import to avoid circular import
import bliss.common.motor_group as motor_group
from bliss.common.logtools import log_debug
from bliss.common.motor_config import MotorConfig
from bliss.common.motor_settings import ControllerAxisSettings, floatOrNone
from bliss.common.axis.axis import Axis
from bliss.common.axis.trajectory import Trajectory
from bliss.common.closed_loop import ClosedLoopState
from bliss.common import event
from bliss.controllers.counter import SamplingCounterController
from bliss.physics import trajectory
from bliss.common.utils import set_custom_members, object_method
from bliss import global_map
from bliss.config.channels import Cache

from bliss.config.plugins.generic import ConfigItemContainer


class EncoderCounterController(SamplingCounterController):
    def __init__(self, motor_controller):
        super().__init__("encoder")

        self.motor_controller = motor_controller

        # High frequency acquisition loop
        self.max_sampling_frequency = None

    def read_all(self, *encoders):
        steps_per_unit = numpy.array([enc.steps_per_unit for enc in encoders])
        try:
            positions_array = numpy.array(
                self.motor_controller.read_encoder_multiple(*encoders)
            )
        except NotImplementedError:
            try:
                positions_array = numpy.array(
                    list(map(self.motor_controller.read_encoder, encoders))
                )
            except Exception as exc:
                # raise any exception, but suppress context (first "NotImplementedError"),
                # see issue #3294
                raise exc from None
        return positions_array / steps_per_unit


def check_disabled(func):
    """
    Decorator used to raise exception if accessing an attribute of a disabled
    motor controller.
    """

    @functools.wraps(func)
    def func_wrapper(self, *args, **kwargs):
        if self._disabled:
            raise RuntimeError("Controller is disabled. Check hardware and restart.")
        return func(self, *args, **kwargs)

    return func_wrapper


class Controller(ConfigItemContainer):
    """
    Motor controller base class
    """

    def __init__(self, *args, **kwargs):  # config

        if len(args) == 1:
            config = args[0]
        else:
            # handle old signature: args = [ name, config, axes, encoders, shutters, switches ]
            config = args[1]

        super().__init__(config)

        self.__motor_config = MotorConfig(config)
        self.__initialized_hw = Cache(self, "initialized", default_value=False)
        self.__initialized_hw_axis = dict()
        self.__initialized_encoder = dict()
        self.__initialized_axis = dict()
        self.__lock = lock.RLock()
        self._encoder_counter_controller = EncoderCounterController(self)
        self._axes = dict()
        self._encoders = dict()
        self._shutters = dict()
        self._switches = dict()
        self._tagged = dict()
        self._disabled = False

        self._axis_settings = ControllerAxisSettings()
        global_map.register(self, parents_list=["controllers"])

    def __close__(self):
        self.close()

    def close(self):
        pass

    def _load_config(self):
        self._axes_config = {}
        self._encoders_config = {}
        self._shutters_config = {}
        self._switches_config = {}

        for k, v in self._subitems_config.items():
            cfg, pkey = v
            if pkey == "axes":
                self._axes_config[k] = cfg

            elif pkey == "encoders":
                self._encoders_config[k] = cfg

            elif pkey == "shutters":
                self._shutters_config[k] = cfg

            elif pkey == "switches":
                self._switches_config[k] = cfg

    def _get_subitem_default_module(self, class_name, cfg, parent_key):
        if parent_key == "axes":
            return "bliss.common.axis"

        elif parent_key == "encoders":
            return "bliss.common.encoder"

        elif parent_key == "shutters":
            return "bliss.common.shutter"

        elif parent_key == "switches":
            return "bliss.common.switch"

    def _get_subitem_default_class_name(self, cfg, parent_key):
        if parent_key == "axes":
            return "Axis"
        elif parent_key == "encoders":
            return "Encoder"
        elif parent_key == "shutters":
            return "Shutter"
        elif parent_key == "switches":
            return "Switch"

    @check_disabled
    def _create_subitem_from_config(
        self, name, cfg, parent_key, item_class, item_obj=None
    ):

        if parent_key == "axes":
            axis = self._create_axis_subitem(
                name, cfg, parent_key, item_class, item_obj
            )
            self._axes[name] = axis
            return axis

        elif parent_key == "encoders":
            encoder = self._encoder_counter_controller.create_counter(
                item_class, name, motor_controller=self, config=cfg
            )
            self._encoders[name] = encoder
            self.__initialized_encoder[encoder] = False
            return encoder

        elif parent_key == "switches":
            switch = item_class(name, cfg)
            self._switches[name] = switch
            return switch

        elif parent_key == "shutters":
            shutter = item_class(name, cfg)
            self._shutters[name] = shutter
            return shutter

    def _create_axis_subitem(self, name, cfg, parent_key, item_class, item_obj=None):
        axis = item_class(name, self, cfg)
        set_custom_members(self, axis, self._initialize_axis)
        axis_initialized = Cache(axis, "initialized", default_value=0)
        self.__initialized_hw_axis[axis] = axis_initialized
        self.__initialized_axis[axis] = False
        self._add_axis(axis)
        return axis

    def _init(self):
        try:
            self.initialize()
            self._disabled = False
        except BaseException:
            self._disabled = True
            raise

    @property
    def config(self):
        return self.__motor_config

    @property
    def axes(self):
        return self._axes

    @property
    def encoders(self):
        return self._encoders

    @property
    def shutters(self):
        return self._shutters

    @property
    def switches(self):
        return self._switches

    @property
    def axis_settings(self):
        return self._axis_settings

    @check_disabled
    def get_axis(self, name):
        return self._get_subitem(name)

    @check_disabled
    def get_encoder(self, name):
        return self._get_subitem(name)

    @check_disabled
    def get_shutter(self, name):
        return self._get_subitem(name)

    @check_disabled
    def get_switch(self, name):
        return self._get_subitem(name)

    def steps_position_precision(self, axis):
        """
        Return a float value representing the precision of the position in steps

        * 1e-6 is the default value: it means the motor can deal with floating point
          steps up to 6 digits
        * 1 means the motor controller can only deal with an integer number of steps
        * soft axis controller return -inf to shunt the _is_already_on_position() check
        """
        return 1e-6

    def check_limits(self, *axis_dial_pos_groups):
        """
        Check limits for sequence of (axis, positions) tuples
        """
        for axis, dial_positions in axis_dial_pos_groups:
            self._do_check_limits(axis, dial_positions)

    def _do_check_limits(self, axis, dial_positions):
        # axis._get_motion will raise ValueError if position is outside limits
        try:
            iter(dial_positions)
        except TypeError:
            # dial_positions is a scalar
            axis._get_motion(axis.dial2user(dial_positions))
        else:
            # check min and max values only (ok for a real/physical axis)
            axis._get_motion(axis.dial2user(min(dial_positions)))
            axis._get_motion(axis.dial2user(max(dial_positions)))

    def initialize(self):
        pass

    def initialize_hardware(self):
        """
        This method should contain all commands needed to initialize the controller hardware.
        i.e: reset, power on....
        This initialization will be called once (by the first client).
        """
        pass

    def finalize(self):
        pass

    @check_disabled
    def encoder_initialized(self, encoder):
        return self.__initialized_encoder[encoder]

    @check_disabled
    def _initialize_encoder(self, encoder):
        with self.__lock:
            if self.__initialized_encoder[encoder]:
                return
            self.__initialized_encoder[encoder] = True
            self._initialize_hardware()

            try:
                self.initialize_encoder(encoder)
            except BaseException as enc_init_exc:
                self.__initialized_encoder[encoder] = False
                raise RuntimeError(
                    f"Cannot initialize {self.name} encoder"
                ) from enc_init_exc

    @check_disabled
    def axis_initialized(self, axis):
        return self.__initialized_axis[axis]

    def _initialize_hardware(self):
        # initialize controller hardware only once.

        if not self.__initialized_hw.value:
            try:
                self.initialize_hardware()
            except BaseException:
                self._disabled = True
                raise
            self.__initialized_hw.value = True

    @check_disabled
    def _initialize_axis(self, axis, *args, **kwargs):
        """
        Called by axis.lazy_init
        """
        with self.__lock:
            if self.__initialized_axis[axis]:
                return

            self._initialize_hardware()

            # Consider axis is initialized
            # => prevent re-entering  _initialize_axis()  in lazy_init
            self.__initialized_axis[axis] = True

            try:
                # Call specific axis initialization.
                self.initialize_axis(axis)

                # Call specific hardware axis initialization.
                # Done only once even in case of multi clients.
                axis_initialized = self.__initialized_hw_axis[axis]
                if not axis_initialized.value:
                    self.initialize_hardware_axis(axis)
                    axis.settings.check_config_settings()
                    axis.settings.init()  # get settings, from config or from cache, and apply to hardware
                    if axis.closed_loop is not None:
                        if axis.closed_loop.state != self.get_closed_loop_state(axis):
                            if axis.closed_loop.state is ClosedLoopState.ON:
                                axis.closed_loop.on()
                            elif axis.closed_loop.state is ClosedLoopState.OFF:
                                axis.closed_loop.off()
                            else:
                                # closed_loop was neither left in ON or OFF state
                                # can't decide what to do, sync with value from hardware
                                axis.closed_loop.sync_hard()
                        axis.closed_loop._activate_setters()
                    axis_initialized.value = 1

            except BaseException:
                # Failed to initialize
                self.__initialized_axis[axis] = False
                raise

    def _add_axis(self, axis):
        """
        This method is called when a new axis is attached to
        this controller.
        This is called only once per axis.
        """
        pass

    def initialize_axis(self, axis):
        raise NotImplementedError

    def initialize_hardware_axis(self, axis):
        """
        This method should contain all commands needed to initialize the
        hardware for this axis.
        i.e: power, closed loop configuration...
        This initialization will call only once (by the first client).
        """
        pass

    def finalize_axis(self, axis):
        raise NotImplementedError

    def get_closed_loop_requirements(self):
        """Return the list of keys this controller expects in a closed loop config"""
        raise NotImplementedError

    def get_closed_loop_state(self, axis):
        """
        DON'T OVERRIDE THIS METHOD, but implement _do_get_closed_loop_state()
        instead. Return ClosedLoopState.UNDEFINED if axis has no closed loop,
        otherwise return the actual state from hardware.
        """
        if axis.closed_loop is None:
            return ClosedLoopState.UNDEFINED
        else:
            return self._do_get_closed_loop_state(axis)

    def _do_get_closed_loop_state(self, axis):
        """Return closed loop state by requesting hardware"""
        raise NotImplementedError

    def activate_closed_loop(self, axis, onoff=True):
        raise NotImplementedError

    def set_closed_loop_param(self, axis, param, value):
        raise NotImplementedError

    def get_closed_loop_param(self, axis, param):
        raise NotImplementedError

    def get_class_name(self):
        return self.__class__.__name__

    def initialize_encoder(self, encoder):
        raise NotImplementedError

    def has_trajectory(self):
        """
        should return True if trajectory is available
        on this controller.
        """
        return False

    def has_trajectory_event(self):
        return False

    def _prepare_trajectory(self, *trajectories):
        for traj in trajectories:
            if traj.has_events() and not self.has_trajectory_event():
                raise NotImplementedError(
                    "Controller does not support trajectories with events"
                )
        else:
            self.prepare_trajectory(*trajectories)
            if self.has_trajectory_event():
                self.set_trajectory_events(*trajectories)

    def prepare_trajectory(self, *trajectories):
        pass

    def prepare_all(self, *motion_list):
        raise NotImplementedError

    def prepare_move(self, motion):
        return

    def start_jog(self, axis, velocity, direction):
        raise NotImplementedError

    def start_one(self, motion):
        raise NotImplementedError

    def start_all(self, *motion_list):
        raise NotImplementedError

    def move_to_trajectory(self, *trajectories):
        """
        Should go move to the first point of the trajectory
        """
        raise NotImplementedError

    def start_trajectory(self, *trajectories):
        """
        Should move to the last point of the trajectory
        """
        raise NotImplementedError

    def set_trajectory_events(self, *trajectories):
        """
        Should set trigger event on trajectories.
        Each trajectory define .events_positions or events_pattern_positions.
        """
        raise NotImplementedError

    def stop(self, axis):
        raise NotImplementedError

    def stop_jog(self, axis):
        return self.stop(axis)

    def stop_all(self, *motions):
        raise NotImplementedError

    def stop_trajectory(self, *trajectories):
        raise NotImplementedError

    def state(self, axis):
        raise NotImplementedError

    def check_ready_to_move(self, axis, state):
        """
        method to check if the axis can move with the current state
        """
        if not state.READY:
            # read state from hardware
            state = axis.hw_state
            axis.settings.set("state", state)

        return state.READY

    def get_info(self, axis):
        raise NotImplementedError

    def get_axis_info(self, axis):
        """
        To overload to display information related to this axis controller in the
        axis __info__ method.
        """
        raise NotImplementedError

    def get_id(self, axis):
        raise NotImplementedError

    def raw_write(self, com):
        raise NotImplementedError

    def raw_write_read(self, com):
        raise NotImplementedError

    def home_search(self, axis, switch):
        raise NotImplementedError

    def home_state(self, axis):
        raise NotImplementedError

    def limit_search(self, axis, limit):
        raise NotImplementedError

    def read_position(self, axis):
        raise NotImplementedError

    def set_position(self, axis, new_position):
        """Set the position of <axis> in controller to <new_position>.
        This method is called by `position` property of <axis>.
        """
        raise NotImplementedError

    def read_encoder(self, encoder):
        """Return the encoder value in *encoder steps*."""
        raise NotImplementedError

    def read_encoder_multiple(self, *encoder):
        """Return the encoder value in *encoder steps*."""
        raise NotImplementedError

    def set_encoder(self, encoder, new_value):
        """Set encoder value. <new_value> is in encoder steps."""
        raise NotImplementedError

    def read_velocity(self, axis):
        raise NotImplementedError

    def set_velocity(self, axis, new_velocity):
        raise NotImplementedError

    def set_on(self, axis):
        raise NotImplementedError

    def set_off(self, axis):
        raise NotImplementedError

    def read_acceleration(self, axis):
        raise NotImplementedError

    def set_acceleration(self, axis, new_acc):
        raise NotImplementedError

    def set_event_positions(self, axis_or_encoder, positions):
        """
        This method is use to load into the controller
        a list of positions for event/trigger.
        The controller should generate an event
        (mainly electrical pulses) when the axis or
        the encoder pass through one of this position.
        """
        raise NotImplementedError

    def get_event_positions(self, axis_or_encoder):
        """
        @see set_event_position
        """
        raise NotImplementedError

    def _is_already_on_position(self, axis, delta):
        """Return True if the difference between current position
        and new position (delta) is smaller than the positioning precision
        """
        if abs(delta) < (self.steps_position_precision(axis) / 2):
            return True  # Already in position
        return False


class CalcController(Controller):
    _PROTECTED_TAGS = ["real", "param"]

    def __init__(self, *args, **kwargs):

        self._reals_group = None
        self._reals = []
        self._pseudos = []
        self._params = []
        self._in_real_pos_update = False
        self._real_pos_cache_dict = {}
        self._real_move_is_done = True

        super().__init__(*args, **kwargs)

        self.axis_settings.config_setting["velocity"] = False
        self.axis_settings.config_setting["acceleration"] = False
        self.axis_settings.config_setting["steps_per_unit"] = False

    def _get_subitem_default_class_name(self, cfg, parent_key):
        if parent_key == "axes":
            return "CalcAxis"

    def _create_axis_subitem(self, name, cfg, parent_key, item_class, item_obj=None):
        axis_tags = cfg["tags"].split()  # tags is mandatory

        # === handle input axes (aka 'reals')
        if item_class is None:  # it is a reference
            if not isinstance(item_obj, Axis):
                raise ValueError(
                    f"CalcController input must be an axis but received {item_obj} instead"
                )

            # check input axis tag validity
            for ptag in self._PROTECTED_TAGS:
                if ptag in axis_tags[1:]:
                    raise ValueError(
                        f"Input axis {name} with role {axis_tags[0]} cannot be tagged {ptag} (this tag is reserved for the role only)"
                    )

            axis = item_obj
            event.connect(axis, "internal_move_done", self._real_axis_move_done)
            event.connect(axis, "internal_position", self._real_position_update)
            event.connect(axis, "internal__set_position", self._real_setpos_update)

            self._add_input_axis(axis, axis_tags)

        # === handle output axes (aka 'pseudos')
        else:

            # check output axis tag validity
            for ptag in self._PROTECTED_TAGS:
                if ptag in axis_tags:
                    raise ValueError(
                        f"Output axis {name} cannot be tagged {ptag} (this tag is reserved for the input axes role only)"
                    )

            axis = super()._create_axis_subitem(
                name, cfg, parent_key, item_class, item_obj
            )
            event.connect(axis, "sync_hard", self.sync_hard)
            self.pseudos.append(axis)

        for tag in axis_tags:
            self._tagged.setdefault(tag, []).append(axis)

        return axis

    def _add_input_axis(self, axis, axis_tags):
        """Decide if an input axis should be added to the reals or the params axes list.
        Choice is made from the 'axis_tags' list knowledge.
        A parametric axis is identified if the first tag is 'param', else it is considered as a real.
        Overwrite this method in a child class if identification can be based on another tag (such as the role tag).
        """
        if axis_tags[0] == "param":
            self.params.append(axis)
        else:
            self.reals.append(axis)

    @property
    def reals(self):
        return self._reals

    @property
    def pseudos(self):
        return self._pseudos

    @property
    def params(self):
        return self._params

    def _axis_tag(self, axis):
        return [
            tag
            for tag, axes in self._tagged.items()
            if tag not in ["real", "param"] and len(axes) == 1 and axis in axes
        ][0]

    def _init(self):
        try:
            # Force creation of all axes subitems (reals and pseudos)
            # because it is not possible to predict which reals will be used
            # in the calc_from_real method of a child class
            for axis_name in self._axes_config.keys():
                self.get_axis(axis_name)

            super()._init()

        except BaseException:
            self.close()
            raise

    def _clear_real_pos_cache(self):
        self._real_pos_cache_dict.clear()

    def initialize(self):
        # called after self._init, so all axes are loaded at this point
        self._reals_group = motor_group.Group(*self.reals)
        global_map.register(self, children_list=self.reals + self.params)
        self._clear_real_pos_cache()

    def close(self):
        self._clear_real_pos_cache()

        for pseudo_axis in self.pseudos:
            event.disconnect(pseudo_axis, "sync_hard", self.sync_hard)

        for axis in self.reals + self.params:
            event.disconnect(axis, "internal_move_done", self._real_axis_move_done)
            event.disconnect(axis, "internal_position", self._real_position_update)
            event.disconnect(axis, "internal__set_position", self._real_setpos_update)

        self._reals_group = None
        self._reals = []
        self._pseudos = []

    def initialize_axis(self, axis):
        pass

    def sync_hard(self):
        # sync reals
        for real_axis in self.reals:
            event.disconnect(real_axis, "internal_position", self._real_position_update)
            event.disconnect(
                real_axis, "internal__set_position", self._real_setpos_update
            )
            try:
                real_axis.sync_hard()
            finally:
                event.connect(
                    real_axis, "internal_position", self._real_position_update
                )
                event.connect(
                    real_axis, "internal__set_position", self._real_setpos_update
                )

        hw_state = self._reals_group.state
        for pseudo in self.pseudos:
            pseudo.settings.set("state", hw_state)

        # sync pseudos
        self.sync_pseudos()

    def sync_pseudos(self):
        self._clear_real_pos_cache()
        self._calc_from_real()
        for pseudo in self.pseudos:
            pseudo._set_position = pseudo.position

    def _get_motion_pos_dict(self, motion_list) -> dict:
        """Get all necessary axes with their dial set_positions to compute calc_to_real.

        Arguments:
            motion_list: List of Motion objects corresponding to axes that are required to move

        Returns:
            A dict { axis_tag: dial_set_position }

        Note: in this base class method, positions of all pseudos and parametrics axes are returned
        as it is not possible to predict which axes are required to compute the reals positions.
        """
        moving_axes = []
        dial_set_positions = {}
        for motion in motion_list:
            axis = motion.axis
            moving_axes.append(axis)
            axis_tag = self._axis_tag(axis)
            dial_set_positions[axis_tag] = motion.dial_target_pos

        # get complementary pseudos
        dial_set_positions.update(self._get_complementary_pseudos_pos_dict(moving_axes))

        return dial_set_positions

    def _get_complementary_pseudos_pos_dict(self, axes):
        """Find the other pseudos which are not in 'axes' and also add parametric axes, and get their actual dial set_position.

        Complementary axes are necessary to compute the reals positions
        via the 'calc_to_real' method.

        Arguments:
            axes: list of Axis objects
        Returns:
            a dict {axis_tag: dial_set_position, ...}
        """
        return {
            self._axis_tag(axis): axis.user2dial(axis._set_position)
            for axis in itertools.chain(self.pseudos, self.params)
            if axis not in axes
        }

    def _get_real_axes_move_dict(self, motion_list):
        pseudos_dial_pos = self._get_motion_pos_dict(motion_list)
        move_dict = {}
        for tag, real_user_pos in self.calc_to_real(pseudos_dial_pos).items():
            real_axis = self._tagged[tag][0]
            move_dict[real_axis] = real_user_pos
        return move_dict

    def _real_position_update(self, pos, sender=None):
        self._real_pos_cache_dict[sender] = pos  # using user pos
        if self._in_real_pos_update:
            return
        self._in_real_pos_update = True
        try:
            for axis in self.pseudos:
                self._initialize_axis(axis)
            return self._calc_from_real()
        finally:
            self._in_real_pos_update = False

    def _real_setpos_update(self, real_setpos):
        if any(m.is_moving for m in self.pseudos):
            return
        self._update_pseudos_setpos_from_reals()

    def _update_pseudos_setpos_from_reals(self):
        reals_user_pos_dict = {
            self._axis_tag(axis): axis._set_position for axis in self.reals
        }

        reals_user_pos_dict.update(
            {self._axis_tag(axis): axis._set_position for axis in self.params}
        )

        pseudos_dial_pos = self.calc_from_real(reals_user_pos_dict)

        for tag, dial_pos in pseudos_dial_pos.items():
            axis = self._tagged[tag][0]
            axis.settings.set("_set_position", axis.dial2user(dial_pos))

    def axes_are_moving(self):
        return not self._real_move_is_done

    def check_limits(self, *axis_dial_pos_groups):
        axes = set()
        posnum = set()  # number of postions per axis
        for axis, dial_pos in axis_dial_pos_groups:

            # === check that axis is owned by this controller
            if axis.controller is not self:
                raise RuntimeError(
                    f"limits check: Axis {axis.name} is not managed by this controller"
                )

            # === check limits of this axis
            self._do_check_limits(axis, dial_pos)

            # === avoid duplicated axis and store its number of postions
            axes.add(axis)
            try:
                size = len(dial_pos)
            except TypeError:
                size = 1
            posnum.add(size)

        # === check that all axes have the same number of positions (for later calc_to_real() computation)
        try:
            assert len(posnum) == 1
        except AssertionError:
            raise RuntimeError(
                f"Axes {axes} doesn't have the same number of positions to check"
            )

        # === build a { axis_tag: position } dict from pseudo axes and parametric axes ;
        # === positions are uniformized to numpy array
        posnum = posnum.pop()
        pseudo_dial_positions = {
            tag: numpy.ones(posnum)
            * dial_pos  # ensure complementary positions have same length
            for tag, dial_pos in self._get_complementary_pseudos_pos_dict(axes).items()
        }

        pseudo_dial_positions.update(
            {
                self._axis_tag(axis): numpy.array(dial_pos, ndmin=1)
                for axis, dial_pos in axis_dial_pos_groups
            }
        )

        # === compute corresponding positions of 'real' axes (which can be CalcAxis too!)
        real_user_positions = self.calc_to_real(pseudo_dial_positions)

        # === check limits of 'real' axes for computed positions (recursively)
        for tag, user_pos in real_user_positions.items():
            real_axis = self._tagged[tag][0]
            real_axis.controller.check_limits(
                (real_axis, real_axis.user2dial(numpy.array(user_pos, ndmin=1)))
            )

    def _calc_from_real(self):
        real_user_positions = {
            self._axis_tag(axis): self._real_pos_cache_dict.setdefault(
                axis, axis.position
            )
            for axis in self.reals
        }
        real_user_positions.update(
            {self._axis_tag(axis): axis.position for axis in self.params}
        )

        pseudo_dial_positions = self.calc_from_real(real_user_positions)

        for tag, dial_pos in pseudo_dial_positions.items():
            axis = self._tagged[tag][0]
            if axis in self.pseudos:
                user_pos = axis.dial2user(dial_pos)
                axis.settings.set(
                    "dial_position",
                    dial_pos,
                    "position",
                    user_pos,
                )
            else:
                msg = f"Axis {axis.name} with tag {tag} is not a pseudo.\n"
                msg += "Check that dict returned by 'calc_from_real' has pseudos only."
                raise RuntimeError(msg)
        return pseudo_dial_positions

    def calc_from_real(self, real_user_positions):
        """Computes pseudo dial positions from real user positions.

        => pseudo_dial_positions = self.calc_from_real(real_user_positions)

        Args:
          real_user_positions: { real_tag: real_user_positions, ... }
        Return:
          a dict: {pseudo_tag: pseudo_dial_positions, ...}
        """
        raise NotImplementedError

    def calc_to_real(self, pseudo_dial_positions):
        """Computes reals user positions from pseudo dial positions.

        => real_user_positions = self.calc_to_real(pseudo_dial_positions)

        Args:
          pseudo_dial_positions: {pseudo_tag: pseudo_dial_positions, ...}
        Return:
          a dict: { real_tag: real_user_positions, ... }
        """
        raise NotImplementedError

    def _real_axis_move_done(self, done, sender=None):
        if done and not any(motor.is_moving for motor in self.reals):
            self._real_move_done(True)
        elif self._real_move_is_done:
            self._real_move_done(False)

    def _real_move_done(self, done):
        if done == self._real_move_is_done:
            return

        self._real_move_is_done = done
        if done:
            self._clear_real_pos_cache()
            self._calc_from_real()

            # ensure re-sync of all pseudos set_position
            # necessary for none-bijective calc such as BraggAxis
            self._update_pseudos_setpos_from_reals()

            tasks = [gevent.spawn(axis._set_move_done) for axis in self.pseudos]
            gevent.joinall(tasks)

        else:
            for axis in self.pseudos:
                axis._set_moving_state()

    def start_one(self, motion):
        self.start_all(motion)

    def start_all(self, *motion_list):
        pass

    def stop(self, axis):
        self._reals_group.stop()

    def read_position(self, axis):
        dial_pos = axis.settings.get("dial_position")
        if dial_pos is None:
            new_dial_positions = self._calc_from_real()
            dial_pos = new_dial_positions[self._axis_tag(axis)]
        return dial_pos

    def state(self, axis, new_state=None):
        return self._reals_group.state

    def hw_state(self, axis, new_state=None):
        return self._reals_group.hw_state

    def set_position(self, axis, new_dial_pos):
        if axis not in self.pseudos:
            raise RuntimeError(
                f"Cannot set dial position of axis {axis.name}, as it is not a pseudo"
            )
        dial_positions = self._get_complementary_pseudos_pos_dict((axis,))
        dial_positions[self._axis_tag(axis)] = new_dial_pos
        real_user_positions = self.calc_to_real(dial_positions)
        for tag, user_pos in real_user_positions.items():
            real_axis = self._tagged[tag][0]
            real_axis.position = user_pos

        self._real_pos_cache_dict = self._reals_group.position
        new_pseudo_dial_positions = self._calc_from_real()

        return new_pseudo_dial_positions[self._axis_tag(axis)]

    @object_method(types_info=(("float", "float", "int", "float"), "object"))
    def scan_on_trajectory(
        self,
        calc_axis,
        start_point,
        end_point,
        nb_points,
        time_per_point,
        interpolation_factor=1,
    ):
        """
        helper to create a trajectories handler for a scan.

        It will check the **trajectory_minimum_resolution** and
        **trajectory_maximum_resolution** axis property.
        If the trajectory resolution asked is lower than the trajectory_minimum_resolution,
        the trajectory will be over sampled.
        And if the trajectory resolution asked is higher than the trajectory_maximum_resolution
        the trajectory will be down sampled.
        Args:
            start -- first point of the trajectory
            end -- the last point of the trajectory
            nb_points -- the number of point created for this trajectory
            time_per_point -- the time between each points.
        """
        # check if real motor has trajectory capability
        real_axes = list()
        real_involved = self.calc_to_real(
            {self._axis_tag(caxis): caxis.position for caxis in self.pseudos}
        )
        for real in self.reals:
            if self._axis_tag(real) in real_involved:
                axis, raxes = self._check_trajectory(real)
                real_axes.append((axis, raxes))

        trajectory_minimum_resolution = calc_axis.config.get(
            "trajectory_minimum_resolution", floatOrNone, None
        )
        trajectory_maximum_resolution = calc_axis.config.get(
            "trajectory_maximum_resolution", floatOrNone, None
        )

        # Check if the resolution is enough
        total_distance = abs(end_point - start_point)
        trajectory_resolution = total_distance / float(nb_points)
        used_resolution = None

        if (
            trajectory_minimum_resolution is not None
            and trajectory_maximum_resolution is not None
        ):
            if not (
                trajectory_maximum_resolution
                >= trajectory_resolution
                >= trajectory_minimum_resolution
            ):
                if trajectory_resolution > trajectory_minimum_resolution:
                    used_resolution = trajectory_minimum_resolution
                else:
                    used_resolution = trajectory_maximum_resolution
        elif trajectory_minimum_resolution is not None:
            if trajectory_resolution > trajectory_minimum_resolution:
                used_resolution = trajectory_minimum_resolution
        elif trajectory_maximum_resolution is not None:
            if trajectory_resolution < trajectory_maximum_resolution:
                used_resolution = trajectory_maximum_resolution

        if used_resolution is not None:
            new_nb_points = int(round(total_distance / used_resolution))
            new_time_point = float(time_per_point * nb_points) / new_nb_points
            nb_points = new_nb_points
            time_per_point = new_time_point

        calc_positions = numpy.linspace(start_point, end_point, nb_points)
        positions = {self._axis_tag(calc_axis): calc_positions}
        # other virtual axis stays at the same position
        for caxis in self.pseudos:
            if caxis is calc_axis:
                continue
            cpos = numpy.zeros(len(calc_positions), dtype=float)
            cpos[:] = caxis.position
            positions[self._axis_tag(caxis)] = cpos

        time = numpy.linspace(0.0, nb_points * time_per_point, nb_points)
        real_positions = self.calc_to_real(positions)
        final_real_axes_position = dict()
        self._get_real_position(real_axes, real_positions, final_real_axes_position)

        pt = trajectory.PointTrajectory()
        spline_nb_points = (
            0 if interpolation_factor == 1 else len(time) * interpolation_factor
        )
        pt.build(
            time,
            {
                axis.name: position
                for axis, position in iter(final_real_axes_position.items())
            },
            spline_nb_points=spline_nb_points,
        )
        # check velocity and acceleration
        max_velocity = pt.max_velocity
        max_acceleration = pt.max_acceleration
        limits = pt.limits
        error_list = list()
        start_stop_acceleration = dict()
        for axis in final_real_axes_position:
            vel = axis.velocity
            acc = axis.acceleration
            axis_limits = axis.limits
            traj_vel = max_velocity[axis.name]
            traj_acc = max_acceleration[axis.name]
            traj_limits = limits[axis.name]
            if traj_acc > acc:
                error_list.append(
                    "Axis %s reach %f acceleration on this trajectory,"
                    "max acceleration is %f" % (axis.name, traj_acc, acc)
                )
            if traj_vel > vel:
                error_list.append(
                    "Axis %s reach %f velocity on this trajectory,"
                    "max velocity is %f" % (axis.name, traj_vel, vel)
                )
            for lm in traj_limits:
                if not axis_limits[0] <= lm <= axis_limits[1]:
                    error_list.append(
                        "Axis %s go beyond limits (%f <= %f <= %f)"
                        % (axis.name, axis_limits[0], traj_limits[0], axis_limits[1])
                    )

            start_stop_acceleration[axis.name] = acc

        if error_list:
            error_message = (
                "Trajectory on calc axis **%s** cannot be done.\n" % calc_axis.name
            )
            error_message += "\n".join(error_list)
            raise ValueError(error_message)

        pvt = pt.pvt(acceleration_start_end=start_stop_acceleration)
        trajectories = [
            Trajectory(axis, pvt[axis.name]) for axis in final_real_axes_position
        ]

        return motor_group.TrajectoryGroup(*trajectories, calc_axis=calc_axis)

    def _check_trajectory(self, axis):
        if axis.controller.has_trajectory():
            return axis, []
        else:  # check if axis is part of calccontroller
            ctrl = axis.controller
            if isinstance(ctrl, CalcController):
                real_involved = ctrl.calc_to_real(
                    {ctrl._axis_tag(caxis): caxis.position for caxis in ctrl.pseudos}
                )
                real_axes = list()
                for real in ctrl.reals:
                    if ctrl._axis_tag(real) in real_involved:
                        raxis, axes = self._check_trajectory(real)
                        real_axes.append((raxis, axes))
                return axis, real_axes
            else:
                raise ValueError(
                    "Controller for axis %s does not support "
                    "trajectories" % axis.name
                )

    def _get_real_position(self, real_axes, real_positions, final_real_axes_position):
        local_real_positions = dict()
        for axis, dep_real_axes in real_axes:
            axis_position = real_positions.get(self._axis_tag(axis))
            if not dep_real_axes:
                if axis_position is None:
                    raise RuntimeError(
                        "Could not get position " "for axis %s" % axis.name
                    )
                else:
                    final_real_axes_position[axis] = axis_position
            else:
                ctrl = axis.controller
                local_real_positions = {ctrl._axis_tag(axis): axis_position}
                for caxis in ctrl.pseudos:
                    axis_tag = ctrl._axis_tag(caxis)
                    if caxis is axis or axis_tag in local_real_positions:
                        continue
                    cpos = numpy.zeros(len(axis_position), dtype=float)
                    cpos[:] = caxis.position
                    local_real_positions[ctrl._axis_tag(caxis)] = cpos

                dep_real_position = ctrl.calc_to_real(local_real_positions)
                ctrl._get_real_position(
                    dep_real_axes, dep_real_position, final_real_axes_position
                )

    def _is_already_on_position(self, axis, delta):
        """With calculated axes, always return False to ensure it updates real axes that might
        have been moved independently (i.e outside CalcMotor context).
        """
        if axis not in self.reals:
            return False
        else:
            return super()._is_already_on_position(axis, delta)

    def __info__(self):
        def format_axis(axis: Axis) -> list:
            unit = axis.unit
            if unit is None:
                unit = ""
            try:
                position = axis.position
            except Exception:
                log_debug(self, "Error while reading position", exc_info=True)
                position = "!ERR"
            return [
                f"{axis.name:<20}",
                f"{axis.axis_rounder(position)} {unit}",
                f"{axis.offset:0.5f}",
                f"{axis.sign}",
            ]

        result = f"{type(self).__name__}: {self.name}"

        if self.reals != []:
            table = [["Reals", "position", "offset", "sign"]]
            for a in self.reals:
                table.append(format_axis(a))
            result += "\n\n"
            result += tabulate.tabulate(
                table, headers="firstrow", disable_numparse=True, tablefmt="simple"
            )

        if self.params != []:
            table = [["Params", "position", "offset", "sign"]]
            for a in self.params:
                table.append(format_axis(a))
            result += "\n\n"
            result += tabulate.tabulate(
                table, headers="firstrow", disable_numparse=True, tablefmt="simple"
            )

        if self.pseudos != []:
            table = [["Pseudos", "position", "offset", "sign"]]
            for a in self.pseudos:
                table.append(format_axis(a))
            result += "\n\n"
            result += tabulate.tabulate(
                table, headers="firstrow", disable_numparse=True, tablefmt="simple"
            )

        return result


def get_real_axes(*axes, depth=-1):
    """Return real axes from given axis objects

    Arguments:
       - axes (positional): list of axes to get real motors from
       - depth: how many levels to go down the controllers (default: -1, unlimited)
    """
    if depth == -1:
        depth = math.inf
    if depth == 0:
        return []
    real_axes = []
    for axis in axes:
        if isinstance(axis.controller, CalcController):
            real_axes += axis.controller.reals
            real_axes += get_real_axes(*axis.controller.reals, depth=depth - 1)
        else:
            real_axes.append(axis)
    return list(dict.fromkeys(real_axes))
