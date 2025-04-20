from __future__ import annotations

import os
import time
import uuid
import gevent
import getpass
import logging
from pathlib import Path
from tempfile import gettempdir
from typing import Any, Optional

import tabulate

from .template_store import Field
from .template_store import FieldLocation
from .template_store import TemplateStore
from .external_store import ExternalTestStore
from .external_store import ExternalRedisStore
from .template_store import cache_external

from bliss import current_session
from bliss import is_bliss_shell
from bliss.scanning.writer import FileWriter
from bliss.scanning.writer import get_writer_class
from bliss.config.conductor.client import get_redis_proxy

logger = logging.getLogger(__name__)


class BasicScanSaving(TemplateStore):
    """Parameterized representation of the scan data file path

    .. code::

        <base_path>/<template>/<data_filename><file_extension>

    where

     - <base_path> is user defined
     - <template> is user defined
     - <data_filename> is user defined
     - <file_extension> depends on the select writer
    """

    class _Fields:
        base_path = Field(
            default=os.path.join(gettempdir(), "scans"),
        )
        data_filename = Field(
            default="data",
        )
        template = Field(
            default=os.path.join("{session}", ""),
        )
        images_path_relative = Field(
            default=True,
            data_type=bool,
        )
        images_path_template = Field(
            default="scan{scan_number}",
        )
        images_prefix = Field(
            default="{img_acq_device}_",
        )
        date_format = Field(
            default="%Y%m%d",
        )
        scan_number_format = Field(
            default="%04d",
        )
        _writer_module = Field(
            default="hdf5",
        )
        session = Field(
            location=FieldLocation.attribute,
            mutable=False,
        )
        date = Field(
            location=FieldLocation.attribute,
            mutable=False,
        )
        user_name = Field(
            location=FieldLocation.attribute,
            mutable=False,
        )
        scan_name = Field(
            location=FieldLocation.local,
            mutable=False,
            default="{scan_name}",
        )
        scan_number = Field(
            location=FieldLocation.local,
            mutable=False,
            default="{scan_number}",
        )
        img_acq_device = Field(
            location=FieldLocation.local,
            mutable=False,
            default="{img_acq_device}",
        )
        data_policy = Field(
            location=FieldLocation.local,
            mutable=False,
            default="None",
        )
        writer = Field(
            location=FieldLocation.attribute,
            init=False,
        )

    _LEGACY_WARDROBE_TEMPLATE = "scan_saving:{}"
    _REDIS_KEY_TEMPLATE = "scan_saving:basic:{}"
    _SCAN_NUMBER_LOCK = gevent.lock.Semaphore()

    def __init__(self, name=None, session_name=None, **kwargs):
        """
        :param name: Name in Redis
        :param session_name: Name of the BLISS session
        """
        if not name:
            name = str(uuid.uuid4().hex)
        self._name = name
        self._session_name = session_name

        redis_key = self._REDIS_KEY_TEMPLATE.format(name)
        if current_session:
            external_storage = ExternalRedisStore(redis_key)
            fields = list(self._FIELDS)
            legacy_wardrobe_name = self._LEGACY_WARDROBE_TEMPLATE.format(name)
            external_storage.cleanup_legacy_wardrobe(legacy_wardrobe_name, fields)
            self._testing = False
        else:
            external_storage = ExternalTestStore(redis_key)
            self._testing = True

        super().__init__(external_storage, **kwargs)

    @property
    def name(self) -> str:
        """The init name or a uuid."""
        return self._name

    @cache_external
    def __info__(self) -> str:
        """BLISS REPL representation"""
        rep_str = super().__info__()
        extra = self._extra_info()
        rep_str += tabulate.tabulate(tuple(extra))
        return rep_str

    def copy(self) -> "BasicScanSaving":
        kwargs = self.unresolved_dict()
        return self.__class__(self._name, self._session_name, **kwargs)

    def _extra_info(self) -> list[tuple[str]]:
        info_table = list()

        if self._testing:
            info_table.append(("NO SAVING",))
            return info_table

        writer = self.writer_object
        if not writer.saving_enabled():
            info_table.append(("NO SAVING",))
            return info_table

        data_file = writer.get_filename()
        data_dir = os.path.dirname(data_file)

        if os.path.exists(data_file):
            label = "exists"
        else:
            label = "does not exist"
        info_table.append((label, "filename", data_file))

        if os.path.exists(data_dir):
            label = "exists"
        else:
            label = "does not exist"
        info_table.append((label, "directory", data_dir))

        return info_table

    ######################
    ### DATA FILE PATH ###
    ######################

    @property
    def session(self) -> str:
        """The name of the current session or 'default' if no current session is defined."""
        return (
            self._session_name
            or (current_session.name if current_session else None)
            or "default"
        )

    @property
    def date(self) -> str:
        """Date at the moment of calling."""
        return time.strftime(self["date_format"])

    @property
    def user_name(self) -> str:
        """User of the current process."""
        return getpass.getuser()

    def get_path(self) -> str:
        """Directory of the scan data file."""
        return self.root_path

    @property
    def root_path(self) -> str:
        """Directory of the scan data file.

        For example :code:`/tmp/scans/session_name`.
        """
        return os.path.join(self.base_path, self.template)

    @property
    def data_path(self) -> str:
        """Full path for the scan data file without the extension
        This is before the writer modifies the name (given by :code:`self.filename`).

        For example :code:`/tmp/scans/session_name/data`.
        """
        return os.path.join(self.root_path, self.data_filename)

    @property
    def data_fullpath(self) -> str:
        """Full path for the scan data file with the extension.
        This is before the writer modifies the name (given by :code:`self.filename`).

        For example :code:`/tmp/scans/session_name/data.h5`.
        """
        return os.path.extsep.join((self.data_path, self.file_extension))

    @property
    def eval_data_filename(self) -> str:
        """Same as :code:`data_filename`."""
        return self.data_filename

    @property
    def filename(self) -> Optional[str]:
        """Full path for the scan data file with the extension.
        Could be modified by the writer instance.

        For example :code:`/tmp/scans/session_name/data.h5`.
        """
        return self.writer_object.get_filename()

    @property
    def images_path(self) -> str:
        """Path to be used by external devices (normally a string template).

        For example :code:`scan{scan_number}/{img_acq_device}_`.
        """
        images_path = os.path.join(self.images_path_template, self.images_prefix)
        # TODO: this is the opposite
        if not self.images_path_relative:
            return images_path
        return os.path.join(self.root_path, images_path)

    ##############
    ### WRITER ###
    ##############

    @property
    def writer(self) -> str:
        """Scan data writer name."""
        return self._writer_module

    @writer.setter
    def writer(self, value: Optional[str]) -> None:
        if value is None:
            value = "null"
        # Raise error when it does a valid writer
        _ = get_writer_class(value)
        self._writer_module = value

    @property
    def writer_class(self) -> type[FileWriter]:
        return get_writer_class(self.writer)

    @property
    def writer_object(self) -> FileWriter:
        """This instantiates the writer class."""
        klass = self.writer_class
        args = self.root_path, self.images_path, self.data_filename
        writer = klass(*args)
        unresolved = self._string_template_keys(os.path.join(*args))
        writer.update_template({name: f"{{{name}}}" for name in unresolved})
        return writer

    @property
    def file_extension(self) -> str:
        """As determined by the writer."""
        return self.writer_class.get_file_extension()

    def get_writer_object(self) -> FileWriter:
        """This instantiates the writer class."""
        return self.writer_object

    def get_writer_options(self) -> dict[str, Any]:
        return self.writer_object.get_writer_options()

    def create_path(self, path: str) -> bool:
        """The path is created by the writer if the path is part
        of the data root, else by Bliss (subdir or outside data root).
        """
        return self.writer_object.create_path(path)

    def create_root_path(self) -> None:
        """Create the scan data directory"""
        self.create_path(self.root_path)

    ############
    ### SCAN ###
    ############

    @cache_external
    def get(self) -> dict[str, Any]:
        """This method will compute all configurations needed for a new scan."""
        return {
            "root_path": self.root_path,
            "data_path": self.data_path,
            "images_path": self.images_path,
            "writer": self.writer_object,
        }

    def clone(self) -> "BasicScanSaving":
        """Called at the instantiation of a scan (in :code:`Scan.__init__`)."""
        return self.copy()

    def to_dict(self) -> dict:
        # Old Wardrobe behavior
        return self.unresolved_dict()

    def from_dict(self, values: dict) -> None:
        # Old Wardrobe behavior
        values = {k: v for k, v in values.items() if k not in self._immutable_keys}
        self.update(values)

    def on_scan_run(self, save: bool) -> None:
        """Called at the start of a scan (in :code:`Scan.run`)."""
        if (
            save
            and is_bliss_shell()
            and Path(gettempdir()) in Path(self.root_path).parents
        ):
            logger.warning(
                f"scan data are currently saved under {gettempdir()}, where files are volatile."
            )

    def _incr_scan_number(self) -> int:
        """Scan count stays in Redis and follows this logic:
        - with a data policy -> reset on dataset change
        - without a data policy -> reset is manual, see reset_scan_number()"""
        if self.data_policy == "None":
            scan_serie_identifier = "no_data_policy"
        else:
            scan_serie_identifier = self.data_path

        scan_counter_key = f"{self.session}:scan_counter"
        cnx = get_redis_proxy()
        with BasicScanSaving._SCAN_NUMBER_LOCK:
            if scan_serie_identifier.encode() == cnx.hget(scan_counter_key, "name"):
                return int(cnx.hincrby(scan_counter_key, "count", 1))
            else:
                cnx.hset(scan_counter_key, {"name": scan_serie_identifier, "count": 1})
                return 1

    def reset_scan_number(self, next=1):
        if self.data_policy != "None":
            raise RuntimeError(
                "Scan counter can't be reset manually when using a data policy"
            )
        if next < 1:
            raise ValueError("Scan number can't be less than one")
        if self.data_policy == "None":
            scan_serie_identifier = "no_data_policy"
        else:
            scan_serie_identifier = self.data_path
        cnx = get_redis_proxy()
        with BasicScanSaving._SCAN_NUMBER_LOCK:
            cnx.hset(
                f"{self.session}:scan_counter",
                {"name": scan_serie_identifier, "count": next - 1},
            )

    def next_scan_number(self):
        if self.data_policy == "None":
            scan_serie_identifier = "no_data_policy"
        else:
            scan_serie_identifier = self.data_path

        cnx = get_redis_proxy()
        scan_counter = cnx.hgetall(f"{self.session}:scan_counter")
        with BasicScanSaving._SCAN_NUMBER_LOCK:
            if scan_serie_identifier.encode() == scan_counter.get(b"name"):
                return int(scan_counter[b"count"]) + 1
            else:
                return 1

    ####################
    ### DATA POLICY ####
    ####################

    def newproposal(self, proposal_name):
        raise NotImplementedError("No data policy enabled")

    def newcollection(self, collection_name, **kw):
        raise NotImplementedError("No data policy enabled")

    def newsample(self, collection_name, **kw):
        raise NotImplementedError("No data policy enabled")

    def newdataset(self, dataset_name, **kw):
        raise NotImplementedError("No data policy enabled")

    @property
    def elogbook(self) -> None:
        return None
