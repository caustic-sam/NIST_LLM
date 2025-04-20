# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

# Single source of truth for the version number and the like
from importlib.metadata import metadata as _metadata

_pkg_metadata = dict(_metadata("bliss").items())

copyright = "2015-2024 Beamline Control Unit, ESRF"
author = _pkg_metadata.get("Author")
author_email = _pkg_metadata.get("Maintainer-email")
license = _pkg_metadata.get("License")
description = _pkg_metadata.get("Summary")
url = _pkg_metadata.get("Home-page")
version = _pkg_metadata.get("Version")
