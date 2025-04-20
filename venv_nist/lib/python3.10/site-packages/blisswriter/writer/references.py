import logging
from .items import Hdf5ItemWriter
from ..io import nexus


logger = logging.getLogger(__name__)


class Hdf5ScanReferenceWriter(Hdf5ItemWriter):
    """Manages creation and growth one HDF5 group for scan references."""

    def __init__(
        self,
        filename=None,
        parent=None,
        filecontext=None,
        nreferences=0,
        parent_logger=None,
    ):
        """
        :param str filename: HDF5 file name
        :param str filecontext: HDF5 open context manager
        :param str parent: path in the HDF5 file
        :param int nreferences: variable length by default
        :param parent_logger:
        """
        if parent_logger is None:
            parent_logger = logger
        super().__init__(
            filename=filename,
            parent=parent,
            filecontext=filecontext,
            parent_logger=parent_logger,
        )
        self.nreferences = nreferences

    @property
    def name(self):
        return ""

    @property
    def alias(self):
        return ""

    @property
    def npoints_expected(self):
        return self.nreferences

    def add_references(self, newuris):
        """
        Add uri links

        :param list(str) newuris:
        """
        self.add(newuris)

    def _insert_data(self, group, newuris):
        """Add uri links

        :param list(str) newuris:
        :returns int: added links
        """
        duri = nexus.getUri(group)
        for uri in newuris:
            linkname = nexus.hdf5_basename(uri)
            if uri == nexus.hdf5_join(duri, linkname):
                continue  # ignore self-reference
            nexus.createLink(group, linkname, uri)
        return len(newuris)

    def _create_parent(self, nxroot):
        """Create the group which will contain the links"""
        grp = nxroot.create_group(self.parent)
        grp.attrs["NX_class"] = "NXcollection"
        self._parent_exists = True

    def _create(self, nxroot):
        """Create the group which will contain the links"""
        self._create_parent(nxroot)
        self._exists = True
