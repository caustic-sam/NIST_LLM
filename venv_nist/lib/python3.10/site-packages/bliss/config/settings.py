# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

from contextlib import contextmanager
from functools import wraps
import weakref
import pickle
import logging
import numpy
from bliss.config.conductor.client import get_redis_proxy
from bliss.redis.scripting import register_script, evaluate_script
from bliss.common.utils import Undefined, natural_sort, auto_coerce


logger = logging.getLogger(__name__)


class InvalidValue:
    __slot__ = []

    def __str__(self):
        raise ValueError

    def __repr__(self):
        return "#ERR"


class DefaultValue:
    def __init__(self, wrapped_value):
        self.__value = wrapped_value

    @property
    def value(self):
        return self.__value


def pickle_loads(var):
    if var is None:
        return None
    try:
        return pickle.loads(var)
    except Exception:
        return InvalidValue()


def ttl_func(cnx, name, value=-1):
    if value is None:
        return cnx.persist(name)
    elif value == -1:
        return cnx.ttl(name)
    else:
        return cnx.expire(name, value)


def read_decorator(func):
    @wraps(func)
    def _read(self, *args, **keys):
        value = func(self, *args, **keys)
        if self._read_type_conversion:
            if isinstance(value, list):
                value = [self._read_type_conversion(x) for x in value]
            elif isinstance(value, dict):
                for k, v in value.items():
                    value[k] = self._read_type_conversion(v)
                if hasattr(self, "default_values") and isinstance(
                    self.default_values, dict
                ):
                    tmp = dict(self._default_values)
                    tmp.update(value)
                    value = tmp
            else:
                if isinstance(value, DefaultValue):
                    value = value.value
                elif value is not None:
                    value = self._read_type_conversion(value)
        if value is None:
            if hasattr(self, "_default_value"):
                value = self._default_value
            elif hasattr(self, "_default_values") and hasattr(
                self._default_values, "get"
            ):
                value = self._default_values.get(args[0])
        return value

    return _read


def write_decorator_dict(func):
    @wraps(func)
    def _write(self, values, **keys):
        if self._write_type_conversion:
            if not isinstance(values, dict) and values is not None:
                raise TypeError("can only be dict")

            if values is not None:
                new_dict = dict()
                for k, v in values.items():
                    new_dict[k] = self._write_type_conversion(v)
                values = new_dict
        return func(self, values, **keys)

    return _write


def write_decorator_multiple(func):
    @wraps(func)
    def _write(self, values, **keys):
        if self._write_type_conversion:
            if (
                not isinstance(values, (list, tuple, numpy.ndarray))
                and values is not None
            ):
                raise TypeError("Can only be tuple, list or numpy array")
            if values is not None:
                values = [self._write_type_conversion(x) for x in values]
        return func(self, values, **keys)

    return _write


def write_decorator(func):
    @wraps(func)
    def _write(self, value, **keys):
        if self._write_type_conversion and value is not None:
            value = self._write_type_conversion(value)
        return func(self, value, **keys)

    return _write


def scan(match="*", count=1000, connection=None):
    if connection is None:
        connection = get_redis_proxy()
    cursor = 0
    while 1:
        cursor, values = connection.scan(cursor=cursor, match=match, count=count)
        for val in values:
            yield val.decode()
        if int(cursor) == 0:
            break


@contextmanager
def pipeline(*settings):
    """
    Contextmanager which create a redis pipeline to group redis commands
    on settings.

    IN CASE OF you execute the pipeline, it will return raw database values
    (byte strings).
    """
    if not all(isinstance(setting, (BaseSetting, Struct)) for setting in settings):
        raise TypeError("Can only group commands for BaseSetting objects")

    # check they have the same connection
    connections = set(setting._cnx() for setting in settings)
    if len(connections) > 1:
        raise RuntimeError("Cannot group redis commands in a pipeline")
    # save the connection
    cnx = connections.pop()
    cnx_ref = weakref.ref(cnx)
    # make a pipeline from the connection
    pipeline = cnx.pipeline()
    pipeline_ref = weakref.ref(pipeline)

    for setting in settings:
        setting._cnx = pipeline_ref

    # replace settings connection with the pipeline
    try:
        yield pipeline
    finally:
        for setting in settings:
            setting._cnx = cnx_ref

    pipeline.execute()


class BaseSetting:
    def __init__(self, name, connection, read_type_conversion, write_type_conversion):
        self._name = name
        if connection is None:
            connection = get_redis_proxy()
        self.__cnx = weakref.ref(connection)
        self._read_type_conversion = read_type_conversion
        self._write_type_conversion = write_type_conversion

    @property
    def name(self):
        return self._name

    def ttl(self, value=-1):
        """
        Set the time to live (ttl) for settings object.
        value -- == -1 default value means read what is the current ttl
        value -- is None mean persistent
        value -- >= 0 set the time to live a this setting in second.
        """
        return ttl_func(self.connection, self.name, value)

    def clear(self):
        """
        Remove all elements from this settings
        """
        self.connection.delete(self.name)

    @property
    def _cnx(self):
        if self.__cnx is None or self.__cnx() is None:
            raise RuntimeError("Connection to Redis lost, Bliss should be restarted")
        return self.__cnx

    @_cnx.setter
    def _cnx(self, value):
        self.__cnx = value

    @property
    def connection(self):
        return self._cnx()


class SimpleSetting(BaseSetting):
    """
    Class to manage a setting that is stored as a string on Redis
    """

    def __init__(
        self,
        name,
        connection=None,
        read_type_conversion=auto_coerce,
        write_type_conversion=str,
        default_value=None,
    ):
        super().__init__(name, connection, read_type_conversion, write_type_conversion)
        self._default_value = default_value

    @read_decorator
    def get(self):
        value = self.connection.get(self.name)
        return value

    @write_decorator
    def set(self, value):
        self.connection.set(self.name, value)

    def __add__(self, other):
        value = self.get()
        if isinstance(other, SimpleSetting):
            other = other.get()
        return value + other

    def __iadd__(self, other):
        cnx = self.connection
        if cnx is not None:
            if isinstance(other, int):
                if other == 1:
                    cnx.incr(self.name)
                else:
                    cnx.incrby(self.name, other)
            elif isinstance(other, float):
                cnx.incrbyfloat(self.name, other)
            else:
                cnx.append(self.name, other)
            return self

    def __isub__(self, other):
        if isinstance(other, str):
            raise TypeError(
                "unsupported operand type(s) for -=: %s" % type(other).__name__
            )
        return self.__iadd__(-other)

    def __getitem__(self, ran):
        cnx = self.connection
        if cnx is not None:
            step = None
            if isinstance(ran, slice):
                i, j = ran.start, ran.stop
                step = ran.step
            elif isinstance(ran, int):
                i = j = ran
            else:
                raise TypeError("indices must be integers")

            value = cnx.getrange(self.name, i, j)
            if step is not None:
                value = value[0:-1:step]
            return value

    def __repr__(self):
        value = self.connection.get(self.name)
        return "<SimpleSetting name=%s value=%s>" % (self.name, value)


class SimpleSettingProp(BaseSetting):
    """
    A python's property implementation for SimpleSetting
    To be used inside user defined classes
    """

    def __init__(
        self,
        name,
        connection=None,
        read_type_conversion=auto_coerce,
        write_type_conversion=str,
        default_value=None,
        use_object_name=True,
    ):
        super().__init__(name, connection, read_type_conversion, write_type_conversion)
        self._default_value = default_value
        self._use_object_name = use_object_name

    def __get__(self, obj, type=None):
        if self._use_object_name:
            name = obj.name + ":" + self.name
        else:
            name = self.name
        return SimpleSetting(
            name,
            self.connection,
            self._read_type_conversion,
            self._write_type_conversion,
            self._default_value,
        )

    def __set__(self, obj, value):
        if isinstance(value, SimpleSetting):
            return

        if self._use_object_name:
            name = obj.name + ":" + self.name
        else:
            name = self.name

        if value is None:
            self.connection.delete(name)
        else:
            if self._write_type_conversion:
                value = self._write_type_conversion(value)
            self.connection.set(name, value)


class QueueSetting(BaseSetting):
    """
    Class to manage a setting that is stored as a list on Redis
    """

    def __init__(
        self,
        name,
        connection=None,
        read_type_conversion=auto_coerce,
        write_type_conversion=str,
    ):
        super().__init__(name, connection, read_type_conversion, write_type_conversion)

    @read_decorator
    def get(self, first=0, last=-1, cnx=None):
        if cnx is None:
            cnx = self.connection
        if first == last:
            lst = cnx.lindex(self.name, first)
        else:
            if last != -1:
                last -= 1
            lst = cnx.lrange(self.name, first, last)
        return lst

    @write_decorator
    def append(self, value, cnx=None):
        if cnx is None:
            cnx = self.connection
        return cnx.rpush(self.name, value)

    @write_decorator
    def prepend(self, value, cnx=None):
        if cnx is None:
            cnx = self.connection
        return cnx.lpush(self.name, value)

    @write_decorator_multiple
    def extend(self, values, cnx=None):
        if cnx is None:
            cnx = self.connection
        return cnx.rpush(self.name, *values)

    @write_decorator
    def remove(self, value, cnx=None):
        if cnx is None:
            cnx = self.connection
        cnx.lrem(self.name, 0, value)

    @write_decorator_multiple
    def set(self, values, cnx=None):
        if cnx is None:
            cnx = self.connection
        p = cnx.pipeline()
        p.delete(self.name)
        if values is not None:
            p.rpush(self.name, *values)
        p.execute()

    @write_decorator
    def set_item(self, value, pos=0, cnx=None):
        if cnx is None:
            cnx = self.connection
        cnx.lset(self.name, pos, value)

    @read_decorator
    def pop_front(self, cnx=None):
        if cnx is None:
            cnx = self.connection
        value = cnx.lpop(self.name)
        if self._read_type_conversion:
            value = self._read_type_conversion(value)
        return value

    @read_decorator
    def pop_back(self, cnx=None):
        if cnx is None:
            cnx = self.connection
        value = cnx.rpop(self.name)
        if self._read_type_conversion:
            value = self._read_type_conversion(value)
        return value

    def __len__(self, cnx=None):
        if cnx is None:
            cnx = self.connection
        return cnx.llen(self.name)

    def __repr__(self, cnx=None):
        if cnx is None:
            cnx = self.connection
        value = cnx.lrange(self.name, 0, -1)
        return "<QueueSetting name=%s value=%s>" % (self.name, value)

    def __iadd__(self, other, cnx=None):
        self.extend(other, cnx)
        return self

    def __getitem__(self, ran, cnx=None):
        if isinstance(ran, slice):
            i = ran.start is not None and ran.start or 0
            j = ran.stop is not None and ran.stop or -1
        elif isinstance(ran, int):
            i = j = ran
        else:
            raise TypeError("indices must be integers")
        value = self.get(first=i, last=j, cnx=cnx)
        if value is None:
            raise IndexError
        else:
            return value

    def __iter__(self, cnx=None):
        if cnx is None:
            cnx = self.connection
        lsize = cnx.llen(self.name)
        for first in range(0, lsize, 1024):
            last = first + 1024
            if last >= lsize:
                last = -1
            for value in self.get(first, last):
                yield value

    def __setitem__(self, ran, value, cnx=None):
        if isinstance(ran, slice):
            for i, v in zip(range(ran.start, ran.stop), value):
                self.set_item(v, pos=i, cnx=cnx)
        elif isinstance(ran, int):
            self.set_item(value, pos=ran, cnx=cnx)
        else:
            raise TypeError("indices must be integers")
        return self


class QueueSettingProp(BaseSetting):
    """
    A python's property implementation for QueueSetting
    To be used inside user defined classes
    """

    def __init__(
        self,
        name,
        connection=None,
        read_type_conversion=auto_coerce,
        write_type_conversion=str,
        use_object_name=True,
    ):
        super().__init__(name, connection, read_type_conversion, write_type_conversion)
        self._use_object_name = use_object_name

    def __get__(self, obj, type=None):
        if self._use_object_name:
            name = obj.name + ":" + self.name
        else:
            name = self.name

        return QueueSetting(
            name,
            self.connection,
            self._read_type_conversion,
            self._write_type_conversion,
        )

    def __set__(self, obj, values):
        if isinstance(values, QueueSetting):
            return

        if self._use_object_name:
            name = obj.name + ":" + self.name
        else:
            name = self.name

        proxy = QueueSetting(
            name,
            self.connection,
            self._read_type_conversion,
            self._write_type_conversion,
        )
        proxy.set(values)


class BaseHashSetting(BaseSetting):
    """
    A `Setting` stored as a key,value pair in Redis

    Arguments:
        name: name of the BaseHashSetting (used on Redis)
        connection: Redis connection object
        read_type_conversion: conversion of data applied after reading
        write_type_conversion: conversion of data applied before writing
    """

    def __init__(
        self,
        name,
        connection=None,
        read_type_conversion=auto_coerce,
        write_type_conversion=str,
    ):
        super().__init__(name, connection, read_type_conversion, write_type_conversion)

    def __repr__(self):
        value = self.get_all()
        return f"<{type(self).__name__} name=%s value=%s>" % (self.name, value)

    def __delitem__(self, key):
        self.remove(key)

    def __len__(self):
        cnx = self.connection
        return cnx.hlen(self.name)

    def raw_get(self, *keys):
        cnx = self.connection
        return cnx.hget(self.name, *keys)

    @read_decorator
    def get(self, key, default=None):
        v = self.raw_get(key)
        if v is None:
            v = DefaultValue(default)
        return v

    def _raw_get_all(self):
        cnx = self.connection
        return cnx.hgetall(self.name)

    def get_all(self):
        all_dict = dict()
        for k, raw_v in self._raw_get_all().items():
            k = k.decode()
            v = self._read_type_conversion(raw_v)
            if isinstance(v, InvalidValue):
                raise ValueError(
                    "%s: Invalid value '%s` (cannot deserialize %r)"
                    % (self.name, k, raw_v)
                )
            all_dict[k] = v
        return all_dict

    @read_decorator
    def pop(self, key, default=Undefined):
        cnx = self.connection.pipeline()
        cnx.hget(self.name, key)
        cnx.hdel(self.name, key)
        (value, worked) = cnx.execute()
        if not worked:
            if default is Undefined:
                raise KeyError(key)
            else:
                value = default
        return value

    def remove(self, *keys):
        cnx = self.connection
        cnx.hdel(self.name, *keys)

    @write_decorator_dict
    def set(self, values):
        cnx = self.connection
        cnx.delete(self.name)
        if values is not None:
            cnx.hset(self.name, mapping=values)

    @write_decorator_dict
    def update(self, values):
        cnx = self.connection
        if values:
            cnx.hset(self.name, mapping=values)

    def keys(self):
        for k, v in self.items():
            yield k

    def values(self):
        for k, v in self.items():
            yield v

    def items(self):
        cnx = self.connection
        next_id = 0
        while True:
            next_id, pd = cnx.hscan(self.name, next_id)
            for k, v in pd.items():
                # Add key conversion
                k = k.decode()
                if self._read_type_conversion:
                    v = self._read_type_conversion(v)
                yield k, v
            if not next_id or next_id == "0":
                break

    def __getitem__(self, key):
        value = self.get(key)
        if value is None:
            raise KeyError(key)
        return value

    def __setitem__(self, key, value):
        cnx = self.connection
        if value is None:
            cnx.hdel(self.name, key)
            return
        if self._write_type_conversion:
            value = self._write_type_conversion(value)
        cnx.hset(self.name, key, value)

    def __contains__(self, key):
        cnx = self.connection
        return cnx.hexists(self.name, key)


orderedhashsetting_helper_script = """#!lua name=mylib
local function orderedhashsetting_helper_script(keys, args)
    -- Atomic addition of a key to a hash and to a list
    -- to keep track of insertion order

    -- KEYS[1]: redis-key of hash
    -- ARGV[1]: attribute to be added
    -- ARGV[2]: value of the attribute

    local hashkey = keys[1]
    local setkey = keys[2]
    local attribute = args[1]
    local value = args[2]

    if (redis.call("EXISTS", setkey)==0) then
        -- set does not exist, create it, create hash and return
        redis.call("ZADD", setkey, 1, attribute)
        return redis.call("HSET", hashkey, attribute, value)
    end

    local set_max_score = tonumber(redis.call("ZRANGE", setkey, -1, -1, "withscores")[2])
    local set_size = redis.call("ZCARD", setkey)

    if set_max_score > set_size  then
        -- we need to reassign scores as some was deleted
        local new_order = 1
        local table = redis.call("ZRANGE", setkey, 0, -1)
        for k, attr in pairs(table)
        do
            new_order = new_order + 1
        end
    end

    if redis.call("ZSCORE", setkey, attribute) == false then
        -- attribute does not exist
        -- create zset attribute

        local list = redis.call("ZPOPMAX",setkey)
        local order, attr = tonumber(list[2]), list[1]
        -- reinserting popped value
        redis.call("ZADD", setkey, order, attr)


        redis.call("ZADD", setkey, order+1, attribute)
        -- create and set hset
        return redis.call("HSET", hashkey, attribute, value)
    else
        -- attribute does exist
        return redis.call("HSET", hashkey, attribute, value)
    end
end

redis.register_function('orderedhashsetting_helper_script', orderedhashsetting_helper_script)"""


class OrderedHashSetting(BaseHashSetting):
    """
    A Setting stored as a key,value pair in Redis
    The insertion order is maintained

    Arguments:
        name: name of the BaseHashSetting (used on Redis)
        connection: Redis connection object
        read_type_conversion: conversion of data applied after reading
        write_type_conversion: conversion of data applied before writing
    """

    def __init__(self, *args, **kw):
        super().__init__(*args, **kw)
        register_script(
            self.connection,
            "orderedhashsetting_helper_script",
            orderedhashsetting_helper_script,
        )

        # check if this ordered hash is built from an existing hash setting
        if self.connection.exists(self.name) and not self.connection.exists(
            self._name_order
        ):
            if self.connection.type(self.name) == b"hash":  # noqa E721
                # we can provide a default order (alphabetical), to ensure an ordered hash
                # can be built on top of an existing hash ; this is to preserve
                # settings coherency in case of type "upgrade" (like when Lima ROIs
                # have been ordered from "normal" hashes)
                sorted_keys = natural_sort(self.connection.hkeys(self.name))
                self.connection.zadd(
                    self._name_order, {v: k for k, v in enumerate(sorted_keys)}
                )

    @property
    def _name_order(self):
        return self._name + ":creation_order"

    def ttl(self, value=-1):
        hash_ttl = super().ttl(value)
        ttl_func(self.connection, self._name_order, value)
        return hash_ttl

    @read_decorator
    def get(self, key, default=None):
        v = self.raw_get(key)
        if v is None:
            v = DefaultValue(default)
        return v

    def _raw_get_all(self):
        cnx = self._cnx()
        order = iter(k for k in cnx.zrange(self._name_order, 0, -1))
        return {key: cnx.hget(self._name, key) for key in order}

    @read_decorator
    def pop(self, key, default=Undefined):
        cnx = self._cnx().pipeline()
        cnx.hget(self._name, key)
        cnx.hdel(self._name, key)
        cnx.zrem(self._name_order, key)
        (value, removed_h, removed_z) = cnx.execute()
        if not (removed_h and removed_z):
            if default is Undefined:
                raise KeyError(key)
            else:
                value = default
        return value

    def remove(self, *keys):
        with pipeline(self) as p:
            p.zrem(self._name_order, *keys)
            p.hdel(self._name, *keys)

    def clear(self):
        with pipeline(self) as p:
            p.delete(self._name)
            p.delete(self._name_order)

    @write_decorator_dict
    def set(self, mapping):
        cnx = self._cnx().pipeline()
        cnx.delete(self._name)
        cnx.delete(self._name_order)
        if mapping is not None:
            for k, v in mapping.items():
                evaluate_script(
                    cnx,
                    "orderedhashsetting_helper_script",
                    keys=(self._name, self._name + ":creation_order"),
                    args=(k, v),
                )
        cnx.execute()

    @write_decorator_dict
    def update(self, values):
        with pipeline(self) as p:
            if values:
                for k, v in values.items():
                    evaluate_script(
                        p,
                        "orderedhashsetting_helper_script",
                        keys=(self._name, self._name + ":creation_order"),
                        args=(k, v),
                    )

    def keys(self):
        cnx = self._cnx()
        return (k.decode() for k in cnx.zrange(self._name_order, 0, -1))

    def values(self):
        for k in self.keys():
            yield self[k]

    def items(self):
        for k in self.keys():
            v = self[k]
            if self._read_type_conversion:
                v = self._read_type_conversion(v)
            yield k, v

    def __getitem__(self, key):
        if key not in self:
            raise KeyError(key)
        return self.get(key)

    def __setitem__(self, key, value):
        if value is None:
            self.remove(key)
            return
        if self._write_type_conversion:
            value = self._write_type_conversion(value)
        cnx = self._cnx()
        evaluate_script(
            cnx,
            "orderedhashsetting_helper_script",
            keys=(self._name, self._name + ":creation_order"),
            args=(key, value),
        )

    def __contains__(self, key):
        cnx = self._cnx()
        return cnx.hexists(self.name, key)


class HashSetting(BaseHashSetting):
    """
    A Setting stored as a key,value pair in Redis
    with a default_value dictionary to serve as a callback
    when elements lookup fails

    Args:
        name: name of the HashSetting (used on Redis)
        connection: Redis connection object
        read_type_conversion: conversion of data applied after reading
        write_type_conversion: conversion of data applied before writing
        default_values: dictionary of default values retrieved on fallback
    """

    def __init__(
        self,
        name,
        connection=None,
        read_type_conversion=auto_coerce,
        write_type_conversion=str,
        default_values=None,
    ):
        super().__init__(
            name,
            connection=connection,
            read_type_conversion=read_type_conversion,
            write_type_conversion=write_type_conversion,
        )
        if default_values is None:
            default_values = dict()
        self._default_values = default_values

    @read_decorator
    def get(self, key, default=None):
        """
        Return a value from a key.

        Return:
            The value, else `bliss.config.settings.InvalidValue` if there
            was a problem during deserialization of the value.
        """
        v = super().raw_get(key)
        if v is None:
            v = DefaultValue(default)
        return v

    def __contains__(self, key):
        return super().__contains__(key) or key in self._default_values

    def __getitem__(self, key):
        value = self.get(key)
        if value is None:
            if key not in self._default_values:
                raise KeyError(key)
        return value

    def get_all(self):
        all_dict = dict(self._default_values)
        for k, raw_v in self._raw_get_all().items():
            k = k.decode()
            v = self._read_type_conversion(raw_v)
            if isinstance(v, InvalidValue):
                raise ValueError(
                    "%s: Invalid value '%s` (cannot deserialize %r)"
                    % (self.name, k, raw_v)
                )
            all_dict[k] = v
        return all_dict

    def items(self):
        seen_keys = set()
        for k, v in super().items():
            seen_keys.add(k)
            yield k, v

        for k, v in self._default_values.items():
            if k in seen_keys:
                continue
            yield k, v


class HashSettingProp(BaseSetting):
    def __init__(
        self,
        name,
        connection=None,
        read_type_conversion=auto_coerce,
        write_type_conversion=str,
        default_values=None,
        use_object_name=True,
    ):
        super().__init__(name, connection, read_type_conversion, write_type_conversion)
        if default_values is None:
            default_values = dict()
        self._default_values = default_values
        self._use_object_name = use_object_name

    def __get__(self, obj, type=None):
        if self._use_object_name:
            name = obj.name + ":" + self.name
        else:
            name = self.name

        return HashSetting(
            name,
            self.connection,
            self._read_type_conversion,
            self._write_type_conversion,
            self._default_values,
        )

    def __set__(self, obj, values):
        if self._use_object_name:
            name = obj.name + ":" + self.name
        else:
            name = self.name

        if isinstance(values, HashSetting):
            return

        proxy = HashSetting(
            name,
            self.connection,
            self._read_type_conversion,
            self._write_type_conversion,
            self._default_values,
        )
        proxy.set(values)

    def get_proxy(self):
        return HashSetting(
            self.name,
            self.connection,
            self._read_type_conversion,
            self._write_type_conversion,
            self._default_values,
        )


# helper


def _change_to_obj_marshalling(keys):
    read_type_conversion = keys.pop("read_type_conversion", pickle_loads)
    write_type_conversion = keys.pop("write_type_conversion", pickle.dumps)
    keys.update(
        {
            "read_type_conversion": read_type_conversion,
            "write_type_conversion": write_type_conversion,
        }
    )


class HashObjSetting(HashSetting):
    """
    Class to manage a setting that is stored as a dictionary on redis
    where values of the dictionary are pickled
    """

    def __init__(self, name, **keys):
        _change_to_obj_marshalling(keys)
        super().__init__(name, **keys)


class OrderedHashObjSetting(OrderedHashSetting):
    """
    Class to manage a setting that is stored as a dictionary on redis
    where values of the dictionary are pickled
    """

    def __init__(self, name, **keys):
        _change_to_obj_marshalling(keys)
        super().__init__(name, **keys)


class HashObjSettingProp(HashSettingProp):
    """
    A python's property implementation for HashObjSetting
    To be used inside user defined classes
    """

    def __init__(self, name, **keys):
        _change_to_obj_marshalling(keys)
        super().__init__(name, **keys)


class QueueObjSetting(QueueSetting):
    """
    Class to manage a setting that is stored as a list on redis
    where values of the list are pickled
    """

    def __init__(self, name, **keys):
        _change_to_obj_marshalling(keys)
        super().__init__(name, **keys)


class QueueObjSettingProp(QueueSettingProp):
    """
    A python's property implementation for QueueObjSetting
    To be used inside user defined classes
    """

    def __init__(self, name, **keys):
        _change_to_obj_marshalling(keys)
        super().__init__(name, **keys)


class SimpleObjSetting(SimpleSetting):
    """
    Class to manage a setting that is stored as pickled object
    on redis
    """

    def __init__(self, name, **keys):
        _change_to_obj_marshalling(keys)
        super().__init__(name, **keys)


class SimpleObjSettingProp(SimpleSettingProp):
    """
    A python's property implementation for SimpleObjSetting
    To be used inside user defined classes
    """

    def __init__(self, name, **keys):
        _change_to_obj_marshalling(keys)
        super().__init__(name, **keys)


class Struct:
    def __init__(self, name, **kwargs):
        object.__setattr__(self, "_Struct__proxy", HashSetting(name, **kwargs))

    @property
    def name(self):
        return self._proxy.name

    @property
    def _proxy(self):
        return self.__proxy

    @property
    def _cnx(self):
        return self._proxy._cnx

    @_cnx.setter
    def _cnx(self, cnx):
        self._proxy._cnx = cnx

    def __dir__(self):
        return self._proxy.keys()

    def __repr__(self):
        return "<Struct with attributes: %s>" % self._proxy.keys()

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return self._proxy.get(name)

    def __setattr__(self, name, value):
        if name in object.__dir__(self):
            return super().__setattr__(name, value)
        else:
            self._proxy[name] = value

    def __delattr__(self, name):
        if name in object.__dir__(self):
            return super().__delattr__(name)
        else:
            self._proxy.remove(name)


class ParametersType(type):
    """
    Created classes have access to a limited number of
    attributes defined inside SLOTS class attribute.
    Also created classes are unique every time, so we
    can use class.__dict__ with Python descriptors
    and be sure that those are not shared beetween
    two different instances
    """

    def __call__(cls, *args, **kwargs):
        class_dict = {"__slots__": tuple(cls.SLOTS), "SLOTS": cls.SLOTS}
        new_cls = type(cls.__name__, (cls,), class_dict)
        return type.__call__(new_cls, *args, **kwargs)

    def __new__(cls, name, bases, attrs):
        attrs["__slots__"] = tuple(attrs.get("SLOTS", []))
        return type.__new__(cls, name, bases, attrs)
