# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.
import math
import numpy as np
from packaging.version import Version
from functools import cached_property
import os
import uuid
import logging
from dataclasses import dataclass

from blissdata.redis_engine.exceptions import (
    IndexNoMoreThereError,
    IndexNotYetThereError,
)
from . import image_utils

try:
    from gevent.monkey import is_anything_patched
except ImportError:
    use_gevent = False
else:
    use_gevent = is_anything_patched()

try:
    if use_gevent:
        from tango.gevent import DeviceProxy
    else:
        from tango import DeviceProxy
    from tango import DevSource
except ImportError:
    DeviceProxy = None
    DevSource = None

# Create a logger
_logger = logging.getLogger(__name__)


def acquisition_on_server(data_store, server_url):
    return int(data_store._redis.get(server_url).decode())


def prepare_next_lima_acquisition(data_store, server_url):
    pipe = data_store._redis.pipeline(transaction=True)
    # when the key doesn't exist, set it to -1 before .incr()
    pipe.setnx(server_url, -1)
    pipe.incr(server_url)
    return pipe.execute()[1]


@dataclass
class ImageReference:
    format: str
    file_path: str
    data_path: str | None
    index: int


class LimaClient:
    PROTOCOL_VERSION = 1

    def __init__(self, data_store, **lima_info):
        protocol = lima_info["protocol_version"]
        if self.PROTOCOL_VERSION != protocol:
            raise Exception(
                f"{type(self).__name__} supports lima json protocol {self.PROTOCOL_VERSION}, found version {protocol}"
            )

        self._data_store = data_store
        self._proxy = None
        self._server_url = lima_info["server_url"]
        self._buffer_max_number = lima_info["buffer_max_number"]
        self._frames_per_acquisition = lima_info["frame_per_acquisition"]
        self._acquisition_offset = lima_info["acquisition_offset"]
        self._acq_trigger_mode: str | None = None

        self._saved = "file_path" in lima_info
        if self._saved:
            self._file_offset = lima_info["file_offset"]
            self._frames_per_file = lima_info["frame_per_file"]
            self._file_path = lima_info["file_path"]
            self._data_path = lima_info["data_path"]
            self._file_format = lima_info["file_format"]
            self._files_per_acquisition = math.ceil(
                self._frames_per_acquisition / self._frames_per_file
            )
        else:
            self._file_format = None
            self._frames_per_file = 0

        self._last_index = -1
        self._last_index_saved = -1
        self._last_index_saved_and_closed = -1
        self._last_acq_seen_on_server = -1

    @cached_property
    def _features_last_index(self) -> bool:
        """
        True if the index -1 is supported to retrieve image.

        For now it is not always featured, depending on setup
        and Lima version.

        See https://gitlab.esrf.fr/bliss/bliss/-/issues/4135
        """
        if self.proxy.acq_mode != "ACCUMULATION":
            return True
        version = Version(self.proxy.lima_version)
        return version >= Version("1.10.0")

    @property
    def acq_trigger_mode(self) -> str:
        """Returns the cached acq trigger mode of this detector"""
        if self._acq_trigger_mode is not None:
            return self._acq_trigger_mode
        acq_trigger_mode = self.proxy.acq_trigger_mode
        self._acq_trigger_mode = acq_trigger_mode
        return acq_trigger_mode

    @property
    def file_format(self):
        return self._file_format

    def __len__(self):
        return self._last_index + 1

    def update(self, last_index, last_index_saved):
        self._last_index = max(self._last_index, last_index)
        self._last_index_saved = max(self._last_index_saved, last_index_saved)
        if self._saved:
            self._last_index_saved_and_closed = self._last_readable_index()

    def _decompose_frame_id(self, frame_id):
        """Decompose a frame_id (scan level index) into its sub-levels.

        Example with the following parameters:
            Frames per file        = 3
            Frames per acquisition = 7
            Files  per acquisition = 3  <- ceil(Frames per acquisition / Frames per file)

        frame_id      |  0  1  2 |  3  4  5 |  6  .  . |  7  8  9 | 10 11 12 | 13  .  . | ...
        --------------------------------------------------------------------------------
        acq_in_scan   |                0               |                1               | ...
        file_in_acq   |     0    |     1    |     2    |     0    |     1    |     2    | ...
        frame_in_file |  0  1  2 |  0  1  2 |  0  .  . |  0  1  2 |  0  1  2 |  0  .  . | ...
        """
        try:
            acq_in_scan, frame_in_acq = divmod(frame_id, self._frames_per_acquisition)
        except ZeroDivisionError:
            acq_in_scan, frame_in_acq = 0, frame_id
        try:
            file_in_acq, frame_in_file = divmod(frame_in_acq, self._frames_per_file)
        except ZeroDivisionError:
            file_in_acq, frame_in_file = 0, frame_in_acq
        return acq_in_scan, file_in_acq, frame_in_file

    def _last_readable_index(self):
        """Return the last frame index that can be read from a closed file, assuming a
        file is closed when at least one frame is saved into the next one.
        NOTE: This is trickier than a modulo as an acquisition may not be an exact
        multiple of the file length (see self._decompose_frame_id).
        """
        acq_in_scan, file_in_acq, _ = self._decompose_frame_id(self._last_index_saved)
        return (
            acq_in_scan * self._frames_per_acquisition
            + file_in_acq * self._frames_per_file
            - 1
        )

    def _is_acquisition_still_on_server(self, acq_id):
        if acq_id < self._last_acq_seen_on_server:
            # We already know this acquisition is outdated
            return False
        self._last_acq_seen_on_server = acquisition_on_server(
            self._data_store, self._server_url
        )
        return acq_id == self._last_acq_seen_on_server

    @property
    def proxy(self):
        """Lazy connection to the tango device, because a lima client may never use it."""
        if self._proxy is None:
            if DeviceProxy is None:
                raise RuntimeError("requires 'pytango' to be installed")
            self._proxy = DeviceProxy(self._server_url)
            self._proxy.set_source(DevSource.DEV)
        return self._proxy

    def get_last_live_image(self) -> image_utils.ImageData:
        """Returns the last frame from the memory buffer of Lima.

        This code does not check if the frame is part of the actual scan.

        Raises:
            NoImageAvailable: when the lima server buffer does not yet contain any frame
            ImageFormatNotSupported: when the retrieved data is not supported
        """
        if self._features_last_index:
            data = image_utils.image_from_server(self.proxy, -1)
        else:
            # This could be dropped with Lima 1.10
            last_index = self.proxy.last_image_ready
            data = image_utils.image_from_server(self.proxy, last_index)

        def normalize_frame_id(frame_id: int | None) -> int | None:
            if frame_id == 0:
                trigger_mode = self.acq_trigger_mode
                if trigger_mode == "INTERNAL_TRIGGER":
                    # Because of the bliss/lima architecture we can't really know the index
                    if self._last_index == 0:
                        # For a ct the index is valid
                        return 0
                    return None
            return frame_id

        frame_id = normalize_frame_id(data.frame_id)
        return image_utils.ImageData(data.array, frame_id, None)

    def __getitem__(self, key):
        if isinstance(key, int):
            return self._get_index(key)
        elif isinstance(key, slice):
            return self._get_slice(key)
        else:
            raise TypeError(
                f"{type(self).__name__} indices must be integers or slices, not {type(key).__name__}"
            )

    def _get_index(self, frame_id):
        if frame_id == -1:
            return self.get_last_live_image().array
        if frame_id < -1:
            raise NotImplementedError("Negative index not supported")

        if self._saved and frame_id <= self._last_index_saved_and_closed:
            # immediately accessible from file
            return self._get_from_file(frame_id)
        elif frame_id <= self._last_index:
            # try to access on lima server
            try:
                return self._get_from_server(frame_id)
            except IndexNoMoreThereError:
                # wait for image to become available from file
                # TODO wait_for_file(timeout)
                if self._saved:
                    return self._get_from_file(frame_id)
                else:
                    raise
        else:
            raise IndexNotYetThereError

    def _get_slice(self, key):
        key_range = range(*key.indices(len(self)))
        return np.array([self._get_index(i) for i in key_range])

    def _file_ref(self, frame_id) -> ImageReference:
        """Convert a frame_id to an image reference to access from file.
        IMPORTANT: It doesn't mean such frame_id is actually available."""
        acq_in_scan, file_in_acq, frame_in_file = self._decompose_frame_id(frame_id)
        file_in_scan = acq_in_scan * self._files_per_acquisition + file_in_acq
        file_number = file_in_scan + self._file_offset
        file_path = self._file_path % (file_number)
        return ImageReference(
            self._file_format, file_path, self._data_path, frame_in_file
        )

    def _get_from_server(self, frame_id):
        if self._buffer_max_number <= (self._last_index - frame_id):
            raise IndexNoMoreThereError()  # TODO IndexError here and more details in LimaStream ???

        acq_in_scan, file_in_acq, frame_in_file = self._decompose_frame_id(frame_id)
        frame_in_acq = file_in_acq * self._frames_per_file + frame_in_file

        # We need to check the server acquisition is the same before AND after reading the image.
        # This is the only way to ensure the image belong to an acquisition. This is due to the
        # lack of atomic operation to get an image and the current acquisition on server.
        if not self._is_acquisition_still_on_server(
            acq_in_scan + self._acquisition_offset
        ):
            raise IndexNoMoreThereError()  # TODO IndexError here and more details in LimaStream ???

        frame = image_utils.image_from_server(self.proxy, frame_in_acq)
        assert frame_in_acq == frame.frame_id

        if not self._is_acquisition_still_on_server(
            acq_in_scan + self._acquisition_offset
        ):
            raise IndexNoMoreThereError()  # TODO IndexError here and more details in LimaStream ???

        return frame.array

    def _get_from_file(self, frame_id):
        ref = self._file_ref(frame_id)
        return image_utils.image_from_file(
            ref.file_path, ref.data_path, ref.index, ref.format
        )

    def get_references(self, key):
        # NOTE: _last_index is used to define when references are available.
        # It should normally be _last_index_saved instead, but then, a LimaView
        # would not return the same amount of images and references.
        saved_len = self._last_index + 1
        if isinstance(key, int):
            if key < 0:
                key += saved_len
            elif key >= saved_len:
                raise IndexNotYetThereError
            return self._file_ref(key)
        elif isinstance(key, slice):
            key_range = range(*key.indices(saved_len))
            return [self._file_ref(i) for i in key_range]
        else:
            raise TypeError(
                f"{type(self).__name__} indices must be integers or slices, not {type(key).__name__}"
            )


class Lima2Client:
    PROTOCOL_VERSION = 1

    def __init__(self, **lima_info):
        self._client = None
        self._proc = None

        server_urls = lima_info["server_urls"]
        self._source = lima_info["name"]

        self._saved = "file_format" in lima_info
        if self._saved:
            self._frame_per_acquisition = lima_info["frame_per_acquisition"]
            self._frame_per_file = lima_info["frame_per_file"]
            self._file_format = lima_info["file_format"]
            self._path_template = lima_info["path_template"]
            self._data_path = lima_info["data_path"]
            self._directory = lima_info["directory"]
            self._rank = lima_info["rank"]
            self._suffix = lima_info["suffix"]
            self._prefix = lima_info["prefix"]
            self._file_per_acquisition = math.ceil(
                self._frame_per_acquisition / self._frame_per_file
            )
        else:
            self._file_format = None

        self._server_urls = server_urls
        self._acq_uuid = None

        self._nb_frames_acquired = 0
        self._nb_frames_xferred = 0

    @property
    def client(self):
        """Lazy creation of the Client, because a session may never use it."""
        from lima2.client import Client

        if not self._client:
            tango_ctrl_dev = DeviceProxy(self._server_urls["tango_ctrl_url"])
            tango_recv_devs = [
                DeviceProxy(url) for url in self._server_urls["tango_recv_urls"]
            ]

            self._client = Client(
                ctl_dev=tango_ctrl_dev,
                rcv_devs=tango_recv_devs,
            )

        return self._client

    @property
    def file_format(self):
        return self._file_format

    def __len__(self):
        return self._nb_frames_xferred

    def update(self, acq_uuid: str, nb_frames_acquired: int, nb_frames_xferred: int):
        _logger.debug(f"Client update with {nb_frames_acquired=} {nb_frames_xferred=}")
        self._nb_frames_acquired = nb_frames_acquired
        self._nb_frames_xferred = nb_frames_xferred

        acq_uuid = uuid.UUID(acq_uuid)
        if self._acq_uuid != acq_uuid:
            self._acq_uuid = acq_uuid

    def _is_acquisition_on_server(self, acq_number):
        # Always on server unless the pipeline is explicitely deleted
        return True

    def _get_from_server(self, frame_id):
        from lima2.client.devencoded.sparse_frame import SparseFrame

        if not self._proc:
            self._proc = self.client.current_pipeline

        # TODO Support slicing
        if isinstance(frame_id, slice):
            raise NotImplementedError

        _logger.debug(f"Getting frame frame_id={frame_id}")
        frm = self._proc.get_frame(frame_id, source=self._source)

        if isinstance(frm, SparseFrame):
            frm = frm.densify()

        # TODO: convert to ImageData for now but without acq_tag
        return image_utils.ImageData(array=frm.data, frame_id=frm.idx, acq_tag=None)

    def get_last_live_image(self) -> image_utils.ImageData:
        _logger.debug("Getting last frame")
        return self._get_from_server(-1)

    def __getitem__(self, frame_id) -> np.ndarray:
        # TODO: support from file
        return self._get_from_server(frame_id).array

    def get_references(self, frame_id) -> ImageReference:
        if isinstance(frame_id, int):
            if frame_id > self._nb_frames_xferred:
                raise IndexNotYetThereError
            return self._file_ref(frame_id)
        elif isinstance(frame_id, slice):
            if frame_id.stop is None:
                stop = self._nb_frames_xferred
            else:
                stop = min(frame_id.stop, self._nb_frames_xferred)
            s = slice(frame_id.start, stop, frame_id.step)
            return [self._file_ref(index) for index in range(*s.indices(s.stop))]
        else:
            raise TypeError()

    def _file_ref(self, frame_id):
        """Convert a frame_id to an image reference to access from file.
        IMPORTANT: It doesn't mean such frame_id is actually available."""
        acq_id, frame_in_acq = divmod(frame_id, self._frame_per_acquisition)
        file_in_acq, frame_in_file = divmod(frame_in_acq, self._frame_per_file)
        file_id = acq_id * self._file_per_acquisition + file_in_acq
        file_name = self._path_template.format(
            filename_prefix=self._prefix,
            filename_rank=self._rank,
            file_number=file_id,
            filename_suffix=self._suffix,
        )
        file_path = os.path.join(self._directory, file_name)
        return ImageReference(
            self._file_format, file_path, self._data_path, frame_in_file
        )
