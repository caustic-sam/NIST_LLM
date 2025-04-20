# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.
import json
import weakref
from cerberus import schema_registry, Validator
from pathlib import Path
from bliss.config.conductor.client import get_redis_proxy

# Json Tree structure is organized as follow, each node has a content dict and a list of children:
# {
#     "CONTENT":{...},
#     "CHILDREN":{
#         "ABCD":{
#             "CONTENT":{...},
#             "CHILDREN":{
#                 "EFGH":{
#                         "CONTENT":{...},
#                         "CHILDREN":{}
#                 },
#                 "IJKL":{
#                         "CONTENT":{...},
#                         "CHILDREN":{}
#                 }
#             }
#         }
#     }
# }

schema_registry.add(
    "tree_node",
    {
        "CONTENT": {"type": "dict"},
        "CHILDREN": {"type": "dict", "valuesrules": {"schema": "tree_node"}},
    },
)


class SingletonPerRedisKey(type):
    _instances = {}

    def __call__(cls, key):
        """If an instance already exists with the same key, return that instance.
        When the key doesn't exist in Redis (eg. flushed between two tests), the
        singleton is recreated."""
        instance_weakref = cls._instances.get(key)
        if instance_weakref is not None:
            instance = instance_weakref()
            if instance is not None and get_redis_proxy().exists(key) == 1:
                return instance
        new_instance = super(SingletonPerRedisKey, cls).__call__(key)
        cls._instances[key] = weakref.ref(new_instance)
        return cls._instances[key]()


class RedisJsonTree(metaclass=SingletonPerRedisKey):
    """A tree data structure with a Redis backup.
    Redis is read once during initialization, then all readings are done on a local copy,
    but writings are done on both local copy and Redis backup.

    In other words, this structure is not multi-process safe, it is just backup for a single process."""

    def __init__(self, key):
        self._redis_key = key
        self._root_dict = get_redis_proxy().json().get(self._redis_key)

        if self._root_dict is None:
            self._root_dict = self._new_node
            get_redis_proxy().json().set(self._redis_key, path="$", obj=self._root_dict)
        else:
            assert Validator().validate(self._root_dict, "tree_node")

    def __str__(self):
        return json.dumps(self._root_dict, indent=" " * 4)

    @property
    def _new_node(self):
        """Return a new dict instance to create a new node in the tree."""
        return {"CONTENT": {}, "CHILDREN": {}}

    def _path_to_redis(self, path: str) -> str:
        """Convert a path into a RedisJson path to access a node into self._redis_key
        ex: /foo/bar@#!,. -> "$['CHILDREN']['foo']['CHILDREN']['bar@#!,.']"
        """
        items = [f"[{repr(item)}]" for item in self._path_to_keys(path)]
        return "$" + "".join(items)

    def _path_to_keys(self, path: str) -> list:
        """Convert a path into a list of keys to access a node into self._root_dict
        ex: /foo/bar@#!,. -> ["CHILDREN", "foo", "CHILDREN", "bar@#!,."]
        """
        path_items = Path(path).parts[1:]  # skip leading '/'
        return list(sum([("CHILDREN", part) for part in path_items], ()))

    def _local_get(self, path):
        subdict = self._root_dict
        for key in self._path_to_keys(path):
            subdict = subdict[key]
        return subdict

    def _local_set(self, path, value):
        keys = self._path_to_keys(path)
        subdict = self._root_dict
        for key in keys[:-1]:
            subdict = subdict[key]
        subdict[keys[-1]] = value

    # Note: There is no _redis_get(), redis is read once at __init__, then it's only written for backup
    # def _redis_get(self, path):
    #     pass

    def _redis_set(self, path, value):
        get_redis_proxy().json().set(
            self._redis_key, path=self._path_to_redis(path), obj=value
        )

    def get_children(self, path):
        child_names = self._local_get(path)["CHILDREN"].keys()
        return [RedisJsonNode(self, Path(path) / name) for name in child_names]

    def create_node(self, path):
        path = Path(path)
        assert path.is_absolute()
        assert len(path.parts) > 1

        for parent in list(reversed(path.parents))[1:]:
            try:
                self._local_get(parent)
            except KeyError:
                raise KeyError(f"Cannot create {path}, parent path {parent} is missing")

        try:
            self._local_get(path)
        except KeyError:
            pass
        else:
            raise KeyError(f"Cannot create {path}, node already exists.")

        self._local_set(path, self._new_node)
        self._redis_set(path, self._new_node)
        return RedisJsonNode(self, path)

    def get_node(self, path):
        """KeyError is raised if path doesn't exist."""
        self._local_get(path)  # may raise KeyError
        return RedisJsonNode(self, path)

    def delete_node(self, path):
        path = Path(path)
        try:
            parent_dict = self._local_get(path.parent)
            parent_dict["CHILDREN"][path.name]  # may raise KeyError too
        except KeyError:
            raise KeyError(f"Cannot delete {path}, node doesn't exist.")

        get_redis_proxy().json().delete(self._redis_key, path=self._path_to_redis(path))
        parent_dict["CHILDREN"].pop(path.name)

    def get_content(self, path):
        try:
            return self._local_get(path)["CONTENT"]
        except KeyError:
            raise KeyError(f"Cannot get node content, {path} doesn't exist.")

    def set_content(self, path, value: dict):
        # TODO if too slow, compare json with previous to only modify specific keys
        try:
            subdict = self._local_get(path)
        except KeyError:
            raise KeyError(f"Cannot set node content, {path} doesn't exist.")

        get_redis_proxy().json().set(
            self._redis_key, path=self._path_to_redis(path) + ".CONTENT", obj=value
        )
        subdict["CONTENT"] = value


class RedisJsonNode:
    def __init__(self, tree: RedisJsonTree, path):
        self._tree = tree
        self._path = (Path("/") / path).resolve()

    def create_child(self, name):
        return self._tree.create_node(self._path / name)

    def delete_child(self, name):
        self._tree.delete_node(self._path / name)

    def get(self) -> dict:
        return self._tree.get_content(self._path)

    def set(self, value: dict):
        self._tree.set_content(self._path, value)

    def __str__(self):
        return f"{type(self).__name__}:{self._path}"

    def __eq__(self, other):
        if isinstance(other, RedisJsonNode):
            return self._tree == other._tree and self._path == other._path
        return False

    @property
    def parent(self):
        if self._path != Path("/"):
            return RedisJsonNode(self._tree, self._path.parent)
        else:
            return None

    @property
    def children(self):
        return self._tree.get_children(self._path)
