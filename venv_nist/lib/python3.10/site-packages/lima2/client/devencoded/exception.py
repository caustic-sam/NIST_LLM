# This file is part of the Lima2 project
#
# Copyright (c) 2020-2024 Beamline Control Unit, ESRF
# Distributed under the MIT license. See LICENSE for more info.


class DevEncodedFormatNotSupported(Exception):
    """ "Raised when the RAW data from the tango device can't be decoded."""
