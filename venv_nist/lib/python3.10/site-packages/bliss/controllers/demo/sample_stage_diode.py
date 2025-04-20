from __future__ import annotations

import os
import fabio
import numpy
from bliss.common.counter import SoftCounter, SamplingMode
from bliss.common.protocols import CounterContainer
from bliss.controllers.counter import counter_namespace
from bliss.common.image_tools import file_to_pil
from bliss.common.utils import autocomplete_property
from bliss.common.expand import expandvars


class SampleStageDiode(CounterContainer):
    """Controller with a diode emitting a value from an image.

    The selected pixel is based on the position of 2 axis dial position.

    The selected pixel is selected using the dial for the 2 axis.
    - This first axis is the `y`
    - The second axis is the `x`

    The is 2 ways to localize the data in front of the motors.

    - With a `min_corner` and `max_corner`, to define the corner of the image
      with the motor coordinates.
    - With a `scale`, which is the sample pixel size: the motor size
      represented by a pixel width. In this case the center of the image is at
      motor position 0,0.

    A `noise` can be defined, which is a random value added to the resulting
    signal.Default is 10.

    If `poissonian` is set to true, a poisonnian random filter is apply to the
    resulting signal.
    """

    def __init__(self, name, config):
        self._name = name
        self._config = config
        self._filename = config.get("data_filename")
        self._img: numpy.NDArray | None = None
        self.axis1 = config["axis1"]
        self.axis2 = config["axis2"]
        self.noise = config.get("noise", 10)
        self.is_poissonian = config.get("poissonian", False)

        self.counter = SoftCounter(
            self, self._read_signal, name="signal", mode=SamplingMode.SINGLE
        )

    def _prepare_data(self):
        filename = self._filename
        config = self._config
        if filename:
            self._img = self._load_data(filename)
        else:
            self._img = numpy.array(config["data"])
        self.dim1, self.dim2 = self._img.shape

        if "min_corner" in config and "max_corner" in config:
            min_corner = config.get("min_corner")
            max_corner = config.get("max_corner")

            def compute_scale_offset(vmin, vmax, dim):
                scale = abs(vmax - vmin) / dim
                vmean = (vmin + vmax) * 0.5
                offset = dim * 0.5 - vmean / scale
                return scale, offset

            self.scale1, self.offset1 = compute_scale_offset(
                min_corner[0], max_corner[0], self.dim1
            )
            self.scale2, self.offset2 = compute_scale_offset(
                min_corner[1], max_corner[1], self.dim2
            )
        else:
            self.scale1 = config.get("scale", 1)  # mm/px
            self.scale2 = config.get("scale", 1)  # mm/px
            self.offset1 = int(self.dim1 * 0.5)
            self.offset2 = int(self.dim2 * 0.5)

    def _load_data(self, filename):
        """Load the filename as a numpy array"""
        filename = expandvars(filename)
        if not os.path.isfile(filename):
            raise RuntimeError(f"Cannot find file {filename}")
        try:
            return fabio.open(filename).data
        except Exception:
            img = file_to_pil(filename)
            img = img.convert("L")
            return numpy.array(img)

    @autocomplete_property
    def counters(self):
        return counter_namespace([self.counter])

    def _read_signal(self):
        return self.read_signal_from_pos(self.axis1.dial, self.axis2.dial)

    def _axis_to_image(self, pos1: float, pos2: float) -> tuple[int, int]:
        """Convert the axis positions into image coordinate.

        The image coordinate is not clamped.
        """
        self._prepare_data()
        pos1 = int(pos1 / self.scale1 + self.offset1)
        pos2 = int(pos2 / self.scale2 + self.offset2)
        return pos1, pos2

    def read_signal_from_pos(self, pos1, pos2):
        pos1, pos2 = self._axis_to_image(pos1, pos2)
        if 0 <= pos1 < self.dim1 and 0 <= pos2 < self.dim2:
            signal = self._img[pos1, pos2]
        else:
            signal = 0

        if self.noise:
            signal = signal + numpy.random.rand() * self.noise
        if self.is_poissonian:
            signal = numpy.random.poisson(signal)
        return signal

    def show(self):
        """Use flint to display the projected image into the axis"""
        from bliss.common.plot import get_flint

        self._prepare_data()
        flint = get_flint()
        plot = flint.get_plot("image", unique_name=f"{self._name}_proj_img")
        plot.set_data(
            self._img,
            scale=(self.scale1, self.scale2),
            origin=(-self.offset1 * self.scale1, -self.offset2 * self.scale2),
        )
