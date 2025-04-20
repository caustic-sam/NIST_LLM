# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections.abc import Callable
from collections.abc import MutableSequence
import traceback
import typing
from typing import Generic, TypeVar
import functools
import importlib

from bliss.common import event
from bliss import global_map
from .typedef import ObjectType

# NOTE: It would be better to drop the following import at some point
from .endpoints.models.object import ObjectSchema, LockedDict


logger = logging.getLogger(__name__)


def get_nested_attr(obj, attr, **kw):
    attributes = attr.split(".")
    for i in attributes:
        try:
            obj = getattr(obj, i)
            if callable(obj):
                obj = obj()
        except AttributeError:
            if "default" in kw:
                return kw["default"]
            else:
                raise
    return obj


def set_nested_attr(obj, attr, value):
    attributes = attr.split(".")
    if len(attributes) == 1:
        setattr(obj, attr, value)
        return

    for i in attributes[0:-1]:
        obj = getattr(obj, i)

    setattr(obj, attributes[-1], value)


class AbstractHardwareProperty2(ABC):
    """
    Implement an automomous property.
    """

    def __init__(self, parent: ObjectMapping):
        self._object = parent
        self.__update = None

    def _connect_update(self, callback):
        """Called by daiquiri at the initialization to be informed by changes"""
        assert self.__update is None
        self.__update = callback

    def emit_update(self, value):
        """To be called by the property itself when the hardware property have changed"""
        update = self.__update
        if update is not None:
            update(value)

    @abstractmethod
    def read_hardware(self, obj):
        """Read the value from the subsystem"""
        ...

    @abstractmethod
    def write_hardware(self, obj, value):
        """Write the value to the subsystem"""
        ...

    @abstractmethod
    def connect_hardware(self, obj):
        """Do the connection of this property to the subsystem"""
        ...

    @abstractmethod
    def disconnect(self, obj):
        """Disconnect the property.

        This also have to disconnect any subsystem"""
        ...


class HardwareProperty:
    """Describe a property from the device controls system.

    Provides translation function between the controls system specific
    nomenclature and the abstract one. This can be overwritten.

    Arguments:
        name: Name used by the controls system
    """

    def __init__(self, name: str, getter: Callable | None = None):
        self._name = name
        self._getter = getter

    @property
    def name(self) -> str:
        return self._name

    @property
    def getter(self) -> Callable | None:
        return self._getter

    def translate_from(self, value):
        """Translate the value from the controls layer to the abstraction layer
        i.e for getters

        Arguments:
            value: The property value to translate.

        Returns:
            The translated value
        """
        return value

    def translate_to(self, value):
        """Translate the value to the controls layer from the abstraction layer
        i.e for setters

        Arguments:
            value: The property value to translate.

        Returns:
            The translated value
        """
        return value


class ObjectRefProperty(HardwareProperty):
    """Attribute read from BLISS as another BLISS object and exposed to Daiquiri
    as an object name

    Attributes:
        name: Name of the attribute in the remote hardware device
        compose: If true the referenced object is part of this component and will
                 automatically be exposed registered in Daiquiri and exposed to
                 the front end.
    """

    def __init__(self, name, compose: bool = False):
        HardwareProperty.__init__(self, name)
        self._compose = compose

    @property
    def compose(self) -> bool:
        """If true the referenced object compose this hardware object.

        As result if this object is registered in Daiquiri, the referenced
        object will be registered too.
        """
        return self._compose

    def translate_from(self, value):
        if value is None:
            name = ""
        elif isinstance(value, str):
            name = value
        else:
            name = value.name
        return "hardware:" + name


class ObjectRefListProperty(HardwareProperty):
    """Attribute read from BLISS as another BLISS object and exposed to Daiquiri
    as a list of object name

    Attributes:
        name: Name of the attribute in the remote hardware device
        compose: If true the referenced object is part of this component and will
                 automatically be exposed registered in Daiquiri and exposed to
                 the front end.

    """

    def __init__(self, name, compose: bool = False):
        HardwareProperty.__init__(self, name)
        self._compose = compose

    @property
    def compose(self) -> bool:
        """If true the referenced object compose this hardware object.

        As result if this object is registered in Daiquiri, the referenced
        object will be registered too.
        """
        return self._compose

    def _device_to_name(self, device):
        if device is None:
            name = ""
        elif isinstance(device, str):
            name = device
        else:
            name = device.name
        return "hardware:" + name

    def translate_from(self, value):
        if value is None:
            return []
        return [self._device_to_name(v) for v in value]


class EnumProperty(HardwareProperty):
    """Attribute read from BLISS as a python enum and exposed to Daiquiri as a
    name
    """

    def __init__(self, name: str, enum_type, getter: Callable | None = None):
        HardwareProperty.__init__(self, name, getter=getter)
        self.__enum_type = enum_type

    def translate_from(self, value):
        if isinstance(value, str):
            value = self.__enum_type(value)
        state = value.name
        return state.upper()


class CouldRaiseException(HardwareProperty):
    """
    Attribute which can raise an exception at get.

    It can be replaced by a valid value.
    """

    def handleExceptionAsValue(self, exception: Exception) -> typing.Any:
        """Return the value to use, else raise the exception."""
        raise exception


class _BaseObject(ABC):
    """Base BaseObject from which all inherit

    The base hardware object defines the objects procotol, type, its properties
    and callables schema, and mechanisms to subscribe to property changes

    Attributes:
        name: Name of the object
    """

    TYPE: type[ObjectType]

    def __init__(self, name: str):
        self._callbacks: dict[str, list[Callable]] = {}
        self._online_callbacks: list[Callable] = []
        self._locked_callbacks: list[Callable] = []
        self._locked: LockedDict | None = None
        self._online = False
        self._name: str = name
        self._alias = None
        self._user_tags: list[str] = []

    @property
    def schema_name(self) -> str:
        ty = self.TYPE.NAME
        return ty[0].upper() + ty[1:]

    def get(self, prop: str) -> typing.Any:
        """Get a property from a hardware object

        First checks the property is defined in the objects
        property schema, then delegates to the local getter
        implementation _get

        Arguments:
            prop: The property to retreive.

        Returns:
            The property value if the property exists, otherwise
            rasises an exception

        """
        if not self._online:
            return None

        if prop in self.TYPE.PROPERTIES:
            value = self._get(prop)
            return value
        else:
            raise KeyError("Unknown property `{p}`".format(p=prop))
        return None

    def set(self, prop: str, value):
        """Set a property on a hardware object

        First checks the property is defined in the objects
        property schema, then delegates to the local setter
        implementation _set

        Arguments:
            prop: The property to set.
            value: The property to set.

        Returns:
            The the result from the object setter if the property exists
            otherwise raises an exception

        """
        if not self._online:
            return

        if prop in self.TYPE.PROPERTIES:
            if self.TYPE.PROPERTIES.read_only_prop(prop):
                raise AttributeError("Property `{p}` is read only".format(p=prop))

            value = self.TYPE.PROPERTIES.validate_prop(prop, value)
            return self._set(prop, value)
        else:
            raise KeyError("Unknown property `{p}`".format(p=prop))

    def get_subobject_names(self) -> list[str]:
        """Returns a list of referenced objects own by this object."""
        return []

    def has_function(self, function_name: str) -> bool:
        """True if a call is available"""
        base_model = self.TYPE.CALLABLES.__annotations__.get(function_name)
        return base_model is not None

    def validate_call(self, function_name: str, *args: typing.Any, **kwargs):
        """Validate a function on a hardware object

        Args:
            function_name: The function to call.
            args: Function arguments
            kwargs: Function keywork arguments

        Raises:
            pydantic.ValidationError: If validation fails
        """
        base_model = self.TYPE.CALLABLES.__annotations__.get(function_name)
        if isinstance(base_model, str):
            # With future annotations or python >= 3.10
            module = importlib.import_module(self.TYPE.__module__)
            # NOTE: Actually this function is used by pydantic to complete the same thing
            base_model = typing._eval_type(
                typing.ForwardRef(base_model), module.__dict__, {}
            )

        if base_model is None:
            raise KeyError(f"Unknown function `{function_name}`")

        validator = base_model.__pydantic_validator__
        validator.validate_assignment(
            base_model.model_construct(),
            "params",
            {"args": args, "kwargs": kwargs},
        )

    def call(self, function_name: str, *args: typing.Any, **kwargs):
        """Calls a function on a hardware object

        First checks the function is defined in the objects
        callables schema, then delegates to the local call
        implementation _call

        Args:
            function_name: The function to call.
            args: The value to call the function with.

        Returns:
            The the result from the object function if the function exists
            otherwise raises an exception

        Raises:
            pydantic.ValidationError: If validation fails
        """
        if not self._online:
            return
        self.validate_call(function_name, *args, **kwargs)
        return self._call(function_name, *args, **kwargs)

    @abstractmethod
    def check_online(self) -> bool:
        """Programatic check if the object is online"""
        pass

    @abstractmethod
    def _get(self, prop: str) -> typing.Any:
        """Local implementation of getter"""
        pass

    @abstractmethod
    def _set(self, prop: str, value: typing.Any):
        """Local implementation of setter"""
        pass

    @abstractmethod
    def _call(self, function: str, *args, **kwargs):
        """Local implementation of call"""
        pass

    @property
    def state(self) -> ObjectSchema:
        """Gets the current state of a hardware object

        Builds a dictionary of the basic info of the object, plus its properties, and
        callables.

        Returns:
            A pydantic model of the hardware object status

        """
        online = self._online
        raw_properties: dict[str, typing.Any] = {}
        properties: dict[str, typing.Any] = {}
        errors = []
        if online:
            for p in self.TYPE.PROPERTIES:
                try:
                    raw_properties[p] = self.get(p)
                except Exception as e:
                    raw_properties[p] = None
                    online = False
                    errors.append(
                        {
                            "property": p,
                            "exception": str(e),
                            "traceback": "".join(traceback.format_tb(e.__traceback__)),
                        }
                    )
                    logger.exception(f"Couldn't get property `{p}` for `{self.name}`")

            try:
                properties = self.TYPE.PROPERTIES(**raw_properties).dict()
            except Exception as e:
                errors.append(
                    {
                        "exception": str(e),
                        "traceback": "".join(traceback.format_tb(e.__traceback__)),
                    }
                )
                properties = {}
                online = False

        return ObjectSchema(
            name=self._name,
            type=self.TYPE.NAME,
            properties=properties,
            online=online,
            locked=self._locked,
            errors=errors,
            alias=self.alias,
            user_tags=self.user_tags(),
        )

    def set_online(self, state: bool) -> None:
        """Set the online state of the device

        Sets the state and execute any registered callbacks

        Args:
            state (boolean): Set the online state
        """
        if self._online == state:
            return
        self._online = state

        for cb in self._online_callbacks:
            cb(self, self._online)

    def set_locked(self, reason: str | None):
        """Set the device locked for a reason.

        Argument:
            reason: Locking reason. If none the device is not locked.
        """
        locked: LockedDict | None = None
        if reason is not None:
            locked = {"reason": reason}
        if self._locked == locked:
            return
        self._locked = locked
        for cb in self._locked_callbacks:
            cb(self, self._locked)

    def subscribe_online(self, fn: Callable):
        """Subscribe to the online state of the hardware object

        Add a function to a list of callbacks for when the online state of the object change

        Args:
            fn: (:callable) The function to call when this property changes.

        """
        if not callable(fn):
            raise AttributeError("Callback function must be callable")

        if fn not in self._online_callbacks:
            self._online_callbacks.append(fn)

    def unsubscribe_online(self, fn: Callable):
        """Unsubscribe to the online state of the hardware object."""
        if fn in self._online_callbacks:
            self._online_callbacks.remove(fn)

    def subscribe_locked(self, fn):
        """Subscribe to the locked state of the hardware object

        Add a function to a list of callbacks for when the locked state of the object change

        Args:
            fn: (:callable) The function to call when this property changes.

        """
        if not callable(fn):
            raise AttributeError("Callback function must be callable")

        if fn not in self._locked_callbacks:
            self._locked_callbacks.append(fn)

    def unsubscribe_locked(self, fn: Callable):
        """Unsubscribe to the locked state of the hardware object."""
        if fn in self._locked_callbacks:
            self._locked_callbacks.remove(fn)

    @property
    def name(self) -> str:
        return self._name

    @property
    def type(self) -> str:
        return self.TYPE.NAME

    @property
    def online(self) -> bool:
        return self._online

    def locked(self) -> LockedDict | None:
        return self._locked

    @property
    def alias(self) -> typing.Optional[str]:
        return self._alias

    def user_tags(self) -> list[str]:
        return self._user_tags

    @property
    def state_ok(self) -> bool:
        """Returns if the current object state is `ok`"""
        state = self.get("state")
        if isinstance(state, list):
            st = False
            for ok in self.TYPE.STATE_OK:
                if ok in state:
                    st = True
            return st

        else:
            return state in self.TYPE.STATE_OK

    def subscribe(self, prop: str, fn):
        """Subscribe to property changes on the hardware object

        Add a function to a list of callbacks for when properties on the object change

        Arguments:
            prop: The property to subscribe to. Can pass 'all' to subscribe to all changes
            fn: (:callable) The function to call when this property changes.

        """
        if not callable(fn):
            raise AttributeError("Callback function must be callable")

        if prop in self.TYPE.PROPERTIES or prop == "all":
            if not (prop in self._callbacks):
                self._callbacks[prop] = []

            if not (fn in self._callbacks[prop]):
                self._callbacks[prop].append(fn)

        else:
            raise KeyError(f"No such property: {prop}")

    def unsubscribe(self, prop: str, fn):
        """Unsubscribe from a property change on the hardware object

        Arguments:
            prop: The property to unsubscribe from.
            fn: (:callable) The function to unsubscribe

        """
        if prop in self._callbacks:
            if fn in self._callbacks[prop]:
                # logger.debug("Unsubscribe from property %s, %s", prop, fn)
                self._callbacks[prop].remove(fn)
                return True

        return False

    def _update(
        self, name: str, prop: HardwareProperty | AbstractHardwareProperty2, value
    ):
        """Internal function to call when a property has changed

        This delegates to all subscribes that a property has changed

        Arguments:
            name: Property name of the abstract device
            prop: Property of the control system hardware
            value: The new value (from the control system).

        """
        # logger.debug('{c}._update {n} - {p}: {v}'.format(c=self.__class__.__name__, n=self._address, p=prop, v=value))
        if not isinstance(prop, AbstractHardwareProperty2):
            value = prop.translate_from(value)
        if name in self.TYPE.PROPERTIES:
            if name in self._callbacks:
                for cb in self._callbacks[name]:
                    cb(self, name, value)

            if "all" in self._callbacks:
                for cb in self._callbacks["all"]:
                    cb(self, name, value)

    def disconnect(self):
        """Disconnect the object from the service"""
        pass


OBJ = TypeVar("OBJ")


class ObjectMapping(_BaseObject, Generic[OBJ]):
    """Hardware object that maps properties via a simple dict

    `_BaseObject` that has a simple map between abstract properties and their
    actual properties on the object with fallback to a function on the parent
    """

    PROPERTY_MAP: dict[str, HardwareProperty] = {}
    CALLABLE_MAP: dict[str, str] = {}

    def __init__(self, obj: OBJ, name: str):
        _BaseObject.__init__(self, name=name)
        self._property_map: dict[
            str, HardwareProperty | AbstractHardwareProperty2
        ] = self._create_properties()
        self._callable_map: dict[str, str] = dict(self.CALLABLE_MAP)

        for prop_name, prop in self._property_map.items():
            if isinstance(prop, AbstractHardwareProperty2):
                prop._connect_update(functools.partial(self._update, prop_name, prop))

        self._online = False

        self._object: OBJ = obj

        aliases = global_map.aliases
        self._alias = aliases.get_alias(obj)

        user_tags = []
        if hasattr(self._object, "config"):
            config = self._object.config
            tags = config.get("user_tag")
            if tags is not None:
                if isinstance(tags, str):
                    user_tags.append(tags)
                elif isinstance(tags, MutableSequence):
                    for tag in tags:
                        user_tags.append(tag)
                else:
                    raise ValueError(f"Unsupported BLISS tag from object {self.name}")
        self._user_tags = user_tags

        logger.debug("Connecting to object %s", self.name)
        for name, prop in self._property_map.items():
            logger.debug("            - Property %s", name)
            if isinstance(prop, AbstractHardwareProperty2):
                prop.connect_hardware(self._object)
            else:
                event.connect(self._object, prop.name, self._event)

    @property
    def object(self) -> object:
        return self._object

    def check_online(self) -> bool:
        """Programatic check if the object is online"""
        return True

    def _create_properties(
        self,
    ) -> dict[str, HardwareProperty | AbstractHardwareProperty2]:
        """Return the properties to be used for this hardware object

        The default implementation reads the descriptions from the
        class attribute `PROPERTY_MAP`.
        """
        return dict(self.PROPERTY_MAP)

    def _create_name_from_ref_property(self, name: str) -> str | None:
        obj_name = self.get(name)
        if not obj_name.startswith("hardware:"):
            return None
        obj_name = obj_name[len("hardware:") :]
        if obj_name == "":
            return None
        return obj_name

    def _create_names_from_ref_list_property(self, name: str) -> list[str]:
        obj_names = self.get(name)
        obj_names = [
            n[len("hardware:") :] for n in obj_names if n.startswith("hardware:")
        ]
        obj_names = [n for n in obj_names if n != ""]
        return obj_names

    def _set(self, prop: str, value):
        """Set a property on the child object

        First try from the simple property map which maps properties to attributes
        on the child object. Delegates to _do_set which locally implements the setter

        Second, if not in the map, try calling the function _get_<prop> on the parent

        Args:
            prop: The property to set.
            value: Its value.
        """
        hprop = self._property_map.get(prop)
        if hprop is not None:
            if not isinstance(hprop, AbstractHardwareProperty2):
                value = hprop.translate_to(value)
            self._do_set(hprop, value)
        else:
            raise KeyError(
                f"Couldnt find a setter for property `{prop}` on `{self.name}`"
            )

    def _do_set(self, prop: HardwareProperty | AbstractHardwareProperty2, value):
        obj = self._object
        if isinstance(prop, AbstractHardwareProperty2):
            prop.write_hardware(obj, value)
        else:
            return set_nested_attr(obj, prop.name, value)

    def _get(self, prop: str):
        """Get a property from the child object

        First try from the simple property map which maps properties to attributes
        on the child object. Delegates to _do_get which locally implements the getter

        Second, if not in the map, try calling the function _get_<prop> on the parent

        Arguments:
            prop: The property to set.

        Returns:
            The property value
        """
        hprop = self._property_map.get(prop)
        if hprop is not None:
            try:
                hvalue = self._do_get(prop, hprop)
                if not isinstance(hprop, AbstractHardwareProperty2):
                    hvalue = hprop.translate_from(hvalue)
                return hvalue
            except NotImplementedError:
                logger.info("Object %s does not implement %s", self._name, prop)
        else:
            raise KeyError(
                f"Couldnt find a getter for property `{prop}` on `{self.name}`"
            )

    def _do_get(
        self, prop_name: str, prop: HardwareProperty | AbstractHardwareProperty2
    ):
        obj = self._object
        if isinstance(prop, AbstractHardwareProperty2):
            try:
                return prop.read_hardware(obj)
            except (NotImplementedError, RuntimeError):
                logger.info(
                    f"Could not get property {prop_name} from {self.name}",
                    exc_info=True,
                )
                return None

        getter = prop.getter
        if getter is not None:
            return getter(self)
        try:
            try:
                return get_nested_attr(obj, prop.name)
            except Exception as e:
                if isinstance(prop, CouldRaiseException):
                    return prop.handleExceptionAsValue(e)
                raise
        except (NotImplementedError, RuntimeError):
            logger.info(
                f"Could not get property {prop.name} from {self.name}", exc_info=True
            )
            return None

    def _call(self, function: str, *args, **kwargs):
        """Call a function on the child object

        First try from the simple function map which maps to function names
        on the child object. Delegates to _do_call which locally implements the getter

        Second, if not in the map, try calling the function _call_<fn> on the parent

        Args:
            function (str): The function to call.
            value: The value to call the function with

        Returns:
            True if function successfully called
        """
        if function in self._callable_map:
            ret = self._do_call(function, *args, **kwargs)
        elif hasattr(self, "_call_{fn}".format(fn=function)):
            ret = getattr(self, "_call_{fn}".format(fn=function))(*args, **kwargs)
        else:
            raise KeyError(
                f"Couldnt find a handler for function `{function}` on `{self.name}`"
            )

        return ret

    def _do_call(self, function, *args, **kwargs):
        fn = getattr(self._object, self._callable_map[function])
        return fn(*args, **kwargs)

    def _event(self, value, *args, signal=None, **kwargs):
        for name, prop in self._property_map.items():
            if isinstance(prop, AbstractHardwareProperty2):
                continue
            if signal == prop.name:
                self._update(name, prop, value)
                break

    def get_subobject_names(self) -> list[str]:
        """
        Create a list of for each object name compositiing
        this object.

        It is based on properties `ObjectRefProperty` and `ObjectRefListProperty`
        with the `compose` attribute to true.
        """
        names: list[str] = []
        for name, prop in self._property_map.items():
            if isinstance(prop, ObjectRefListProperty) and prop.compose:
                names.extend(self._create_names_from_ref_list_property(name))
            if isinstance(prop, ObjectRefProperty) and prop.compose:
                obj_name = self._create_name_from_ref_property(name)
                if obj_name is not None:
                    names.append(obj_name)

        return names

    def disconnect(self):
        for prop in self._property_map.values():
            if isinstance(prop, AbstractHardwareProperty2):
                prop.disconnect(self._object)
            else:
                event.disconnect(self._object, prop.name, self._event)

    def __repr__(self) -> str:
        return f"<ObjectMapping: {self.name} ({self.__class__.__name__}/{self._object.__class__.__name__})>"
