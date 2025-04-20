from __future__ import annotations
import logging
import typing
import importlib

from .core import CoreBase, CoreResource, doc
from .models.common import ErrorResponse
from .models.object_type import ObjectTypeIDPath, ObjectTypeSchema
from .models.utils import paginated
from ..typedef import ObjectType

logger = logging.getLogger(__name__)


def serialize_object_type(obj: type[ObjectType]) -> dict[str, typing.Any]:
    callables: dict[str, dict] = {}
    if obj.CALLABLES is not None:
        for k, callable_model in obj.CALLABLES.__annotations__.items():
            if isinstance(callable_model, str):
                # With future annotations or python >= 3.10
                module = importlib.import_module(obj.__module__)
                # NOTE: Actually this function is used by pydantic to complete the same thing
                callable_model = typing._eval_type(
                    typing.ForwardRef(callable_model), module.__dict__, {}
                )

            schema = callable_model.model_json_schema()
            # Drop virtual def
            schema = list(schema.values())[0]
            # Drop schema name
            schema = list(schema.values())[0]

            callables[k] = schema["properties"]

    schema = {
        "type": obj.NAME,
        "state_ok": obj.STATE_OK,
        "properties": obj.PROPERTIES.model_json_schema()["properties"],
        "callables": callables,
    }
    return schema


class ObjectTypeApi(CoreBase):
    """Abstract object feature.

    Exposes the description of the abstract objects
    exposed by BLISS rest API.
    """

    _base_url = "object_type"
    _namespace = "object_type"

    def setup(self) -> None:
        self.register_route(_ObjectTypesResource, "")
        self.register_route(_ObjectTypeResource, "/<string:id>")


class _ObjectTypesResource(CoreResource[ObjectType]):
    @doc(
        summary="Get a list of all abstract objects",
        responses={"200": paginated(ObjectTypeSchema)},
    )
    def get(self):
        """Get a list of all abstract objects"""
        object_factory = self.rest_service.object_factory
        objects = object_factory.get_abstract_objects()
        results = [serialize_object_type(obj) for obj in objects]
        return {"total": len(objects), "results": results}, 200


class _ObjectTypeResource(CoreResource[ObjectType]):
    @doc(
        summary="Get a single abstract object",
        responses={"200": ObjectTypeSchema, "404": ErrorResponse},
    )
    def get(self, path: ObjectTypeIDPath):
        """Get a single abstract object"""
        object_factory = self.rest_service.object_factory
        obj = object_factory.get_abstract_object(path.id)
        if obj is None:
            return {"error": "No such abstract object"}, 404
        schema = serialize_object_type(obj)
        return schema, 200
