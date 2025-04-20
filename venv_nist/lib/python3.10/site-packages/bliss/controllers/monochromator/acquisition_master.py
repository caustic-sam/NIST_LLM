# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

"""
???
"""

import numpy
import gevent
import time

from bliss.scanning.chain import AcquisitionMaster
from bliss.common.motor_group import Group
from bliss.controllers.motors.icepap import Icepap
from bliss.common.logtools import disable_print
from bliss.common.utils import (  # noqa: F401
    ColorTags,
    BOLD,
    GREEN,
    YELLOW,
    BLUE,
    RED,
    ORANGE,
)


class TrajectoryMonochromatorMasterBase(AcquisitionMaster):
    def __init__(
        self,
        mono,
        start_pos,
        stop_pos,
        npoints,
        nscans,
        trigger_type=AcquisitionMaster.SOFTWARE,
        backnforth=False,
        show_time=True,
        **keys,
    ):

        # Monochromator
        self.mono = mono

        # Scan parameters
        self.start_pos = start_pos
        self.stop_pos = stop_pos
        self.nb_iter = nscans
        self.backnforth = backnforth

        self.movables_params = {}

        # Motors
        self.traj_axis = self.mono._motors["trajectory"]
        self.traj_real_axis = None

        # Build list of motors which will move Theoretical energy
        # There is always a trajectory, at least for the virtual energy axis
        self.movables = [self.traj_axis]

        self.movables.extend(self.get_extra_movables())

        super().__init__(
            self.traj_axis, trigger_type=trigger_type, npoints=npoints, **keys
        )

        self._mot_group = Group(*self.movables)

        self._display_time = show_time

        self.spectrum_time = None
        self.return_time = 0
        self.setaccspeed_time = 0
        self.unsetaccspeed_time = 0
        self.gotostart_time = 0

        # Calculate parameters (speed/acc/start/stop) for all moving motors
        self.start_time = time.time()
        self.calc_param()

        self.start_movement = []
        for name in self.movables_params.keys():
            self.start_movement.append(self.movables_params[name]["axis"])
            self.start_movement.append(self.movables_params[name]["rstart"])

        self.end_movement = []
        for name in self.movables_params.keys():
            self.end_movement.append(self.movables_params[name]["axis"])
            self.end_movement.append(self.movables_params[name]["rstop"])

        self.move_start = []
        self.move_end = []
        for i in range(nscans):
            if self.backnforth:
                if i % 2 == 0:
                    self.move_start.append(self.start_movement)
                    self.move_end.append(self.end_movement)
                else:
                    self.move_start.append(self.end_movement)
                    self.move_end.append(self.start_movement)
            else:
                self.move_start.append(self.start_movement)
                self.move_end.append(self.end_movement)

        if self._display_time:
            print(RED(f"SCAN CALC PARAM: {time.time()-self.start_time}"))

    def get_extra_movables(self):
        return []

    def calc_param(self):
        raise NotImplementedError

    def __iter__(self):
        self.iter_index = 0
        while self.iter_index < self.nb_iter:
            self.goto_start = self.move_start[self.iter_index]
            self.goto_end = self.move_end[self.iter_index]
            yield self
            self.iter_index += 1

    def prepare(self):
        if self.iter_index > 0 and self.iter_index < self.nb_iter:
            if self.traj_real_axis is not None:
                if not self.backnforth:
                    # Move Monochromator "energy_tracker" motor to come back from previous scan ASAP
                    self.return_time = time.time()
                    self.traj_real_axis.move(self.start_pos)
                    self.return_time = time.time() - self.return_time
        # Move to start position
        self.gotostart_time = time.time()
        self._mot_group.move(*self.goto_start)
        self.gotostart_time = time.time() - self.gotostart_time

    def start(self):
        if self.parent:
            return
        self.trigger()

    def trigger(self):

        if self.nb_iter > 1:
            print(BLUE(f"\n\nStarting Scan #{self.iter_index+1}/{self.nb_iter}\n"))

        if self.spectrum_time is not None and self._display_time:
            total_time = time.time() - self.spectrum_time
            acc_time = self.movables_params[self.traj_axis.name]["acct"]
            dead_time = (
                total_time
                - self.gotostart_time
                - self.setaccspeed_time
                - 2 * acc_time
                - self.scan_time
                - self.unsetaccspeed_time
                - self.return_time
            )
            print(GREEN(f"TOTAL TIME      : {total_time}"))
            print(GREEN(f"GO TO START     : {self.gotostart_time:.3f}"))
            print(GREEN(f"SET ACC/SPEED   : {self.setaccspeed_time:.3f}"))
            print(GREEN(f"ACCELERATION    : {acc_time:.3f}"))
            print(GREEN(f"SCAN TIME       : {self.scan_time}"))
            print(GREEN(f"UNSET ACC/SPEED : {self.unsetaccspeed_time:.3f}"))
            print(GREEN(f"RETURN TIME     : {self.return_time:.3f}"))
            print(GREEN(f"DEAD TIME       : {dead_time:.3f}"))
            print("\n")
        self.spectrum_time = time.time()

        try:
            # Set speed and acceleration for concerned motors
            self.setaccspeed_time = time.time()
            if not self.backnforth or self.iter_index == 0:
                self.set_speed_acc()
            self.setaccspeed_time = time.time() - self.setaccspeed_time

            # Move to end position
            if self.mono._external_start:
                for axis in self.movables:
                    axis.external_start = True
                self._mot_group.move(*self.goto_end, wait=False)
                gevent.sleep(0.1)
                self.mono._trigmove.pulse()
                self._mot_group.wait_move()
                for axis in self.movables:
                    axis.external_start = False
            else:
                self._mot_group.move(*self.goto_end)

        finally:
            self.unsetaccspeed_time = time.time()
            if self.mono._external_start:
                for axis in self.movables:
                    axis.external_start = False
            if not self.backnforth or self.iter_index == self.nb_iter - 1:
                self.unset_speed_acc()
            self.unsetaccspeed_time = time.time() - self.unsetaccspeed_time

    def stop(self):
        pass

    def unset_speed_acc(self):
        with disable_print():
            for key in self.movables_params:
                params = self.movables_params[key]
                params["axis"].wait_move()

                # WARNING: Check if this loop is still needed
                #          It may add 100ms ....!!!
                while params["axis"].state.MOVING:
                    gevent.sleep(0.1)

                params["axis"].velocity = params["vel_old"]
                params["axis"].acceleration = params["acc_old"]

    def set_speed_acc(self):
        with disable_print():
            for key in self.movables_params:
                params = self.movables_params[key]
                params["axis"].wait_move()
                params["axis"].velocity = params["vel"]
                params["axis"].acceleration = params["acc"]
                params["acc_set"] = params["axis"].acceleration
                params["vel_set"] = params["axis"].velocity
                self._check_speed(params["axis"])

    # WARNING: to be rewritten: what to do if speed is not well set???
    def _check_speed(self, axis):
        real_velocity = axis.velocity
        if self.velocity != real_velocity:
            if hasattr(axis, "tracking"):
                track_start = axis.tracking.energy2tracker(self.start_pos)
                track_stop = axis.tracking.energy2tracker(self.stop_pos)

                new_scan_time = numpy.abs(track_start - track_stop) / real_velocity
                new_int_time = new_scan_time / self.npoints

                print(GREEN(f'"{axis.name}" Optimal Int. Time (s): {new_int_time}'))


class TrajectoryEnergyTrackerMaster(TrajectoryMonochromatorMasterBase):
    def __init__(
        self,
        mono,
        Estart,
        Estop,
        npoints,
        time_per_point,
        nscans,
        trajectory_mode,  # energy/bragg/undulator
        undulator_master=None,
        trigger_type=AcquisitionMaster.SOFTWARE,
        backnforth=False,
        show_time=False,
        **keys,
    ):
        self.time_per_point = time_per_point
        self.scan_time = float(npoints * self.time_per_point)

        self.trajectory_mode = trajectory_mode
        if self.trajectory_mode == "UNDULATOR":
            if undulator_master is None:
                raise RuntimeError("No undulator master specified")
            scanning_mode = undulator_master.tracking.scanning_mode
            if scanning_mode != "MOVING":
                raise RuntimeError(
                    f"Wrong scanning mode for undulator master: {scanning_mode}"
                )
            self.tracker_master_axis = undulator_master
        else:
            self.tracker_master_axis = None

        self.energy_undershoot = 0.0
        if mono._motors["virtual_energy"] is not None:
            self.energy_undershoot = (
                100.0 / mono._motors["virtual_energy"].steps_per_unit
            )
        if Estart < Estop:
            self.undershoot_start_pos = Estart - self.energy_undershoot
            self.undershoot_stop_pos = Estop + self.energy_undershoot
        else:
            self.energy_start_pos = Estart + self.energy_undershoot
            self.energy_stop_pos = Estop - self.energy_undershoot

        super().__init__(
            mono,
            Estart,
            Estop,
            npoints,
            nscans,
            trigger_type=trigger_type,
            backnforth=backnforth,
            show_time=show_time,
            **keys,
        )

        self.traj_real_axis = self.mono._motors["energy_tracker"]

    def get_extra_movables(self):
        extra_movable = []
        # Add undulator master axis if trajectory mode is undulator
        if self.trajectory_mode == "UNDULATOR":
            extra_movable.append(self.tracker_master_axis)
        # Add trackers without trajectory.
        # register them for start/stop/speed/acc calculations
        self.tracked_list = []
        self.fix_tracked_list = []
        if self.mono._has_tracking:
            for mot in self.mono.tracking._motors.values():
                # tracker_master_axis is added independantly
                if mot != self.tracker_master_axis:
                    # user asked to track this motor
                    if mot.tracking.state:
                        # if the motor is an icepap it has beean already
                        # added in the trajectory motor
                        # Otherwise, the start and the stop position are
                        # calculated from the start/stop energy and a
                        # cst speed is apply to the tracker axis
                        scanning_mode = mot.tracking.scanning_mode
                        if scanning_mode != "MOVING":
                            self.fix_tracked_list.append(mot)
                        else:
                            if not isinstance(mot.controller, Icepap):
                                extra_movable.append(mot)
                                self.tracked_list.append(mot)
        return extra_movable

    def calc_param(self):

        # Find acceleration time for all motors concerned in the scan
        self.acc_time = {}

        if self.trajectory_mode == "ENERGY":
            # Energy Trajectory
            energy_undershoot_start = numpy.fabs(
                self.start_pos - self.undershoot_start_pos
            )
            energy_undershoot_stop = numpy.fabs(
                self.stop_pos - self.undershoot_stop_pos
            )
            self.acc_time[self.traj_axis.name] = self.get_traj_acct(
                self.start_pos,
                self.stop_pos,
                energy_undershoot_start,
                energy_undershoot_stop,
            )

        elif self.trajectory_mode == "BRAGG":

            # Bragg Trajectory
            # WARNING: bragg start/stop position should maybe be
            #          calculated taking offset into account
            bragg_start = self.mono.energy2bragg(self.start_pos)
            undershoot_bragg_start = self.mono.energy2bragg(self.undershoot_start_pos)
            bragg_undershoot_start = numpy.fabs(bragg_start - undershoot_bragg_start)

            bragg_stop = self.mono.energy2bragg(self.stop_pos)
            undershoot_bragg_stop = self.mono.energy2bragg(self.undershoot_stop_pos)
            bragg_undershoot_stop = numpy.fabs(bragg_stop - undershoot_bragg_stop)

            self.acc_time[self.traj_axis.name] = self.get_traj_acct(
                bragg_start, bragg_stop, bragg_undershoot_start, bragg_undershoot_stop
            )

        elif self.trajectory_mode == "UNDULATOR":

            # Calculate start and stop position for undulator_master
            undu_start = self.tracker_master_axis.tracking.energy2tracker(
                self.start_pos
            )
            undershoot_undu_start = self.tracker_master_axis.tracking.energy2tracker(
                self.undershoot_start_pos
            )
            undu_undershoot_start = numpy.fabs(undu_start - undershoot_undu_start)

            undu_stop = self.tracker_master_axis.tracking.energy2tracker(self.stop_pos)
            undershoot_undu_stop = self.tracker_master_axis.tracking.energy2tracker(
                self.undershoot_stop_pos
            )
            undu_undershoot_stop = numpy.fabs(undu_stop - undershoot_undu_stop)

            self.acc_time[self.traj_axis.name] = self.get_traj_acct(
                undu_start, undu_stop, undu_undershoot_start, undu_undershoot_stop
            )
            self.acc_time[self.tracker_master_axis.name] = self.get_mot_acct(
                self.tracker_master_axis,
                undu_start,
                undu_stop,
                undu_undershoot_start,
                undu_undershoot_stop,
            )

        else:
            raise RuntimeError(f'Trajectory mode "{self.trajectory_mode}" unknown')

        # Calculate acct for tracked motors without trajectory facility
        if self.tracked_list is not None:
            for axis in self.tracked_list:
                start_pos = axis.tracking.energy2tracker(self.start_pos)
                undershoot_start_pos = axis.tracking.energy2tracker(
                    self.undershoot_start_pos
                )
                undershoot_start = numpy.fabs(start_pos - undershoot_start_pos)

                stop_pos = axis.tracking.energy2tracker(self.stop_pos)
                undershoot_stop_pos = axis.tracking.energy2tracker(
                    self.undershoot_stop_pos
                )
                undershoot_stop = numpy.fabs(stop_pos - undershoot_stop_pos)

                self.acc_time[axis.name] = self.get_mot_acct(
                    axis,
                    start_pos,
                    stop_pos,
                    undershoot_start,
                    undershoot_stop,
                )

        # Find Maximum acceleration time among all scanned motors
        acct = self.acc_time[self.traj_axis.name]
        if self.trajectory_mode == "UNDULATOR":
            if acct < self.acc_time[self.tracker_master_axis.name]:
                acct = self.acc_time[self.tracker_master_axis.name]
        if self.tracked_list is not None:
            for axis in self.tracked_list:
                if acct < self.acc_time[axis.name]:
                    acct = self.acc_time[axis.name]

        # Fill movables parameters with common acct
        self.fill_movable(self.traj_axis, acct)
        if self.trajectory_mode == "UNDULATOR":
            self.fill_movable(self.tracker_master_axis, acct)
        if self.tracked_list is not None:
            for axis in self.tracked_list:
                self.fill_movable(axis, acct)
                if self.movables_params[axis.name]["acc"] < 0.5:
                    self.movables_params[axis.name]["acc"] = 0.5

        # Check if speed/acc are valid for trajectory axis
        vel_max = self.traj_axis._get_max_velocity()
        vel = self.movables_params[self.traj_axis.name]["vel"]
        if vel > vel_max:
            raise RuntimeError(
                RED(f"Velocity not valid for trajectory: {vel} (max: {vel_max})")
            )
        self.undershoot = self.movables_params[self.traj_axis.name]["accd"]
        self.velocity = self.movables_params[self.traj_axis.name]["vel"]

    def fill_movable(self, axis, acct):
        params = self.movables_params[axis.name]
        params["acct"] = acct
        params["acc"] = params["vel"] / params["acct"]
        params["accd"] = params["vel"] * params["acct"] / 2.0
        if params["start"] < params["stop"]:
            params["rstart"] = (
                params["start"] - params["accd"] - params["start_undershoot"]
            )
            params["rstop"] = (
                params["stop"] + params["accd"] + params["stop_undershoot"]
            )
        else:
            params["rstart"] = (
                params["start"] + params["accd"] + params["start_undershoot"]
            )
            params["rstop"] = (
                params["stop"] - params["accd"] - params["stop_undershoot"]
            )

    def get_mot_acct(self, axis, startv, stopv, start_undershoot, stop_undershoot):
        vel = abs(stopv - startv) / self.scan_time
        self.movables_params[axis.name] = {
            "axis": axis,
            "vel": vel,
            "start": startv,
            "stop": stopv,
            "vel_old": axis.velocity,
            "acc_old": axis.acceleration,
            "start_undershoot": start_undershoot,
            "stop_undershoot": stop_undershoot,
        }
        min_acct = axis.acctime
        max_vel = axis.velocity
        return min_acct * vel / max_vel

    def get_traj_acct(self, startv, stopv, start_undershoot, stop_undershoot):
        vel = abs(stopv - startv) / self.scan_time
        self.movables_params[self.traj_axis.name] = {
            "axis": self.traj_axis,
            "vel": vel,
            "start": startv,
            "stop": stopv,
            "vel_old": self.traj_axis.velocity,
            "acc_old": self.traj_axis.acceleration,
            "start_undershoot": start_undershoot,
            "stop_undershoot": stop_undershoot,
        }
        min_acct = self.traj_axis._get_min_acceleration_time()
        max_vel = self.traj_axis._get_max_velocity()
        return min_acct * vel / max_vel
