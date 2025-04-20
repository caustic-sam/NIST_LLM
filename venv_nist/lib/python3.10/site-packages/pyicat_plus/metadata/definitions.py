import re
from enum import Enum
from io import StringIO
from xml.etree import ElementTree
from dataclasses import dataclass
from collections.abc import Mapping, Sequence
from typing import Any, Callable, Dict, Iterable, Iterator, Optional, Union

import requests
import icat_esrf_definitions

from .namespace_wrapper import NamespaceWrapper


IcatNodeIdLike = Union[str, Iterable[str]]
IcatItemType = Union["IcatField", "IcatFieldGroup"]
IcatCategory = Enum(
    "IcatCategory",
    "other technique instrument sample positioner note slit attenuator detector",
)
NX_CLASS_TO_CATEGORY = {
    "NXsubentry": IcatCategory.technique,
    "NXpositioner": IcatCategory.positioner,
    "NXinstrument": IcatCategory.instrument,
    "NXsample": IcatCategory.sample,
    "NXnote": IcatCategory.note,
    "NXslit": IcatCategory.slit,
    "NXattenuator": IcatCategory.attenuator,
    "NXdetector": IcatCategory.detector,
}


class IcatNodeId(Sequence):
    """Identifier of an item in the ICAT metadata tree."""

    def __init__(self, iterable: Optional[IcatNodeIdLike] = None) -> None:
        self._sequence = tuple(self._parse_init(iterable))

    def _parse_init(self, iterable: Optional[IcatNodeIdLike] = None) -> Iterable[str]:
        if iterable is None:
            return
        if isinstance(iterable, str):
            yield iterable
            return
        for s in iterable:
            if not isinstance(s, str):
                raise TypeError(s)
            if s:
                yield s

    def __getitem__(self, idx) -> str:
        value = self._sequence[idx]
        if isinstance(value, tuple):
            return type(self)(value)
        return value

    def __len__(self) -> int:
        return len(self._sequence)

    def __str__(self) -> str:
        return ".".join(self)

    def __repr__(self) -> str:
        return repr(str(self))

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, type(self)):
            return False
        return self._sequence == other._sequence

    def __add__(self, other: IcatNodeIdLike):
        if isinstance(other, str):
            other = (other,)
        elif not isinstance(other, tuple):
            other = tuple(other)
        return type(self)(self._sequence + other)

    def endswith(self, suffix: IcatNodeIdLike):
        if isinstance(suffix, str):
            suffix = (suffix,)
        elif not isinstance(suffix, tuple):
            suffix = tuple(suffix)
        return self._sequence[-len(suffix) :] == suffix


def as_icatnodeid(node_id: IcatNodeIdLike) -> IcatNodeId:
    if isinstance(node_id, IcatNodeId):
        return node_id
    else:
        return IcatNodeId(node_id)


@dataclass(frozen=True, repr=True, eq=True, order=True)
class IcatField:
    """Description of a single ICAT database field"""

    name: str
    field_name: str
    parent: IcatNodeId
    nxtype: str
    description: Optional[str]
    units: Optional[str]

    @property
    def node_id(self) -> IcatNodeId:
        """Node identifier in the metadata tree"""
        return self.parent + self.name

    @property
    def info(self) -> "IcatField":
        return self


@dataclass(frozen=True, repr=True, eq=True, order=True)
class IcatGroup:
    """Description of a group of ICAT database items"""

    name: str
    parent: IcatNodeId
    NX_class: str

    @property
    def node_id(self) -> IcatNodeId:
        """Node identifier in the metadata tree"""
        return self.parent + self.name

    @property
    def category(self) -> IcatCategory:
        return NX_CLASS_TO_CATEGORY.get(self.NX_class, IcatCategory.other)

    @property
    def info(self) -> "IcatGroup":
        return self


class IcatFieldGroup(Mapping):
    """A group of ICAT database items"""

    def __init__(self, info: IcatGroup, nodes: Dict[str, IcatItemType]) -> None:
        self.__info = info
        self.__nodes = nodes

    def __getitem__(self, node_id: IcatNodeIdLike) -> IcatItemType:
        node_id = as_icatnodeid(node_id)
        adict = self.__nodes
        for name in node_id:
            adict = adict[name]
        return adict

    def __iter__(self) -> Iterator[str]:
        return self.__nodes.__iter__()

    def __len__(self) -> int:
        return self.__nodes.__len__()

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self.info!r})"

    @property
    def info(self) -> IcatGroup:
        return self.__info

    def iter_fields(self) -> Iterable[IcatField]:
        for icat_item in self.values():
            if isinstance(icat_item, IcatField):
                yield icat_item
            else:
                yield from icat_item.iter_fields()

    def iter_groups(self) -> Iterable["IcatFieldGroup"]:
        cls = type(self)
        for icat_item in self.values():
            if isinstance(icat_item, cls):
                yield icat_item
                yield from icat_item.iter_groups()

    def iter_group_names(self) -> Iterable[str]:
        for group in self.iter_groups():
            yield str(group.info.node_id)

    def iter_field_names(self) -> Iterable[str]:
        for field in self.iter_fields():
            yield str(field.node_id)

    def get_field_with_field_name(self, field_name: str) -> Optional[IcatField]:
        for field in self.iter_fields():
            if field.field_name == field_name:
                return field

    def iter_groups_with_type(
        self, categories: Union[IcatCategory, str, Iterable[Union[IcatCategory, str]]]
    ) -> Iterable["IcatFieldGroup"]:
        if isinstance(categories, (IcatCategory, str)):
            categories = {categories}
        categories = {
            (
                category
                if isinstance(category, IcatCategory)
                else IcatCategory.__members__[category]
            )
            for category in categories
        }
        for group in self.iter_groups():
            if group.info.category in categories:
                yield group

    def iter_items_with_node_id_suffix(
        self, node_id_suffix: IcatNodeIdLike
    ) -> Iterable["IcatFieldGroup"]:
        node_id_suffix = as_icatnodeid(node_id_suffix)
        cls = type(self)
        for icat_item in self.values():
            if icat_item.info.node_id.endswith(node_id_suffix):
                yield icat_item
            elif isinstance(icat_item, cls):
                yield from icat_item.iter_items_with_node_id_suffix(node_id_suffix)

    def namespace(
        self,
        getter: Optional[Callable[[Any, str], Any]] = None,
        setter: Optional[Callable[[Any, str, Any], None]] = None,
        property_decorator: Optional[Callable] = None,
    ) -> NamespaceWrapper:
        adict = dict()

        if setter is None:
            wrap_setter = None
        else:

            def wrap_setter(key, value):
                field = adict.get(key, None)
                if isinstance(field, NamespaceWrapper):
                    raise AttributeError(f"'{key}' is not an ICAT field")
                setter(field.field_name, value)

        def wrap_getter(key):
            value = adict[key]
            if getter is None:
                return value
            elif isinstance(value, NamespaceWrapper):
                return value
            else:
                return getter(value.info.field_name)

        cls = type(self)
        for k, v in self.items():
            if isinstance(v, cls):
                adict[k] = v.namespace(
                    getter=getter, setter=setter, property_decorator=property_decorator
                )
            else:
                adict[k] = v
        return NamespaceWrapper(
            property_names=list(adict),
            property_decorator=property_decorator,
            getter=wrap_getter,
            setter=wrap_setter,
        )

    def valid_field_name(self, field_name: str) -> bool:
        for field in self.iter_fields():
            if field.field_name == field_name:
                return True
        return False


ICAT_FIELD_NAME = re.compile(r"^\s*\$\{(.+)\}\s*$")


def load_group(in_node: ElementTree.Element, **node_attributes) -> IcatFieldGroup:
    """Convert an XML node to an `IcatFieldGroup` instance"""
    fields = list()
    groups = list()
    for element in in_node:
        if element.text is None:
            match = None
            assert element.tag == "link", f"Found {element.tag}"
            continue
        match = ICAT_FIELD_NAME.match(element.text)
        if match:
            assert element.tag != "group"
            field_name = match.groups()[0]
            fields.append((element.tag, field_name, element.attrib))
        else:
            assert element.tag == "group", f"Found {element.tag}, {element.text}"
            group_name = element.attrib["groupName"]
            groups.append((group_name, element))

    info = IcatGroup(**node_attributes)
    parent = info.node_id
    nodes = dict()

    for name, field_name, attrs in fields:
        icat_item = IcatField(
            name=name,
            field_name=field_name,
            parent=parent,
            nxtype=attrs["NAPItype"],
            description=attrs.get("ESRF_description"),
            units=attrs.get("units"),
        )
        nodes[icat_item.name] = icat_item
    for group_name, element in groups:
        icat_item = load_group(
            element, name=group_name, parent=parent, NX_class=element.attrib["NX_class"]
        )
        nodes[icat_item.info.name] = icat_item

    return IcatFieldGroup(info, nodes)


def icat_fields_source(url: Optional[str] = None) -> Union[str, StringIO]:
    """Get XML description of all ICAT fields from a URL.

    :param url: supports filenames or strings with the scheme prefix "file://",
                "xml://" or anything the `requests` library can handle.
    :returns: filename or a file object
    """
    if not url:
        return icat_esrf_definitions.DEFINITIONS_FILE
    if re.match(r"[a-z]+://", url) is None:
        return url
    if url.startswith("file://") or url.startswith("xml://"):
        return re.sub(r"^[a-z]+://", "", url)
    try:
        response = requests.get(url)
        response.raise_for_status()
    except Exception:
        working_url = "https://gitlab.esrf.fr/icat/hdf5-master-config/-/raw/master/src/icat_esrf_definitions/hdf5_cfg.xml"
        raise RuntimeError(
            "The ICAT definitions URL is wrong. Do not specify a URL to fall back to the"
            f" definitions from the 'icat-esrf-definitions' package or use '{working_url}'"
        )
    return StringIO(response.text)


def load_icat_fields(url: Optional[str] = None) -> IcatFieldGroup:
    """Returns an object which allows browsing the ICAT metadata definitions.

    :param url: supports filenames or strings with the scheme prefix
                "file://", "xml://" or anything the `requests` library can handle.
    :returns: a mapping object representing the tree relationship of ICAT fields
    """
    tree = ElementTree.parse(icat_fields_source(url=url))
    in_node = tree.getroot()
    return load_group(in_node, name="", parent=IcatNodeId(), NX_class="NXentry")
