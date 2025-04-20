# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.
from abc import ABC, abstractmethod
from collections.abc import Iterator, Mapping
from dataclasses import dataclass

from beartype import beartype


@beartype  # TODO remove runtime checks after blissdata transition
@dataclass
class EncodedBatch:
    payload: bytes
    len: int = 1

    # TODO remove runtime checks after blissdata transition
    def __post_init__(self):
        if self.len < 1:
            raise ValueError("Field 'len' cannot be less than one.")

    def todict(self):
        if self.len > 1:
            return self.__dict__
        else:
            return {"payload": self.payload}


class StreamEncoder(ABC):
    @abstractmethod
    def info(self) -> Mapping:
        raise NotImplementedError

    @classmethod
    @abstractmethod
    def from_info(cls, info: dict):
        raise NotImplementedError

    @abstractmethod
    def encode(self, data) -> EncodedBatch:
        raise NotImplementedError

    @abstractmethod
    def decode(self, batches: Iterator[EncodedBatch]):
        raise NotImplementedError
