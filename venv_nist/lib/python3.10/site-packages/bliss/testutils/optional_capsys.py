# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

import pytest


def pytest_addoption(parser):
    parser.addoption(
        "--disable-capsys",
        action="store_true",
        default=False,
        help="Disable capsys for debugging (only works with `optional_capsys` fixture)",
    )


@pytest.fixture
def optional_capsys(request, pytestconfig):
    """Capsys that can be disabled from command line for debugging"""
    capsys_disabled = pytestconfig.getoption("--disable-capsys")
    if capsys_disabled:
        yield None
    else:
        yield request.getfixturevalue("capsys")
