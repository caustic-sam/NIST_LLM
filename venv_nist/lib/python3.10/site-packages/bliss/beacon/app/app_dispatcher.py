# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

from werkzeug.middleware.dispatcher import DispatcherMiddleware

from ..services.homepage import homepage_app as home
from ..services.configuration import config_app as conf


def create_app(log_viewer_port):
    return DispatcherMiddleware(
        home.create_app(log_viewer_port),
        {
            "/config": conf.create_app(),
        },
    )
