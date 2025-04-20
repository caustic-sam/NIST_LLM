"""Manage the writing of scan data by Bliss and by device servers (currently only LiMa)"""

from .base import FileWriter

# Register writers by importing them
from .null import NullWriter  # noqa F401
from .internal import InternalNexusWriter  # noqa F401
from .external import ExternalNexusWriter  # noqa F401
from .external_tango import ExternalTangoNexusWriter  # noqa F401

get_writer_class = FileWriter.get_writer_class
