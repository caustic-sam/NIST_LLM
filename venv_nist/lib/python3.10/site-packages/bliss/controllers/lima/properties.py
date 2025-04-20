# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

import re
import inspect
from enum import Enum
from typing import Any, Optional
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from tango import AttrWriteType, AttributeInfo

from bliss.common.tango import DevFailed
from bliss.common.tango import DeviceProxy
from bliss.config.beacon_object import BeaconObject


class LimaProperty(property):
    pass


class LimaDeferredWriteProperty(BeaconObject._property):
    pass


def camel_to_snake(camelCasedStr: str) -> str:
    """Convert a string with camelCase to a string with snake_case."""
    first_cap_re = re.compile(r"(.)([A-Z][a-z]+)")
    all_cap_re = re.compile(r"([a-z0-9])([A-Z])")
    sub1 = first_cap_re.sub(r"\1_\2", camelCasedStr)
    snake_cased_str = all_cap_re.sub(r"\1_\2", sub1).lower()
    return snake_cased_str.replace("__", "_")


def raise_tango_enum_error(attr: str, value: Any, values_enum: Enum) -> None:
    raise ValueError(
        value,
        "'%s` only accepts following values: %s"
        % (attr, ", ".join([x.name for x in list(values_enum)])),
    )


def tango_attribute_to_python(
    attr: str, value: Any, values_enum: Optional[Enum] = None
) -> Any:
    if values_enum:
        if value not in values_enum.__members__:
            raise_tango_enum_error(attr, value, values_enum)
    return value


def python_to_tango_attribute(
    attr: str, value: Any, values_enum: Optional[Enum] = None
) -> Any:
    if not values_enum:
        return value
    if isinstance(value, Enum):
        if value not in values_enum:
            raise_tango_enum_error(attr, value, values_enum)
        return value.value
    else:
        try:
            return values_enum[value].value
        except (TypeError, KeyError):
            raise_tango_enum_error(attr, value, values_enum)


def read_lima_attribute(
    proxy: DeviceProxy,
    attr: str,
    values_enum: Optional[Enum] = None,
    raise_on_error: bool = True,
) -> Any:
    try:
        value = proxy.read_attribute(attr).value
    except DevFailed as exc:
        if raise_on_error:
            raise
        print(f"Error reading lima property {attr} ({exc.args[0].desc.strip()})")
    else:
        return tango_attribute_to_python(attr, value, values_enum=values_enum)


def write_lima_attribute(
    proxy: DeviceProxy,
    attr: str,
    value: Any,
    values_enum: Optional[Enum] = None,
    raise_on_error: bool = True,
) -> Any:
    value = python_to_tango_attribute(attr, value, values_enum=values_enum)
    try:
        return proxy.write_attribute(attr, value)
    except DevFailed as exc:
        if raise_on_error:
            raise
        print(f"Error writing lima property {attr} ({exc.args[0].desc.strip()})")
    return value


@dataclass(frozen=True, eq=True)
class LimaAttrInfo:
    attribute_name: str
    property_name: str
    readable: bool
    writable: bool
    values_enum: Optional[Enum] = None

    def create_enum_getter(self):
        def fget(owner):
            return self.values_enum

        fget.__name__ = self.values_enum.__name__
        return fget

    def create_lima_getter(self, proxy):
        def fget(owner):
            return read_lima_attribute(
                proxy,
                self.attribute_name,
                values_enum=self.values_enum,
                raise_on_error=False,
            )

        fget.__name__ = self.property_name
        return fget

    def create_lima_setter(self, proxy):
        if proxy is None:

            def fset(owner, value):
                return python_to_tango_attribute(
                    self.attribute_name, value, values_enum=self.values_enum
                )

        else:

            def fset(owner, value):
                return write_lima_attribute(
                    proxy,
                    self.attribute_name,
                    value,
                    values_enum=self.values_enum,
                    raise_on_error=False,
                )

        fset.__name__ = self.property_name
        return fset


WRITABLE = AttrWriteType.READ_WITH_WRITE, AttrWriteType.READ_WRITE


def tango_to_lima_attr_info(
    proxy: DeviceProxy, attr_info: AttributeInfo, property_name: str
) -> LimaAttrInfo:
    attribute_name = attr_info.name

    values_enum = None
    if attr_info.data_format == 0 and attr_info.data_type == 8:
        # SCALAR, DevString
        try:
            possible_values = proxy.getAttrStringValueList(attribute_name)
        except AttributeError:
            # 'getAttrStringValueList' is not present in Lima device
            pass
        else:
            if possible_values:
                values_enum = Enum(
                    property_name + "_enum",
                    [(v.replace(" ", "_"), v) for v in possible_values],
                    type=str,
                )

    return LimaAttrInfo(
        attribute_name=attribute_name,
        property_name=property_name,
        readable=True,
        writable=attr_info.writable in WRITABLE,
        values_enum=values_enum,
    )


def iter_lima_attributes(
    proxy: DeviceProxy,
    prefix: Optional[str] = None,
    strip_prefix: Optional[bool] = False,
) -> Iterator[LimaAttrInfo]:
    for attr_info in proxy.attribute_list_query():
        attribute_name = attr_info.name
        if attribute_name in (
            "image_events_push_data",
            "image_events_max_rate",
            "last_image",
        ):
            continue
        if prefix is not None and not attribute_name.startswith(prefix):
            continue
        property_name = attribute_name
        if strip_prefix and prefix:
            property_name = property_name[len(prefix) :]
        property_name = camel_to_snake(property_name)
        yield tango_to_lima_attr_info(proxy, attr_info, property_name)


def iter_lima_properties(cls: Any, proxy: DeviceProxy) -> Iterable[LimaAttrInfo]:
    for property_name, prop in inspect.getmembers(
        cls, lambda x: isinstance(x, (LimaProperty, LimaDeferredWriteProperty))
    ):
        try:
            attr_info = proxy.get_attribute_config(property_name)
        except Exception:
            attr_info = LimaAttrInfo(
                attribute_name=property_name,
                property_name=property_name,
                readable=True,
                writable=prop.fset is not None,
            )
        else:
            attr_info = tango_to_lima_attr_info(proxy, attr_info, property_name)
        yield attr_info


def ensure_lima_properties(
    cls, proxy: DeviceProxy, attr_iterator: Iterable
) -> Iterable[tuple[LimaAttrInfo, property]]:
    """Loop over the attributes and verify the associated
    lima properties. Add lima properties when missing.
    """
    deferred_tango_setting = issubclass(cls, LimaAttributesAsDeferredWriteProperties)
    for attr_info in attr_iterator:
        # Add the associated enum property to the class
        if attr_info.values_enum:
            enum_prop_name = attr_info.values_enum.__name__
            if not hasattr(cls, enum_prop_name):
                prop = property(fget=attr_info.create_enum_getter())
                setattr(cls, enum_prop_name, prop)

        # Yield the lima property when implemented explicitly
        prop = getattr(cls, attr_info.property_name, None)
        if isinstance(prop, (LimaProperty, LimaDeferredWriteProperty)):
            yield attr_info, prop
            continue
        elif prop is not None:
            raise TypeError(
                prop, "Must be 'LimaProperty' or 'LimaDeferredWriteProperty'"
            )

        # Add the lima property to the class and yield it
        if attr_info.readable:
            fget = attr_info.create_lima_getter(proxy)
        else:

            def fget(_):
                return None

        if attr_info.writable:
            if deferred_tango_setting:
                fset = attr_info.create_lima_setter(None)
                property_class = LimaDeferredWriteProperty
            else:
                fset = attr_info.create_lima_setter(proxy)
                property_class = LimaProperty
        else:
            fset = None
            property_class = LimaProperty

        prop = property_class(fget, fset)
        setattr(cls, attr_info.property_name, prop)
        yield attr_info, prop


class LimaAttributesAsProperties:
    """Class that exposes Lima tango attributes as class properties for getting
    and setting (if writable).

    Note: attribute enum's are also exposed as properties.

    Optionally: expose only the Lima tango attributes with a certain prefix.
    property name can be with or without the prefix. For example:

    .. code
        class MyClass(
            LimaAttributesAsProperties,
            proxy=proxy,
            prefix="acc_",
            strip_prefix=True
            ):
            pass

    """

    _LIMA_PROPERTIES = dict()

    def __init_subclass__(
        subcls,
        proxy: DeviceProxy = None,
        prefix: Optional[str] = None,
        strip_prefix: Optional[bool] = False,
        **kw,
    ) -> None:
        super().__init_subclass__(**kw)
        if proxy is not None:
            subcls._register_lima_attributes_as_properties(
                proxy, prefix=prefix, strip_prefix=strip_prefix
            )

    @classmethod
    def _register_lima_attributes_as_properties(
        cls,
        proxy: DeviceProxy,
        prefix: Optional[str] = None,
        strip_prefix: Optional[bool] = False,
    ) -> None:
        """Expose lima attributes as class properties. Only
        expose the attributes with a certain prefix (if any).
        The property name can be with or without the prefix.

        Note: attribute enum's are also exposed as properties.
        """
        attr_iterator = iter_lima_properties(cls, proxy)
        lima_properties = dict(ensure_lima_properties(cls, proxy, attr_iterator))
        attr_iterator = iter_lima_attributes(
            proxy, prefix=prefix, strip_prefix=strip_prefix
        )
        lima_properties.update(ensure_lima_properties(cls, proxy, attr_iterator))
        cls._LIMA_PROPERTIES = lima_properties

    def __info__(self):
        display_list = []
        for attr_info, prop in self._LIMA_PROPERTIES.items():
            pname = attr_info.property_name
            try:
                value = prop.fget(self)
            except DevFailed as exc:
                display_list.append(f"{pname} = ? ({exc.args[0].desc.strip()})")
            else:
                if value is None:
                    display_list.append(f"{pname} = ? (failed to read attribute)")
                else:
                    display_v = f"{value if isinstance(value, Enum) else repr(value)}"
                    display_list.append(
                        f"{pname}: {display_v if attr_info.writable else '|%s|' % display_v}"
                    )

        return "\n".join(display_list)

    def to_dict(
        self,
        tango_names: Optional[bool] = False,
        writeable_only: Optional[bool] = False,
        exclude_properties: Optional[list[str]] = None,
        include_properties: Optional[list[str]] = None,
    ) -> dict:
        """Dictionary of all Lima properties { name: value }

        tango_names: use the tango names (from device server) or use property names (defined in BLISS)
        writeable_only: only returns writable attributes
        exclude_properties: list of property names to be excluded from returned dictionary
        include_properties: list of properties to be included to returned dictionary (takes over exclude_properties)
        """
        props = {
            attr_info.attribute_name if tango_names else attr_info.property_name: prop
            for attr_info, prop in self._LIMA_PROPERTIES.items()
            if not writeable_only or attr_info.writable
        }
        ret = {}

        if include_properties:
            for name in include_properties:
                prop = props[name]
                ret[name] = prop.fget(self)
        else:
            for name, prop in props.items():
                if exclude_properties and name in exclude_properties:
                    continue
                ret[name] = prop.fget(self)

        return ret


class LimaAttributesAsDeferredWriteProperties(
    LimaAttributesAsProperties, BeaconObject, proxy=None
):
    """Class that exposes Lima tango attributes as class properties for getting
    and setting (if writable).

    As opposed to `LimaAttributesAsProperties` the setting of writable properties
    is deferred (in Redis) and does not change Lima immediately. You can use
    `apply` to update Lima.
    """

    @BeaconObject.lazy_init
    def to_dict(self, **kw) -> dict:
        return super().to_dict(**kw)

    def dict_for_tango_update(self):
        return self.to_dict(tango_names=True, writeable_only=True)

    def apply(self, proxy: DeviceProxy):
        for attribute_name, redis_value in self.dict_for_tango_update().items():
            setattr(proxy, attribute_name, redis_value)

    def store(self, proxy: DeviceProxy):
        for attr_info in self._LIMA_PROPERTIES:
            if attr_info.writable:
                tango_value = getattr(proxy, attr_info.attribute_name)
                setattr(self, attr_info.property_name, tango_value)
