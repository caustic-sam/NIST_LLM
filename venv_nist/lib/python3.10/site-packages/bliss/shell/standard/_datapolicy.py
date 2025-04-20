# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.
"""
Standard functions provided to the BLISS shell.
"""

from __future__ import annotations

import typing
from bliss.common.logtools import elogbook
from bliss import current_session
from bliss.common.utils import typecheck


@typecheck
@elogbook.disable_command_logging
def newproposal(
    proposal_name: typing.Optional[str] = None,
    session_name: typing.Optional[str] = None,
    prompt: typing.Optional[bool] = False,
):
    """
    Change the proposal and session name used to determine the saving path.
    """
    current_session.scan_saving.newproposal(
        proposal_name, session_name=session_name, prompt=prompt
    )


@typecheck
@elogbook.disable_command_logging
def newsample(
    collection_name: typing.Optional[str] = None,
    description: typing.Optional[str] = None,
):
    """
    Same as `newcollection` with sample name equal to the collection name.
    """
    current_session.scan_saving.newsample(collection_name, description=description)


@typecheck
@elogbook.disable_command_logging
def newcollection(
    collection_name: typing.Optional[str] = None,
    sample_name: typing.Optional[str] = None,
    sample_description: typing.Optional[str] = None,
):
    """
    Change the collection name used to determine the saving path.
    Metadata can be modified later if needed.
    """
    current_session.scan_saving.newcollection(
        collection_name, sample_name=sample_name, sample_description=sample_description
    )


@typecheck
@elogbook.disable_command_logging
def newdataset(
    dataset_name: typing.Optional[typing.Union[str, int]] = None,
    description: typing.Optional[str] = None,
    sample_name: typing.Optional[str] = None,
    sample_description: typing.Optional[str] = None,
):
    """
    Change the dataset name used to determine the saving path.

    The description can be modified until the dataset is closed.
    """
    current_session.scan_saving.newdataset(
        dataset_name,
        description=description,
        sample_name=sample_name,
        sample_description=sample_description,
    )


@elogbook.disable_command_logging
def endproposal():
    """
    Close the active dataset and move to the default inhouse proposal.
    """
    current_session.scan_saving.endproposal()


@elogbook.disable_command_logging
def enddataset():
    """
    Close the active dataset.
    """
    current_session.scan_saving.enddataset()
