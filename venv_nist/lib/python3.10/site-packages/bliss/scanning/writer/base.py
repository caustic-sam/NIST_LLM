import os
from typing import Optional, Union
from collections.abc import Sequence, Callable, Generator

import numpy
from bliss.config import settings

from bliss import current_session
from bliss.common.event import connect, disconnect
from bliss.scanning.chain import AcquisitionSlave, AcquisitionMaster
from bliss.scanning.channel import AcquisitionChannel
from blissdata.redis_engine.exceptions import EmptyViewException
from blissdata.streams.base import Stream, EventRange
from blissdata.streams.hdf5_fallback import Hdf5BackedStream


_ScanEventInfo = Union[Sequence[numpy.ndarray], dict, None]
_ScanSignal = str
_ScanEventSender = Union[AcquisitionMaster, AcquisitionSlave, AcquisitionChannel]
_ScanEventCallback = Callable[
    [Optional[_ScanEventInfo], Optional[_ScanSignal], Optional[_ScanEventSender]], None
]


class FileWriter:
    """Write scan data in files."""

    _CLASSES = dict()
    _NAME = None
    _FILE_EXTENSION = NotImplemented

    def __init_subclass__(cls, name: Optional[str] = None) -> None:
        super().__init_subclass__()
        if name:
            FileWriter._CLASSES[name] = cls
            cls._NAME = name

    @classmethod
    def get_writer_class(cls, name: str) -> type["FileWriter"]:
        try:
            return cls._CLASSES[name]
        except KeyError:
            raise RuntimeError(
                f"Writer **{name}** does not exist. Possible writers are {list(cls._CLASSES)}"
            ) from None

    @classmethod
    def get_writer_names(cls) -> list[str]:
        return list(cls._CLASSES)

    def __init__(
        self,
        root_path_template: str = "",
        device_root_path_template: str = "",
        data_filename_template: str = "",
        connection=None,
    ) -> None:
        # Templates for paths
        self._root_path_template = root_path_template
        self._acq_obj_root_path_template = device_root_path_template
        self._data_filename_template = data_filename_template
        self._template_dict = dict()

        # Saving options
        self._acq_obj_saving = True

        # Callbacks upon scan events
        self._event_receivers: list[_EventReceiver] = list()
        self._master_event_callback: Optional[_ScanEventCallback] = None
        self._slave_event_callback: Optional[_ScanEventCallback] = None

        # Persistent options shared by all instances of this class in the same session
        try:
            name = current_session.name
        except AttributeError:
            name = "default"
        db_name = f"writers:{name}:{self.__class__.__name__.lower()}"
        self._options = settings.HashObjSetting(db_name, connection=connection)

    @classmethod
    def get_name(cls) -> Optional[None]:
        return cls._NAME

    def get_filename(self) -> str:
        """Filename for the Bliss data, i.e. not the filename(s) used by device servers"""
        if not self.saving_enabled():
            return None
        dirname = self._get_root_path()
        basename = self._data_filename_template.format(**self._template_dict)
        basename = os.path.extsep.join((basename, self._FILE_EXTENSION))
        return os.path.join(dirname, basename)

    @classmethod
    def get_file_extension(cls) -> str:
        return cls._FILE_EXTENSION

    def get_writer_options(self) -> dict:
        return self._options.get_all()

    def update_template(self, *args, **kw) -> None:
        """Directories and filename are string templates. Set the template variables."""
        self._template_dict.update(*args, **kw)

    def enable_device_saving(self, save: bool) -> None:
        """Enable the saving by device servers (currently LiMa only)"""
        self._acq_obj_saving = save

    def get_device_root_path(self, img_acq_device: Optional[str] = None) -> str:
        """Root directory where devices save data"""
        params = self._template_dict
        if img_acq_device:
            params = dict(params)
            params["img_acq_device"] = img_acq_device
        return self._acq_obj_root_path_template.format(**params)

    @classmethod
    def saving_enabled(cls) -> bool:
        """Returns True when saving is enabled"""
        return bool(cls._FILE_EXTENSION)

    def device_saving_enabled(self) -> bool:
        """Returns True when saving is enabled for device servers"""
        return self._acq_obj_saving

    def get_last_scan_number(self) -> int:
        """Scans start numbering from 1 so 0 indicates
        no scan exists in the file.
        """
        raise NotImplementedError

    @property
    def separate_scan_files(self) -> Optional[bool]:
        """Each scan is saved in a separate file"""
        raise NotImplementedError

    def create_path(self, path: str) -> bool:
        """Create a directory that should be owned by the writer (could be an external process) and not by Bliss (this process)"""
        abspath = os.path.abspath(path)
        os.makedirs(abspath, exist_ok=True)
        return True

    def finalize(self, scan) -> None:
        """Call at the end of the scan"""
        raise NotImplementedError

    def cleanup(self) -> None:
        """Call after finalize"""
        for ev_receiver in self._event_receivers:
            ev_receiver.disconnect()
        self._event_receivers.clear()

    def prepare(self, scan) -> None:
        """Call at the start of the scan"""
        if self.saving_enabled():
            self.create_path(self._get_root_path())

        self._prepare_scan(scan)

        self._event_receivers.clear()
        for acq_master in self._iter_acq_masters(scan):
            if callable(self._master_event_callback):
                self._prepare_callbacks(acq_master, self._master_event_callback)

            self._prepare_device_saving(acq_master)

            if callable(self._slave_event_callback):
                for slave in acq_master.slaves:
                    if isinstance(slave, AcquisitionSlave):
                        self._prepare_callbacks(slave, self._slave_event_callback)

    def _prepare_scan(self, scan) -> None:
        raise NotImplementedError

    def _iter_acq_masters(self, scan) -> Generator[AcquisitionMaster, None, None]:
        for device in scan._acq_chain.nodes_list:
            if isinstance(device, AcquisitionMaster):
                yield device

    def _prepare_callbacks(
        self, acq_obj: Union[AcquisitionSlave, AcquisitionMaster], callback
    ) -> None:
        ev_receiver = _EventReceiver(acq_obj, callback)
        ev_receiver.connect()
        self._event_receivers.append(ev_receiver)

    def _get_root_path(self) -> str:
        """Detectory where the writer saves data"""
        return self._root_path_template.format(**self._template_dict)

    def _prepare_device_saving(self, acq_master: AcquisitionMaster) -> None:
        """Tell devices where to save the data (currently only LiMa master has this)"""
        if not self.device_saving_enabled():
            acq_master.set_device_saving(None, None, force_no_saving=True)
            return
        device_root_path = self.get_device_root_path(img_acq_device=acq_master.name)
        directory = os.path.dirname(device_root_path)
        prefix = os.path.basename(device_root_path)
        acq_master.set_device_saving(directory, prefix)
        # Note: directory could be mapped to the real physical directory
        #       so `makedirs` should be done in `set_device_saving` or by
        #       the device itself


class _EventReceiver:
    def __init__(
        self,
        acq_obj: Union[AcquisitionSlave, AcquisitionMaster],
        callback: _ScanEventCallback,
    ):
        self._acq_obj = acq_obj
        self._callback = callback

    def __call__(
        self,
        event_dict: Optional[_ScanEventInfo] = None,
        signal: Optional[_ScanSignal] = None,
        sender: Optional[_ScanEventSender] = None,
    ):
        # Louie calls for AcquisitionSlave and AcquisitionMaster:
        #     receiver(signal=signal, sender=sender)
        # Louie calls for AcquisitionChannel:
        #     receiver(event_dict, signal=signal, sender=sender)
        if not callable(self._callback):
            return

        if signal == "new_data":
            channel = sender

            # emulates blissdata Views creation, but without actually going through Redis
            if not hasattr(channel, "_internal_writer_cache"):
                cache = {}

                # Unwrap file backed streams to make sure writer never tries to read
                # from the file it is supposed to write
                if isinstance(channel.stream, Hdf5BackedStream):
                    cache["stream"] = Stream(channel.stream.event_stream)
                else:
                    cache["stream"] = channel.stream

                cache["encoder"] = channel.stream.event_stream._encoder
                cache["hl_index"] = 0
                cache["ll_index"] = 0

                channel._internal_writer_cache = cache
            else:
                cache = channel._internal_writer_cache

            encoder = cache["encoder"]
            data = encoder.decode(encoder.encode(event_dict))
            try:
                view = cache["stream"]._build_view_from_events(
                    cache["hl_index"],
                    EventRange(cache["ll_index"], 0, data, False),
                    last_only=False,
                )
                cache["hl_index"] = view.index + len(view)
            except EmptyViewException:
                return
            finally:
                cache["ll_index"] += len(data)
            event_dict = view
        self._callback(event_dict, signal, sender)

    def connect(self):
        connect(self._acq_obj, "start", self)
        for channel in self._acq_obj.channels:
            connect(channel, "new_data", self)

    def disconnect(self):
        if self._acq_obj is None:
            return
        disconnect(self._acq_obj, "start", self)
        for channel in self._acq_obj.channels:
            disconnect(channel, "new_data", self)
        self._acq_obj = None
