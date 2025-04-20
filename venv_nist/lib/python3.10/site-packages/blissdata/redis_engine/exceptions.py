# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.


class EndOfStream(Exception):
    pass


class IndexNoMoreThereError(IndexError):
    """Accessing a valid stream index, but it has been trimmed off for memory saving."""

    pass


class IndexNotYetThereError(IndexError):
    """Accessing index outside stream, but it can still arrive later."""

    pass


class IndexWontBeThereError(IndexError):
    """Accessing index outside stream, but stream is already sealed."""

    pass


class NoScanAvailable(Exception):
    pass


class NoWritePermission(Exception):
    pass


class ScanLoadError(Exception):
    pass


class ScanNotFoundError(ScanLoadError):
    pass


class ScanValidationError(ScanLoadError):
    """Scan exists but is not valid, most likely the scan model version on the publisher side is different."""

    pass


class UnauthorizeStateTransition(Exception):
    def __init__(self, orig_state, dest_state):
        super().__init__(
            f"Unauthorize scan transition from {orig_state.name} to {dest_state.name}"
        )


class UnknownEncodingError(Exception):
    pass


class EmptyViewException(Exception):
    pass


class MissingPluginException(Exception):
    def __init__(self, stream_name, plugin_name):
        super().__init__(
            f"No plugin matching '{plugin_name}' entry point for stream '{stream_name}'"
        )
