from typing import Any, Optional, Union
from collections.abc import Iterable, Mapping, Sequence
from itertools import chain

from pyicat_plus.metadata.definitions import (
    IcatField,
    IcatFieldGroup,
    IcatItemType,
    load_icat_fields,
)

from bliss.common.namespace_wrapper import NamespaceWrapper
from bliss.common.protocols import HasMetadataForDataset
from bliss.common.utils import autocomplete_property
from bliss.common.logtools import log_warning
from bliss.config.static import ConfigList
from bliss import global_map


class ICATmetadata:
    """Object for gathering ICAT metadata from BLISS objects."""

    _ICAT_GROUP_SEP = "."

    def __init__(self, config: Mapping):
        url = config.get("definitions", None)
        self._icat_fields = load_icat_fields(url=url)
        self._config = config
        self.resetup()

    def resetup(self) -> None:
        self.__definitions: Optional[NamespaceWrapper] = None
        self.__config_metadata_controllers: Optional[list[Any]] = None
        self.__icat_items: dict[str, IcatItemType] = dict()

    def _config_metadata_object_items(
        self,
    ) -> Iterable[tuple[Optional[str], IcatItemType, Any]]:
        """Yields objects for metadata gathering with their associated technique (if any) and ICAT item

        Note: Do not cache the results because when configured object is a controller attribute, its value may change.
        """
        for technique, adict in self._iter_config_metadata_object_dict():
            for icat_item, obj in self._iter_config_metadata_object_items(adict):
                yield technique, icat_item, obj

    def _discovered_metadata_object_items(
        self,
    ) -> Iterable[tuple[Optional[str], IcatItemType, HasMetadataForDataset]]:
        """Get Bliss session objects for the global map. Exclude the objects that appear
        in the metadata configuration.
        """
        exclude = self._config_metadata_controllers()
        for obj in global_map.protocol_iter(HasMetadataForDataset):
            if obj in exclude:
                continue
            for node_id_suffix in obj.dataset_metadata_groups():
                icat_item = self._get_icat_item_from_item_id_suffix(node_id_suffix)
                if icat_item is not None:
                    yield (None, icat_item, obj)

    def _iter_config_metadata_object_dict(
        self,
    ) -> Iterable[tuple[Optional[str], dict[str, Any]]]:
        """Yields dictionaries of objects for metadata gathering with the associated technique"""
        config_metadata_objects_dict = self._config.get("default", None)
        if config_metadata_objects_dict:
            yield None, config_metadata_objects_dict
        techniques = self._config.get("techniques", dict())
        for technique, config_metadata_objects_dict in techniques.items():
            if config_metadata_objects_dict:
                yield technique.upper(), config_metadata_objects_dict

    def _iter_config_metadata_object_items(
        self, config_metadata_objects_dict: dict[str, Any]
    ) -> Iterable[tuple[IcatItemType, Any]]:
        """Yields objects for metadata gathering with their associated ICAT item"""
        for node_id_suffix, obj in config_metadata_objects_dict.items():
            icat_item = self._get_icat_item_from_item_id_suffix(node_id_suffix)
            if icat_item is None:
                pass
            elif isinstance(obj, (list, ConfigList)):
                for obji in obj:
                    yield (icat_item, obji)
            else:
                yield (icat_item, obj)

    def _config_metadata_controllers(self) -> list[HasMetadataForDataset]:
        """Controllers that appear in the metadata configuration"""
        if self.__config_metadata_controllers is None:
            self.__config_metadata_controllers = [
                value
                for _, _, value in self._config_metadata_object_items()
                if isinstance(value, HasMetadataForDataset)
            ]
        return self.__config_metadata_controllers

    def _get_icat_item_from_item_id_suffix(
        self, node_id_suffix: str
    ) -> Optional[IcatItemType]:
        if node_id_suffix in self.__icat_items:
            return self.__icat_items[node_id_suffix]
        _item_id_suffix = node_id_suffix.split(self._ICAT_GROUP_SEP)
        icat_items = list(
            self._icat_fields.iter_items_with_node_id_suffix(_item_id_suffix)
        )
        if len(icat_items) == 1:
            icat_item = icat_items[0]
        else:
            names = [str(icat_item.info.node_id) for icat_item in icat_items]
            if names:
                log_warning(
                    self,
                    "ICAT name %r should refer to exactly one ICAT field or group. Instead it refers to these ICAT metadata items: %s",
                    node_id_suffix,
                    names,
                )
            else:
                log_warning(
                    self,
                    "ICAT name %r does not refer to any ICAT field or group. Use `session.icat_metadata.available_icat_groups` to list all available groups.",
                    node_id_suffix,
                )
            icat_item = None
        self.__icat_items[node_id_suffix] = icat_item
        return icat_item

    @autocomplete_property
    def available_icat_groups(self) -> list[str]:
        return list(self._icat_fields.iter_group_names())

    @autocomplete_property
    def available_icat_fields(self) -> list[str]:
        return list(self._icat_fields.iter_field_names())

    @autocomplete_property
    def definitions(self) -> NamespaceWrapper:
        if self.__definitions is None:
            self.__definitions = self._icat_fields.namespace()
        return self.__definitions

    def get_metadata(self, techniques: Optional[Sequence] = None) -> dict[str, Any]:
        metadata = dict()
        cache = dict()
        if techniques is None:
            techniques = set()
        else:
            techniques = {s.upper() for s in techniques}
        items = chain(
            self._config_metadata_object_items(),
            self._discovered_metadata_object_items(),
        )
        for technique, icat_item, obj in items:
            if technique is not None and technique not in techniques:
                continue
            if isinstance(obj, HasMetadataForDataset):
                if not obj.dataset_metadata_enabled:
                    continue
                if not isinstance(icat_item, IcatFieldGroup):
                    log_warning(
                        self,
                        "controllers object %r is assigned to %s. Instead if should be assigned to an ICAT group.",
                        obj,
                        icat_item,
                    )
                    continue
                obj_metadata = icat_metadata_from_device(icat_item, obj, cache=cache)
            else:
                if not isinstance(icat_item, IcatField):
                    log_warning(
                        self,
                        "object %r is assigned to %s. Instead if should implement the 'HasMetadataForDataset' protocol when it is a controller or be assigned to an ICAT field.",
                        obj,
                        icat_item,
                    )
                    continue
                obj_metadata = {icat_item.field_name: obj}
            for k, v in obj_metadata.items():
                vkeep = metadata.get(k, None)
                if vkeep is None:
                    metadata[k] = v
                elif isinstance(vkeep, list):
                    metadata[k].append(v)
                else:
                    metadata[k] = [vkeep, v]
        return metadata


def icat_metadata_from_device(
    group: IcatFieldGroup,
    metadata: Union[HasMetadataForDataset, Mapping, Any],
    cache=Optional[Mapping],
) -> dict:
    """Metadata from device to ICAT"""
    if isinstance(metadata, HasMetadataForDataset):
        try:
            if cache is None:
                obj_metadata = metadata.dataset_metadata()
            else:
                obj_metadata = cache.get(metadata.name, None)
                if obj_metadata is None:
                    obj_metadata = metadata.dataset_metadata()
                    cache[metadata.name] = obj_metadata
            metadata = obj_metadata
        except Exception:
            raise RuntimeError(f"Failed to get dataset metadata for {metadata.name}")

    result = dict()
    if metadata is None:
        return result
    for field_name, field_value in metadata.items():
        field = group.get(field_name, None)
        if isinstance(field, IcatField):
            if field is None:
                raise ValueError(f"ICAT field {field_name!r} does not exist")
            result[field.field_name] = field_value
        else:
            result.update(icat_metadata_from_device(field, field_value, cache=cache))
    return result
