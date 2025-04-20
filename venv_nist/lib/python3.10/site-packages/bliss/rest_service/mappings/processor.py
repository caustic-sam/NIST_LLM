import logging

from bliss.rest_service.mappingdef import (
    ObjectMapping,
    HardwareProperty,
)
from ..types.processor import (
    ProcessorType,
    ProcessorCallablesSchema,
    ProcessorPropertiesSchema,
)

logger = logging.getLogger(__name__)


class DynamicPropertiesMixin:
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        dynamic_parameters = {}
        for (
            parameter_key,
            parameter_schema,
        ) in self._object.parameters_schema._declared_fields.items():
            dynamic_parameters[parameter_key] = parameter_schema
            self._property_map[parameter_key] = HardwareProperty(parameter_key)
            self._connect_event(self._property_map[parameter_key])

        dynamic_parameters["Meta"] = self._object.parameters_schema.Meta

        properties_schema = type(
            self.name() + "PropertiesSchema",
            (ProcessorPropertiesSchema,),
            dynamic_parameters,
        )
        self._properties = properties_schema()

        callables_schema = type(
            self.name() + "CallablesSchema", (ProcessorCallablesSchema,), {}
        )
        self.CALLABLES = callables_schema()

    def schema_name(self):
        return self.name()


class Processor(DynamicPropertiesMixin, ObjectMapping):
    TYPE = ProcessorType

    NAME = "processor"

    PROPERTY_MAP = {
        "state": HardwareProperty("state"),
        "enabled": HardwareProperty("enabled"),
    }
    CALLABLE_MAP = {"reprocess": "reprocess"}


Default = Processor
