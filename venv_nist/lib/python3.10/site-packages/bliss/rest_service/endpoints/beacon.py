# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

from __future__ import annotations
import ruamel.yaml
from flask_openapi3 import Tag

from bliss.config.conductor.client import get_default_connection

from .core import CoreBase, doc
from .models.common import ErrorResponse, custom_description
from .models.beacon import YamlContentSchema, FilePath

yaml_load = ruamel.yaml.YAML().load


class BeaconApi(CoreBase):
    _base_url = "beacon"
    _namespace = "beacon"

    def setup(self):
        resource = _GetYamlResourceV0()

        # NOTE: Here we have to use a local function and not a class
        #       in order to use `<path:path>`.
        #       It's a work around a bug on openapi.
        def get_beacon(path: FilePath):
            return resource.get(path)

        self.app.get(
            f"/api/{self._base_url}/<path:path>",
            summary="Get a yaml file from beacon",
            tags=[Tag(name=self.__class__.__name__)],
            responses={
                "200": YamlContentSchema,
                "404": custom_description(ErrorResponse, "Beacon resource not found"),
                "400": custom_description(
                    ErrorResponse, "Could not process beacon resource"
                ),
            },
        )(get_beacon)


class _GetYamlResourceV0:
    @doc(
        summary="Get a yaml file from beacon",
        responses={
            "200": YamlContentSchema,
            "404": ErrorResponse,
            "400": ErrorResponse,
        },
    )
    def get(self, path: FilePath):
        """Get a yaml file from beacon."""
        file_path = path.path

        if file_path.startswith("/"):
            return {"error": f"Wrong path syntax {file_path}"}, 400
        if not file_path.endswith(".yml"):
            return {"error": f"Wrong file kind {file_path}"}, 400
        if ".." in file_path:
            return {"error": f"Wrong path syntax {file_path}"}, 400
        if "~" in file_path:
            return {"error": f"Wrong path syntax {file_path}"}, 400

        beacon = get_default_connection()
        try:
            data = beacon.get_config_file(file_path)
            content = yaml_load(data)
        except RuntimeError:
            return {"error": "File not found"}, 404

        return {"content": content}, 200
