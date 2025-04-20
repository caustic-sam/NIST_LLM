# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

# Entry-point compatibility for BLISS<=2.1 API
# Simplify transition for CI from side projects
from bliss.beacon.app.beacon_server import main


if __name__ == "__main__":
    main()
