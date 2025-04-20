from __future__ import annotations
import time
from tango.server import run
from tango.server import Device
from tango.server import attribute
from tango.server import command
from tango import DevState


class DummyAxis(Device):
    """
    Dummy tango server exposing an axis.

    The motion is started by changing the `position`.

    The simulation have a linear behaviour.

    An acceleration is exposed as mock. It is not used for
    the simulation.
    """

    def __init__(self, *args, **kwargs):
        Device.__init__(self, *args, **kwargs)
        self._position = 1.4078913
        self._velocity = 5.0
        self._acceleration = 125.0
        self._dir = 1
        self._target: float | None = None
        self._start_time = None
        self.poll_attribute("state", 100)
        self.poll_attribute("position", 100)
        self.set_state(DevState.ON)

    def _move_to(self, target: float):
        if self._position == target:
            return
        self.set_state(DevState.MOVING)
        self._target = target
        self._start_time = time.time()
        if self._position < target:
            self._dir = 1
        else:
            self._dir = -1

    def _stop(self):
        self._target = None
        self._start_time = None
        self.set_state(DevState.ON)

    def _update_pos(self):
        if self._start_time is None:
            return
        assert self._target is not None
        prev_time = self._start_time
        self._start_time = time.time()
        duration = self._start_time - prev_time
        new_pos = self._position + duration * self._velocity * self._dir
        if (new_pos - self._target) * self._dir > 0:
            self._position = self._target
            self._stop()
            return
        self._position = new_pos

    @command
    def On(self):
        self.set_state(DevState.ON)

    @command
    def Off(self):
        self.set_state(DevState.OFF)

    @command
    def Abort(self):
        if self._start_time is None:
            return
        self._update_pos()
        self._stop()

    @attribute(
        dtype=float,
        label="Position",
        unit="mm",
        min_value=-100,
        max_value=100,
        rel_change="0.001",
    )
    def position(self):
        self._update_pos()
        return self._position

    @position.setter
    def setPosition(self, value: float):
        self._update_pos()
        self._move_to(value)

    @attribute(
        dtype=float,
        label="Velocity",
        unit="mm/s",
        min_value=-100,
        max_value=100,
        rel_change="0.001",
    )
    def velocity(self):
        return self._velocity

    @velocity.setter
    def setVelocity(self, value: float):
        self._velocity = value

    @attribute(
        dtype=float,
        label="Acceleration",
        unit="mm/s2",
        min_value=-10000,
        max_value=10000,
        rel_change="0.001",
    )
    def acceleration(self):
        return self._acceleration

    @acceleration.setter
    def setAcceleration(self, value: float):
        self._acceleration = value

    @attribute(
        dtype=float,
        label="Acceleration Time",
        unit="s",
        min_value=-10000,
        max_value=10000,
        rel_change="0.001",
    )
    def acceleration_time(self):
        return self._velocity / self._acceleration

    @acceleration_time.setter
    def setAccelerationTime(self, value: float):
        self._acceleration = self._velocity / value


if __name__ == "__main__":
    run((DummyAxis,))
