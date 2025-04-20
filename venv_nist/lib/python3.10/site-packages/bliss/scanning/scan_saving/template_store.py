from __future__ import annotations

import enum
import string
import functools
from dataclasses import dataclass
from contextlib import contextmanager

from collections.abc import Iterator
from collections.abc import Iterable
from collections.abc import Generator
from collections.abc import MutableMapping
from typing import Any, Optional


FieldLocation = enum.Enum("FieldLocation", "external,local,attribute")

_PARSE_STRING_TEMPLATE = string.Formatter().parse


@dataclass(frozen=True)
class Field:
    """To be used in the definition of a class derived from :code:`TemplateStore`."""

    default: Any = None
    mutable: bool = True
    location: FieldLocation = FieldLocation.external
    data_type: Optional[type] = str
    init: bool = True

    def __post_init__(self) -> None:
        self.validate(self.default)

    def validate(self, value: Any) -> Any:
        if self.data_type is not None and not (
            isinstance(value, self.data_type) or value is None
        ):
            raise TypeError(
                f"Expected value of type {self.data_type.__name__}, but got {type(value).__name__}."
            )
        return value


def cache_external(method):
    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        with self.cache_external():
            return method(self, *args, **kwargs)

    return wrapper


class TemplateStore(MutableMapping):
    """Class with a mapping API where every key can be a template of other keys.

    Only a fixed set of keys are allowed and they can be immutable. All keys are
    exposed as attributes so this restricts the key names.

    The values can be stored in four different locations:

    * external dictionary
    * instance dictionary
    * instance attribute
    * default `_FIELDS` values

    Setting and getting do different things: setting sets the template
    and getting resolves the template.

    Usage:

    .. code:: python

        class MyTemplateStore(TemplateStore):
            class _Fields:
                external = Field(default="a_{instance}")
                instance = Field(default="b_{myattribute}", location=FieldLocation.local)
                myattribute = Field(location=FieldLocation.attribute, mutable=False)

            @property
            def myattribute(self):
                return "c"


        external = dict()
        store = MyTemplateStore(external)
        all_resolved_values = dict(store)
        all_unresolved_values = store.unresolved_dict()

        assert store.external == store["external"] == "a_b_c"

    """

    _Fields = NotImplemented  # Only used upon class instantiation
    _FIELDS: dict[str, Field] = dict()  # Used by class instances

    @classmethod
    def __init_subclass__(cls, *args, **kwargs) -> None:
        """Merge the fields of the parent class with the filds of the derived class."""
        child_fields = {
            key: value
            for key, value in cls._Fields.__dict__.items()
            if isinstance(value, Field)
        }
        # The unmodified fields of the parent come first.
        fields = {
            key: value
            for key, value in cls._FIELDS.items()
            if getattr(cls._Fields, key, None) is not NotImplemented
            and key not in child_fields
        }
        # The new or modified fields of the child come after.
        fields.update(child_fields)
        super().__init_subclass__(*args, **kwargs)
        cls._FIELDS = fields

    def __init__(self, external_dict: MutableMapping, *args, **kwargs) -> None:
        r"""
        :param external_dict: where external fields are stored
        :\*args: normal dictionary API
        :\*kwargs: normal dictionary API
        """
        self._external_dict = external_dict
        self._external_cache = None
        self._external_cache_init = None
        self._external_cache_readonly = None

        self._instance_dict = dict()

        self._immutable_keys = set()
        self._local_keys = set()
        self._external_keys = set()
        self._attribute_keys = set()

        self._init_fields(self._FIELDS, *args, **kwargs)
        self._init_dynamic_fields()

    def _init_fields(self, fields: dict[str, Field], *args, **kwargs) -> dict[str, str]:
        external_copy = dict(self._external_dict)
        init_keys = set()

        for key, field in fields.items():
            if field.location == FieldLocation.external:
                self._external_keys.add(key)
                storage = external_copy
            elif field.location == FieldLocation.local:
                self._local_keys.add(key)
                storage = self._instance_dict
            elif field.location == FieldLocation.attribute:
                self._attribute_keys.add(key)
                storage = kwargs
            else:
                raise TypeError(str(field.location))

            if field.init:
                init_keys.add(key)
            if field.mutable:
                if field.init:
                    _ = storage.setdefault(key, field.default)
            else:
                if field.init:
                    storage[key] = field.default
                self._immutable_keys.add(key)

        # Synchronize external dictionary
        self._external_dict.update(external_copy)

        # Default field values
        for key, value in dict(*args, **kwargs).items():
            if key not in init_keys:
                continue
            try:
                self._set_raw_value(key, value)
            except Exception:
                if key in self._immutable_keys:
                    # Setting a default of an immutable attribute might fail.
                    # For example a property without setter.
                    continue
                raise

    def _init_dynamic_fields(self) -> None:
        for key in set(self._external_dict) - self._external_keys:
            value = self._external_dict[key]
            if isinstance(value, str):
                # Assume dynamically added field (see `add`)
                self.add(key, "")
            else:
                # Assume deprecated field
                del self._external_dict[key]

    def __getitem__(self, key: str) -> Any:
        """Get the resolved field value."""
        if key not in self._FIELDS:
            raise KeyError(f"Key '{key}' is not a field.")
        try:
            value = self._get_raw_value(key)
            if not isinstance(value, str):
                return value
            return self.eval_template(value)
        except Exception as e:
            raise KeyError(f"Failed getting field value '{key}'") from e

    def __setitem__(self, key: str, value: Any) -> None:
        """Set the field template value."""
        if key in self._immutable_keys:
            raise KeyError(f"Field '{key}' is immutable and cannot be modified.")
        if key not in self._FIELDS:
            raise KeyError(f"Key '{key}' is not a field.")
        try:
            self._set_raw_value(key, value)
        except Exception as e:
            raise KeyError(f"Failed setting field value '{key}'") from e

    def __delitem__(self, key: str) -> None:
        if key in self._immutable_keys:
            raise KeyError(f"Field '{key}' is immutable and cannot be deleted.")

        try:
            if key in self._external_keys:
                del self._external_dict_or_cache[key]
                return
            if key in self._local_keys:
                del self._instance_dict[key]
                return
        except Exception as e:
            raise KeyError(f"Failed deleting field '{key}'") from e

        if key in self._attribute_keys:
            raise KeyError(f"Field '{key}' is attribute cannot be deleted.")
        raise KeyError(f"Key '{key}' is not a field.")

    def __iter__(self) -> Iterator[str]:
        return iter(self._FIELDS)

    def __len__(self) -> int:
        return len(self._FIELDS)

    def _get_raw_value(self, key: str) -> Any:
        """Get the field value from a location depending on the field type."""
        if key in self._attribute_keys:
            return self._get_raw_attribute(key)
        if key in self._immutable_keys:
            return self._FIELDS[key].default
        if key in self._external_keys:
            return self._external_dict_or_cache[key]
        if key in self._local_keys:
            return self._instance_dict[key]
        return f"{{{key}}}"

    def _set_raw_value(self, key: str, value: Any) -> None:
        field = self._FIELDS[key]
        value = field.validate(value)

        if key in self._external_keys:
            self._external_dict_or_cache[key] = value
        elif key in self._local_keys:
            self._instance_dict[key] = value
        elif key in self._attribute_keys:
            self._set_raw_attribute(key, value)
        else:
            raise KeyError(f"Key '{key}' is not a field.")

    def _get_raw_attribute(self, attr: str) -> Any:
        return super().__getattribute__(attr)

    def _set_raw_attribute(self, attr: str, value: Any) -> None:
        super().__setattr__(attr, value)

    @cache_external
    def _get_raw_attribute_dict(self) -> dict[str, Any]:
        return {
            key: self._get_raw_attribute(key)
            for key, field in self._FIELDS.items()
            if field.location == FieldLocation.attribute
        }

    def _get_raw_default_dict(self) -> dict[str, Any]:
        return {key: field.default for key, field in self._FIELDS.items()}

    @cache_external
    def unresolved_dict(self) -> dict[str, Any]:
        # attributes, external and instance are disjoint so order does not matter
        return {
            **self._get_raw_default_dict(),
            **self._get_raw_attribute_dict(),
            **self._external_dict_or_cache,
            **self._instance_dict,
        }

    def eval_template(self, template: str) -> str:
        """Resolve a template string."""
        cache = dict()
        prev_cache = dict()
        while True:
            for key in self._string_template_keys(template):
                if key not in cache:
                    cache[key] = self._get_raw_value(key)

            if cache == prev_cache:
                break
            prev_cache = cache.copy()

            try:
                formatted = template.format_map(cache)
            except KeyError:
                break
            if formatted == template:
                break

            template = formatted

        return template

    @staticmethod
    def _string_template_keys(template: str) -> set:
        """Extract keys from a template string."""
        return {
            name
            for _, name, _, _ in _PARSE_STRING_TEMPLATE(template)
            if name is not None
        }

    def __getattribute__(self, attr: str) -> Any:
        """Get the resolved field value or a normal attribute."""
        if attr != "_FIELDS" and attr in self._FIELDS:
            try:
                return self[attr]
            except Exception as e:
                raise AttributeError(f"can't get attribute '{attr}'") from e
        return super().__getattribute__(attr)

    def __setattr__(self, attr: str, value: Any) -> None:
        """Set the field value or a normal attribute."""
        if attr in self._FIELDS:
            try:
                self[attr] = value
            except Exception as e:
                raise AttributeError(f"can't set attribute '{attr}'") from e
        super().__setattr__(attr, value)

    def __dir__(self) -> Iterable[str]:
        # For autocompletion
        mappingapi = set(dir(TemplateStore))
        attrs = set(super().__dir__())
        fields = set(self._FIELDS)
        exposed = (attrs | fields) - mappingapi
        return (key for key in exposed if not key.startswith("_"))

    def __info__(self) -> str:
        """BLISS REPL representation"""
        data = self.unresolved_dict()
        rep_str = "Parameters (default) -\n\n"
        if not data:
            return rep_str

        max_len = max(len(key) for key in data.keys() if not key.startswith("_"))
        str_format = f"  .{{:<{max_len}}} = {{!r}}\n"
        for key, value in data.items():
            if key.startswith("_"):
                continue
            rep_str += str_format.format(key, value)

        return rep_str

    @contextmanager
    def cache_external(self) -> Generator[None, None, None]:
        """All operations to the external storage happen locally inside this re-entrant context."""
        cache = self._external_cache is None
        if cache:
            self._external_cache = dict(self._external_dict)
            self._external_cache_init = dict(self._external_cache)
        try:
            yield
        finally:
            if cache:
                self._push_external_cache()
                self._external_cache = None
                self._external_cache_init = None

    def _push_external_cache(self) -> None:
        """When the external storage is cached locally in, push it to the external storage when
        there are changes with respect to the moment the caching started or the last push."""
        if self._external_cache is None:
            return
        if self._external_cache == self._external_cache_init:
            return
        self._external_dict.update(self._external_cache)
        for key in set(self._external_cache_init) - set(self._external_cache):
            del self._external_dict[key]
        self._external_cache_init = dict(self._external_cache)

    @property
    def _external_dict_or_cache(self) -> dict:
        if self._external_cache is None:
            return self._external_dict
        return self._external_cache

    def copy(self) -> "TemplateStore":
        kwargs = self.unresolved_dict()
        return self.__class__(self._external_dict, **kwargs)

    def add(self, key: str, default: Any = None) -> None:
        """Add a persistent string field dynamically."""
        if key in self._FIELDS:
            return
        field = Field(location=FieldLocation.external, default=default)
        new_fields = {key: field}
        self._FIELDS = {**self._FIELDS, **new_fields}
        self._init_fields(new_fields)
