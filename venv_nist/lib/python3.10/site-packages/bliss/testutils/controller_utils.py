# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

import os
from contextlib import contextmanager
from bliss.testutils.process_utils import start_tango_server


@contextmanager
def lima_simulator_context(personal_name: str, device_name: str):
    fqdn_prefix = f"tango://{os.environ['TANGO_HOST']}"
    device_fqdn = f"{fqdn_prefix}/{device_name}"
    admin_device_fqdn = f"{fqdn_prefix}/dserver/LimaCCDs/{personal_name}"

    conda_env = os.environ.get("LIMA_SIMULATOR_CONDA_ENV")
    if not conda_env:
        conda_env = None
    conda = os.environ.get("CONDA_EXE", None)
    if conda_env and conda:
        if os.sep in conda_env:
            option = "-p"
        else:
            option = "-n"
        runner = [conda, "run", option, conda_env, "--no-capture-output", "LimaCCDs"]
    else:
        runner = ["LimaCCDs"]

    with start_tango_server(
        *runner,
        personal_name,
        # "-v4",               # to enable debug
        device_fqdn=device_fqdn,
        admin_device_fqdn=admin_device_fqdn,
        state=None,
        check_children=conda_env is not None,
    ) as dev_proxy:
        yield device_fqdn, dev_proxy


@contextmanager
def mosca_simulator_context(personal_name: str, device_name: str):
    fqdn_prefix = f"tango://{os.environ['TANGO_HOST']}"
    device_fqdn = f"{fqdn_prefix}/{device_name}"
    admin_device_fqdn = f"{fqdn_prefix}/dserver/SimulSpectro/{personal_name}"

    conda_env = os.environ.get("MOSCA_SIMULATOR_CONDA_ENV")
    if not conda_env:
        conda_env = None
    conda = os.environ.get("CONDA_EXE", None)
    if conda_env and conda:
        if os.sep in conda_env:
            option = "-p"
        else:
            option = "-n"
        runner = [
            conda,
            "run",
            option,
            conda_env,
            "--no-capture-output",
            "SimulSpectro",
        ]
    else:
        runner = ["SimulSpectro"]

    with start_tango_server(
        *runner,
        personal_name,
        # "-v4",               # to enable debug
        device_fqdn=device_fqdn,
        admin_device_fqdn=admin_device_fqdn,
        state=None,
        check_children=conda_env is not None,
    ) as dev_proxy:
        yield device_fqdn, dev_proxy
