# This file is part of the Lima2 project
#
# Copyright (c) 2020-2024 Beamline Control Unit, ESRF
# Distributed under the MIT license. See LICENSE for more info.

"""Aggregation of progress counters in multi-receiver topologies."""

from dataclasses import dataclass

from beartype import beartype


@beartype
@dataclass
class SingleCounter:
    """Progress counter reported by a single receiver.

    The Client is reponsible for aggregating SingleCounters into a ProgressCounter.
    """

    # Counter label (e.g. nb_frames_acquired)
    name: str
    value: int
    # Holds the name of the device that generated this counter
    source: str


@beartype
@dataclass
class ProgressCounter:
    """Final progress counter after aggregation of one or more SingleCounters by `aggregate()`."""

    name: str
    counters: list[SingleCounter]

    @classmethod
    def from_single(cls, single_counter: SingleCounter) -> "ProgressCounter":
        """Construct from a single SingleCounter"""
        return ProgressCounter(name=single_counter.name, counters=[single_counter])

    @property
    def sum(self) -> int:
        return sum([c.value for c in self.counters])

    @property
    def max(self) -> int:
        return max([c.value for c in self.counters])

    @property
    def min(self) -> int:
        return min([c.value for c in self.counters])

    @property
    def avg(self) -> float:
        return self.sum / len(self.counters)

    def __repr__(self) -> str:
        return f"{self.name}: {self.sum}/{self.min}/{self.max}/{self.avg} (total/min/max/avg)"


def aggregate(single_counters: list[SingleCounter]) -> ProgressCounter:
    """Transform a list of counters (one per receiver) into an aggregated ProgressCounter"""
    name = single_counters[0].name
    return ProgressCounter(
        name=name,
        counters=single_counters,
    )
