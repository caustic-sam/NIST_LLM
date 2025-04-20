# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

from __future__ import annotations

from importlib import metadata

from bliss import _get_current_session
from bliss.common.session import Session

from .core import CoreBase, doc
from .models.info import InfoSchema


class InfoApi(CoreBase):
    _base_url = "info"
    _namespace = "info"

    def setup(self):
        self.register_route(_InfoResource, "")


class _InfoResource:
    @doc(
        summary="Get information from this BLISS session",
        responses={
            "200": InfoSchema,
        },
    )
    def get(self):
        """Get information from the session."""

        session: Session = _get_current_session()
        session_config = session.local_config
        config = session.config.root

        def get_meta(name: str) -> str:
            """Get the meta from the session description, else from the root"""
            return session_config.get(name) or config.get(name) or ""

        def safe_version(name: str) -> str:
            """Return a package version, else an empty string"""
            try:
                return metadata.version(name)
            except metadata.PackageNotFoundError:
                return ""

        info = InfoSchema(
            beamline=get_meta("beamline"),
            synchrotron=get_meta("synchrotron"),
            instrument=get_meta("instrument"),
            session=session.name,
            bliss_version=metadata.version("bliss"),
            blissdata_version=metadata.version("blissdata"),
            flint_version=safe_version("flint"),
            blisstomo_version=safe_version("bliss-tomo"),
            fscan_version=safe_version("fscan"),
            blisswebui_version=safe_version("blisswebui"),
        )

        return info.model_dump(), 200
