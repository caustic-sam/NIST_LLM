# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.
from blissdata.redis_engine.store import DataStore

_default_data_store = None


def set_default_data_store(redis_url):
    global _default_data_store
    _default_data_store = DataStore(redis_url)


def get_default_data_store():
    global _default_data_store
    if _default_data_store is None:
        raise RuntimeError(
            "No default store has been set. Use set_default_data_store(redis_url) before."
        )
    return _default_data_store
