import gevent
from bliss.controllers.motor import Controller
from bliss.common.axis import AxisState


class Sequencer(Controller):

    """A controller to serialize the motions of the axes referenced in its configuration.
    It ensures that declared axes cannot be moved in parallel.

    YAML configuration example:

      - class: Sequencer
        plugin: emotion
        axes:
          - name: sax
            axis: $ax

          - name: say
            axis: $ay

          - name: saz
            axis: $az

    'sax' is the serialized version of 'ax'
    'say' is the serialized version of 'ay'
    'saz' is the serialized version of 'az'

    example:
        => move(sax, 1, say, 2)
        'sax' moves first to 1.
        'say' starts moving to 2 only when 'sax' has reached its target position.
        'saz' cannot be moved while motion command is running.
    """

    def _get_linked(self, axis):
        return axis.config.get("axis")

    def _perform_move_task(self, *motion_list):
        for motion in motion_list:
            linked_axis = self._get_linked(motion.axis)
            linked_axis.move(motion.user_target_pos)

    def initialize(self):
        self._move_task = None

    def initialize_axis(self, axis):
        linked_axis = axis.config.get("axis")
        if linked_axis is None:
            raise RuntimeError(f"missing key 'axis 'in {axis.name} YAML configuration")

        axis.config.set("velocity", linked_axis.velocity)
        axis.config.set("acceleration", linked_axis.acceleration)
        axis.config.set("steps_per_unit", linked_axis.steps_per_unit)

    def prepare_all(self, *motion_list):
        raise NotImplementedError

    def prepare_move(self, motion):
        return

    def start_one(self, motion):
        raise NotImplementedError

    def start_all(self, *motion_list):
        if self._move_task:
            raise RuntimeError(
                f"MotionSequencer is already running a motion with {[ax.name for ax in self._in_motion_axes]}"
            )

        self._in_motion_axes = [motion.axis for motion in motion_list]
        task = gevent.spawn(self._perform_move_task, *motion_list)
        task.name = f"Sequencer_perform_move_task_{self.name}"
        self._move_task = task

    def start_jog(self, axis, velocity, direction):
        raise NotImplementedError

    def stop(self, axis):
        if self._move_task and axis in self._in_motion_axes:
            self._move_task.kill()

    def stop_all(self, *motions):
        raise NotImplementedError

    def read_position(self, axis):
        return self._get_linked(axis).position

    def state(self, axis):
        if self._move_task and axis in self._in_motion_axes:
            return AxisState("MOVING")
        else:
            return AxisState("READY")

    def read_velocity(self, axis):
        return self._get_linked(axis).velocity

    def set_velocity(self, axis, velocity):
        self._get_linked(axis).velocity = velocity

    def read_acceleration(self, axis):
        return self._get_linked(axis).acceleration

    def set_acceleration(self, axis, acceleration):
        self._get_linked(axis).acceleration = acceleration

    def set_off(self, axis):
        pass

    def set_on(self, axis):
        pass
