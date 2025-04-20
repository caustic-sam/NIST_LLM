# This file is part of the Lima2 project
#
# Copyright (c) 2020-2024 Beamline Control Unit, ESRF
# Distributed under the MIT license. See LICENSE for more info.

"""Finite state machine definition for the detector.
"""

import logging
from enum import Enum, auto
from typing_extensions import Callable

import finite_state_machine as fsm
import gevent
import tango
from finite_state_machine import transition
from tango import EventType

# Create a logger
_logger = logging.getLogger(__name__)


class State(Enum):
    """The possible states of the Detector state machine."""

    # Enum values for '<enum 'State'>' must start at 0 and increment by 1.
    IDLE = 0
    PREPARING = auto()
    PREPARED = auto()
    STARTING = auto()
    RUNNING = auto()
    STOPPING = auto()
    ENDING = auto()
    CLOSING = auto()
    RESETTING = auto()
    FAULT = auto()
    UNKNOWN = auto()
    DISCONNECTED = auto()


# Conditions
def trigger_mode_software(fsm):
    # TODO
    return True


class StateMachine(fsm.StateMachine):
    """Detector Finite State Machine."""

    def __init__(self, ctrl: tango.DeviceProxy, recvs: list[tango.DeviceProxy]):
        """
        Construct a StateMachine that control the state of the detector.

        Args:
            ctrl: Control tango device
            recvs: list of Receiver tango devices
        """
        self._state = State.UNKNOWN
        self.ctrl = ctrl
        self.recvs = recvs
        self.__loggers: list[Callable] = []
        super().__init__()

        def on_acq_state_change(evt):
            if evt.err:
                if self._state != State.DISCONNECTED:
                    _logger.error(
                        f"Lost connection to device {evt.device.dev_name()}:\n"
                        + "\n".join([f"- {err.desc}" for err in evt.errors])
                        + "\n"
                    )
                    self._state = State.DISCONNECTED
                return

            AcqState = type(evt.device.acq_state)
            acq_state = AcqState(evt.attr_value.value)
            _logger.debug(f"on_acq_state {acq_state}")

            # TODO Check the global state of the detector according to the individual states
            if acq_state == AcqState.fault and self._state not in [
                State.FAULT,
                State.UNKNOWN,
            ]:
                _logger.error(
                    f"Device {evt.device.dev_name()} in FAULT state. Stopping all devices."
                )
                gevent.spawn(self.abort).join()

            if self.state == State.DISCONNECTED:
                _logger.info(
                    f"{evt.device.dev_name()} seems back up, trying to sync..."
                )
                # Calling self.sync() here outside of a greenlet causes a segfault
                # TODO(mdu) possibly the tango+gevent combination...
                gevent.spawn(self.sync).join()
                _logger.info(f"Current state: {self._state}")

        self.event_ids = {
            r: r.subscribe_event(
                "acq_state",
                EventType.CHANGE_EVENT,
                on_acq_state_change,
            )
            for r in self.devs
        }

    def sync(self):
        """Synchronize the local state with the server states.

        Raises if:
        - we cannot ping one of the devices
        - states are not identical across all devices

        Returns the synchronized state.
        """
        for device in [self.ctrl] + self.recvs:
            device.ping()
        states = [d.acq_state for d in [self.ctrl] + self.recvs]
        ctl_state = states[0]

        ServerState = type(ctl_state)

        if all(s in [ServerState.idle, ServerState.prepared] for s in states):
            # Tolerate a mix of prepared and idle devices. In this case,
            # the global state shall be IDLE (forcing user to prepare again).
            self.state = State.IDLE
        elif any(s == ServerState.fault for s in states):
            self.state = State.FAULT
        elif not all(s == ctl_state for s in states):
            # Otherwise, if states are divergent, sync cannot be done
            self._state = State.UNKNOWN
            raise RuntimeError(f"Cannot sync: inconsistent server states ({states})")
        else:
            map = {
                ServerState.idle: State.IDLE,
                ServerState.prepared: State.PREPARED,
                ServerState.running: State.RUNNING,
                ServerState.stopped: State.IDLE,
            }
            self.state = map[ctl_state]

        return self.state

    @property
    def devs(self):
        return [self.ctrl] + self.recvs

    @property
    def state(self) -> State:
        return self._state

    @state.setter
    def state(self, value: State) -> None:
        if self._state != value:
            self._state = value
            self.on_state_change(value)

    @transition(
        source=[State.IDLE, State.PREPARED],
        target=State.PREPARING,
        on_error=State.IDLE,
    )
    def prepare(self, uuid: str):
        def prepare():
            greenlets = [gevent.spawn(dev.Prepare, uuid) for dev in self.devs]
            gevent.joinall(greenlets, raise_error=True)

        greenlet = gevent.spawn(prepare)
        greenlet.link(self.prepare_end)
        return greenlet

    @transition(source=State.PREPARING, target=State.PREPARED, on_error=State.IDLE)
    def prepare_end(self, greenlet):
        greenlet.get()

    @transition(source=State.PREPARED, target=State.STARTING, on_error=State.FAULT)
    def start(self):
        def start():
            greenlets = [gevent.spawn(dev.Start) for dev in self.devs]
            gevent.joinall(greenlets, raise_error=True)

        self.__subscribe_events()
        greenlet = gevent.spawn(start)
        greenlet.link(self.start_end)
        return greenlet

    @transition(source=State.STARTING, target=State.RUNNING)
    def start_end(self, greenlet):
        greenlet.get()

    @transition(
        source=State.RUNNING, target=State.RUNNING, conditions=[trigger_mode_software]
    )
    def trigger(self):
        greenlet = gevent.spawn(self.ctrl.Trigger)
        greenlet.join()

    @transition(source=State.RUNNING, target=State.RUNNING)
    def trigger_end(self):
        pass

    @transition(source=State.RUNNING, target=State.STOPPING)
    def stop(self):
        def stop():
            greenlets = [gevent.spawn(dev.Stop) for dev in self.devs]
            gevent.joinall(greenlets, raise_error=True)

        greenlet = gevent.spawn(stop)
        greenlet.link(self.stop_end)
        return greenlet

    @transition(source=State.STOPPING, target=State.CLOSING)
    def stop_end(self, greenlet):
        greenlet.get()

        # Send close to control device only (recv close by themselves)
        greenlet = gevent.spawn(self.ctrl.Close)
        greenlet.link(self.close_end)

    @transition(source=State.RUNNING, target=State.CLOSING)
    def close(self):
        # Send close to control device only (recv close by themselves)
        greenlet = gevent.spawn(self.ctrl.Close)
        greenlet.link(self.close_end)

    @transition(source=State.CLOSING, target=State.IDLE)
    def close_end(self, greenlet):
        greenlet.get()

    @transition(source=State.FAULT, target=State.IDLE)
    def reset_acq(self):
        greenlets = [gevent.spawn(dev.Reset) for dev in self.devs]
        gevent.joinall(greenlets, raise_error=True)

    @transition(
        source=[
            State.IDLE,
            State.PREPARING,
            State.STARTING,
            State.RUNNING,
            State.STOPPING,
            State.ENDING,
        ],
        target=State.FAULT,
    )
    def abort(self):
        """Called when a device enters FAULT state."""
        self.ctrl.Stop()

        # Stop acquisition on receivers that are running
        greenlets = [
            gevent.spawn(recv.Stop)
            for recv in self.recvs
            if recv.acq_state.name == "running"
        ]
        gevent.joinall(greenlets, raise_error=False)

        self.ctrl.Close()

    def __subscribe_events(self):
        nb_receivers = len(self.recvs)
        nb_frames_xferreds = []
        event_ids = {}

        def on_end_acq(evt):
            nonlocal nb_frames_xferreds
            nonlocal event_ids

            # Filter spurious error events (if the device is restarted)
            if evt.err:
                _logger.error(
                    f"Error on {evt.device.dev_name()} 'nb_frames_xferred' event:\n"
                    + "\n".join([f"- {err.desc}" for err in evt.errors])
                    + "\n"
                )
                for r, e in event_ids.items():
                    r.unsubscribe_event(e)
                return

            nb_frames_xferreds.append(evt)

            if len(nb_frames_xferreds) == nb_receivers:
                for r, e in event_ids.items():
                    r.unsubscribe_event(e)

                _logger.debug(f"on_end_acq while {self.state}")
                if self.state == State.RUNNING:
                    gevent.spawn(self.close)

                nb_frames_xferreds = []

        # On server side, data_ready_event is pushed by each receiver
        # at the end of the acquisition loop.
        event_ids = {
            r: r.subscribe_event(
                "nb_frames_xferred",
                EventType.DATA_READY_EVENT,
                on_end_acq,
            )
            for r in self.recvs
        }

    def register_transition_logger(self, logger):
        """
        Register a logger to be notified on transition.

        Args:
            logger: A callback with the following signature.

        Example:
            def on_transition(source, target):
                print(f"transition from {source} to {target}")

            fsm.register_transition_logger(on_transition)
        """
        self.__loggers.append(logger)

    def unregister_transition_logger(self, logger):
        """
        Unregister a given transition logger function.

        Args:
            logger: A callback to unregister.
        """
        if logger in self.__loggers:
            self.__loggers.remove(logger)

    def on_state_change(self, state):
        _logger.debug(f"on_state_change {state}")
        greenlets = [gevent.spawn(logger, state) for logger in self.__loggers]
        gevent.joinall(greenlets, raise_error=True)
