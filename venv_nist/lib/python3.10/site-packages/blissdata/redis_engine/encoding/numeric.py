# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.
from . import EncodedBatch, StreamEncoder

import sys
import msgpack
import numpy as np


class NumericStreamEncoder(StreamEncoder):
    def __init__(self, dtype, shape=()):
        """
        dtype: Any numeric type (including boolean) from python built-in or numpy.
        shape: Shape for a single point, e.g. (32,) for a 1D sensor producing 32 values long spectrums.
        Encoder will be able to encode single spectrums (32,) or many at once (n, 32,).

        Data points can have dynamic dimensions (like a variable length spectrum).
        Use -1 for the variable dimension, for example a dynamic 1D spectrum will be (-1,).
        In that case, the output will embed shape information for the decoder.
        """
        self._dtype = np.dtype(dtype)
        self._point_shape = tuple(shape)
        self._batch_shape = (-1,) + self._point_shape
        self._embed_shape_in_payload = -1 in self._point_shape

        # accept bool, int, uint, float, complex (and all numpy variants)
        if self._dtype.kind not in "biufc":
            raise ValueError(
                f"Expect dtype in [bool, int, uint, float, complex], received {dtype}"
            )

    @property
    def dtype(self):
        return self._dtype

    @property
    def shape(self):
        return self._point_shape

    def info(self):
        return {
            "type": "numeric",
            "dtype_str": self._dtype.str,
            "shape": self._point_shape,
        }

    @classmethod
    def from_info(cls, info):
        assert info["type"] == "numeric"
        return cls(info["dtype_str"], info["shape"])

    def encode(self, data):
        """If data points are vectors, the number of points should be the first dimension.
        For example n-points in 3D space would be:
            [
                [x1, y1, z1],
                [x2, y2, z2],
                ...
                [xn, yn, zn],
            ]
        """
        data = np.asarray(data)
        if data.size == 0:
            raise ValueError("Cannot encode empty data")

        if data.dtype.str != self._dtype.str:
            if (
                data.dtype.byteorder != self._dtype.byteorder
                and not data.dtype.byteorder.startswith("|")
                and not self._dtype.byteorder.startswith("|")
            ):
                endian_names = {
                    "=": sys.byteorder,
                    ">": "big",
                    "<": "little",
                    "|": "no",
                }
                raise TypeError(
                    f"Expected {endian_names[self._dtype.byteorder]} endian, "
                    f"received {endian_names[data.dtype.byteorder]} endian (see numpy.dtype.byteorder doc)"
                )
            else:
                raise TypeError(
                    f"Expected numpy.{self._dtype}, received numpy.{data.dtype}"
                )

        # ensure data has one more dimension than the point shape
        if data.ndim == len(self._point_shape) + 1:
            batch = data
        elif data.ndim == len(self._point_shape):
            batch = data[np.newaxis, ...]
        else:
            raise ValueError(
                f"Expected shape {self._point_shape} or {self._batch_shape}, but received {data.shape}"
            )

        # match shape components, except for free ones (-1 values)
        for expected, actual in zip(self._point_shape, batch.shape[1:]):
            if expected not in [-1, actual]:
                raise ValueError(
                    f"Expected shape {self._point_shape} or {self._batch_shape}, but received {data.shape}"
                )

        # .tobytes() always produces 'C' order data, no matter the actual order in memory
        if self._embed_shape_in_payload:
            buffer = msgpack.packb((batch.shape[1:], batch.tobytes()))
            return EncodedBatch(buffer, len=batch.shape[0])
        else:
            return EncodedBatch(batch.tobytes(), len=batch.shape[0])

    def decode(self, batches):
        if isinstance(batches, EncodedBatch):
            batches = (batches,)

        if self._embed_shape_in_payload:
            points = []
            for batch in batches:
                shape, buffer = msgpack.unpackb(batch.payload)
                points.extend(
                    np.frombuffer(buffer, dtype=self._dtype).reshape(
                        (batch.len, *shape)
                    )
                )
            try:
                return np.asarray(points)
            except ValueError:
                # Point shapes are not homogeneous, return a list of distinct np.arrays
                return points
        else:
            buffer = bytearray()
            for batch in batches:
                buffer.extend(batch.payload)
            return np.frombuffer(buffer, dtype=self._dtype).reshape(
                (-1,) + self._point_shape
            )
