# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

import pytest
import os
from unittest.mock import MagicMock
from bliss.icat.client import DatasetId
from bliss.config import static
from bliss.common import logtools


@pytest.fixture
def icat_mock_client(mocker) -> MagicMock:
    """
    Setup a mocked ICAT client.

    The retured object can be used to check that all desired calls are being made.
    """
    config = static.get_config()
    config.root["icat_servers"] = {"disable": False}
    mockedclass = mocker.patch("bliss.icat.client.IcatClient")

    datasetids: list[DatasetId] = []

    def store_dataset(path=None, **_):
        nonlocal datasetids
        datasetids.append(DatasetId(name=os.path.basename(path), path=path))

    def registered_dataset_ids(**_):
        nonlocal datasetids
        return None if datasetids == [] else datasetids

    mockedclass.return_value.store_dataset.side_effect = store_dataset

    mockedclass.return_value.registered_dataset_ids.side_effect = registered_dataset_ids

    return mockedclass


@pytest.fixture
def elogbook_enabled(icat_mock_client):
    """Enables the Elogbook for any Bliss session.

    Setup icat mocked client.
    """
    logtools.elogbook.enable()
    try:
        yield
    finally:
        logtools.elogbook.disable()
