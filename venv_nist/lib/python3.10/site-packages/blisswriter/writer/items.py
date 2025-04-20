import os
import abc
import logging
from contextlib import contextmanager
from typing import Tuple

import numpy

from ..utils.logging_utils import CustomLogger
from ..io import nexus


logger = logging.getLogger(__name__)


class Hdf5ItemWriter(abc.ABC):
    """Manages creation and growth of one HDF5 dataset or group."""

    def __init__(
        self, filename=None, parent=None, filecontext=None, parent_logger=None
    ):
        """
        :param str filename: HDF5 file name
        :param str filecontext: HDF5 open context manager
        :param str parent: path in the HDF5 file
        :param parent_logger:
        """
        if filecontext is None:
            filecontext = self._filecontext
        self.filename = filename
        self.filecontext = filecontext
        if parent is None:
            self.parent = ""
        else:
            self.parent = parent
        if parent_logger is None:
            parent_logger = logger
        self.logger = CustomLogger(parent_logger, self)
        self.npoints = 0
        self._exists = False
        self._parent_exists = False
        self.__cached_dataset_nxroot = None
        self.__cached_dataset = None
        self.__cached_parent_nxroot = None
        self.__cached_parent = None
        self.__cached_nxroot = None

    def __repr__(self):
        if self.name:
            return self.path
        else:
            return os.path.splitext(os.path.basename(self.filename))[0]

    @property
    def path(self):
        """Path inside the file of the dataset"""
        if self.name:
            return "/".join([self.parent, self.name])
        else:
            return self.parent

    @property
    def url(self):
        """Full URL of the dataset"""
        return self.filename + "::" + self.path

    @property
    def parent_url(self):
        """Full URL of the parent"""
        return self.filename + "::" + self.parent

    @abc.abstractproperty
    def name(self):
        """Name of the dataset inside the file"""
        pass

    @abc.abstractproperty
    def alias(self):
        """Alias of the dataset inside the file"""
        pass

    @contextmanager
    def _filecontext(self):
        if self.__cached_nxroot is None:
            with nexus.nxRoot(self.filename, mode="a") as nxroot:
                self.__cached_nxroot = nxroot
                try:
                    yield nxroot
                finally:
                    self.__cached_nxroot = None
        else:
            yield self.__cached_nxroot

    def create(self):
        """Skipped when it already exists. It may not exist after creation."""
        self.create_parent()
        if self.exists:
            return
        with self.filecontext() as nxroot:
            self._create(nxroot)

    def create_parent(self):
        """Skipped when it already exists. It may not exist after creation."""
        if self.parent_exists:
            return
        with self.filecontext() as nxroot:
            self._create_parent(nxroot)

    @abc.abstractmethod
    def _create(self, nxroot):
        pass

    @abc.abstractmethod
    def _create_parent(self, nxroot):
        pass

    @property
    def exists(self):
        """
        :returns bool:
        """
        if self._exists:
            return True
        with self.filecontext() as nxroot:
            self._exists = exists = self.path in nxroot
            return exists

    @property
    def parent_exists(self):
        """
        :returns bool:
        """
        if self._parent_exists:
            return True
        with self.filecontext() as nxroot:
            self._parent_exists = exists = self.parent in nxroot
            return exists

    @contextmanager
    def open(self, create=False):
        """
        :param bool create: when missing
        :yields h5py.Dataset or None:
        """
        with self.filecontext() as nxroot:
            cached_dataset_nxroot = nxroot
            if cached_dataset_nxroot is self.__cached_dataset_nxroot:
                yield self.__cached_dataset
            else:
                if create and not self.exists:
                    self.create()
                try:
                    dset = nxroot[self.path]
                except Exception:
                    self.logger.warning("'%s' does not exist", self.url)
                    dset = None
                else:
                    self.__cached_dataset = dset
                    self.__cached_dataset_nxroot = cached_dataset_nxroot
                yield dset

    @contextmanager
    def open_parent(self, create=False):
        """
        :param bool create: when missing
        :yields h5py.Group or None:
        """
        with self.filecontext() as nxroot:
            cached_parent_nxroot = nxroot
            if cached_parent_nxroot is self.__cached_parent_nxroot:
                yield self.__cached_parent
            else:
                if create and not self.parent_exists:
                    self.parent_create()
                try:
                    group = nxroot[self.parent]
                except Exception:
                    self.logger.warning("'%s' does not exist", self.parent_url)
                    group = None
                else:
                    self.__cached_parent = group
                    self.__cached_parent_nxroot = cached_parent_nxroot
                yield group

    def add(self, newdata):
        """Add data

        :param sequence newdata:
        """
        with self.open(create=True) as destination:
            try:
                self.npoints += self._insert_data(destination, newdata)
            except Exception:
                self.logger.error("Exception when adding data")
                raise

    @abc.abstractmethod
    def _insert_data(self, destination, newdata):
        """Insert new data in dataset

        :param h5py.Dataset or h5py.Group dset:
        :param sequence newdata:
        :returns int: number of added points
        """
        pass

    @property
    def npoints_expected(self):
        return 0

    @property
    def complete(self):
        """Variable length scans are marked complete when we have some data"""
        n, nall = self.npoints, self.npoints_expected
        return n and n >= nall

    @property
    def progress(self):
        if self.npoints_expected:
            return self.npoints / self.npoints_expected
        else:
            if self.npoints:
                return numpy.nan
            else:
                return 0

    @property
    def progress_string(self) -> Tuple[str, str]:
        if self.npoints_expected:
            sortkey = self.npoints / self.npoints_expected
            s = f"{sortkey * 100:.0f}%"
        else:
            sortkey = self.npoints
            s = f"{sortkey:d}pts"
        return s, sortkey

    @property
    def _progress_log_suffix(self):
        return ""

    def log_progress(self, expect_complete: bool = False) -> bool:
        npoints_expected = self.npoints_expected
        npoints_current = self.npoints
        complete = self.complete
        if expect_complete:
            if complete:
                self.logger.debug(
                    "%d/%d points published%s",
                    npoints_current,
                    npoints_expected,
                    self._progress_log_suffix,
                )
            elif npoints_current:
                self.logger.warning(
                    "only %d/%d points published%s",
                    npoints_current,
                    npoints_expected,
                    self._progress_log_suffix,
                )
            else:
                self.logger.error("no data published%s", self._progress_log_suffix)
        else:
            self.logger.debug(
                "progress %d/%d%s",
                npoints_current,
                npoints_expected,
                self._progress_log_suffix,
            )
        return complete

    def flush(self):
        """Flush any buffered data"""
        self.create()
