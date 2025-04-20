from __future__ import annotations
import logging

from pydantic import ValidationError

from .core import CoreBase, CoreResource, doc
from .models.common import ErrorResponse, custom_description
from .models.object import (
    HardwareNamePath,
    ObjectSchema,
    HardwaresResourceQuery,
    SetObjectProperty,
    RegisterHardwareSchema,
    CouldNotRegisterErrorResponse,
)
from .models.utils import paginated

logger = logging.getLogger(__name__)


class ObjectApi(CoreBase):
    """The Hardware Feature

    This makes all loaded hardware available via flask resources, properties can be changed
    via put, and functions called via post requests.

    Hardware changes are notified via socketio events
    """

    _base_url = "object"
    _namespace = "object"

    def setup(self) -> None:
        self.register_route(_HardwaresResourceV1, "")
        self.register_route(_HardwareResourceV1, "/<string:name>")


class _HardwaresResourceV1(CoreResource[ObjectApi]):
    @doc(
        summary="Get a list of all hardware objects and their statuses",
        responses={"200": paginated(ObjectSchema)},
    )
    def get(self, query: HardwaresResourceQuery):
        """Get a list of all hardware objects and their statuses"""
        object_store = self.rest_service.object_store
        objects = [
            o.state.model_dump() for o in object_store.get_objects(type=query.type)
        ]
        return {"total": len(objects), "results": objects}, 200

    @doc(
        summary="Register a series of hardware objects to be made available via the API",
        responses={"200": RegisterHardwareSchema, "400": CouldNotRegisterErrorResponse},
    )
    def post(self, body: RegisterHardwareSchema):
        """Register a series of hardware objects to be made available via the API"""
        object_store = self.rest_service.object_store

        unregistered_objects = []
        for name in body.names:
            try:
                object_store.register_object(name)
            except Exception as e:
                unregistered_objects.append({"name": name, "error": str(e)})

        if unregistered_objects:
            return {
                "error": "Could not register hardware objects",
                "objects": unregistered_objects,
            }, 400

        return body.model_dump()


class _HardwareResourceV1(CoreResource[ObjectApi]):
    @doc(
        summary="Get a single hardware object",
        responses={"200": ObjectSchema, "404": ErrorResponse},
    )
    def get(self, path: HardwareNamePath):
        """Get the status of a particular hardware object"""
        obj = self.rest_service.object_store.get_object(path.name)
        if obj:
            return obj.state.model_dump(), 200
        else:
            return {"error": "No such object"}, 404

    @doc(
        summary="Update an object property",
        responses={
            "200": SetObjectProperty,
            "400": custom_description(ErrorResponse, "Could not set object property"),
            "404": custom_description(ErrorResponse, "No such object"),
        },
    )
    def put(self, path: HardwareNamePath, body: SetObjectProperty):
        """Update a property on a hardware object"""
        obj = self.rest_service.object_store.get_object(path.name)
        if obj:
            try:
                obj.set(body.property, body.value)
                return {"property": body.property, "value": body.value}, 200
            except ValidationError as e:
                return e.errors(), 422
            # To catch gevent.Timeout as well
            except BaseException as e:
                logger.exception(
                    f"Could not change property {body.property}: {str(e)} for {obj.name}",
                )
                return {"error": str(e)}, 400
        else:
            return {"error": "No such object"}, 404

    @doc(
        summary="Unregister an object",
        responses={
            "204": None,
            "404": ErrorResponse,
        },
    )
    def delete(self, path: HardwareNamePath):
        """Unregister an object."""
        object_store = self.rest_service.object_store
        obj = object_store.get_object(path.name)
        if obj is None:
            return {"error": "No such object"}, 404

        object_store.unregister_object(path.name)
        return b"", 204
