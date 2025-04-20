# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

import re
import datetime
import logging
import time
from tabulate import tabulate
from ruamel.yaml import YAML
from ruamel.yaml.compat import StringIO

from bliss.config.conductor.client import set_config_db_file, remote_open
from bliss import current_session
from bliss.config.settings import OrderedHashSetting, QueueSetting, ParametersType

from bliss.config.settings import _change_to_obj_marshalling, pipeline

logger = logging.getLogger(__name__)


class ParamDescriptorWithDefault:
    """
    Used to link python global objects
    If necessary It will create an entry on redis under
    parameters:objects:name
    If the proxy key doesn't exists it returns the value of the default
    """

    OBJECT_PREFIX = "parameters:object:"

    def __init__(self, proxy, proxy_default, name, value, assign=True):
        self.proxy = proxy
        self.proxy_default = proxy_default
        self.name = name  # name of parameter
        if assign:
            self.assign(value)

    def assign(self, value):
        """
        if the value is a global defined object it will create a link
        to that object inside the ParamDescriptor and the link will
        be stored inside redis in this way:'parameters:object:name'
        otherwise the value will be stored normally
        """
        if hasattr(value, "name") and value.name in current_session.env_dict:
            value = "%s%s" % (self.OBJECT_PREFIX, value.name)
        try:
            self.proxy[self.name] = value
        except Exception:
            raise ValueError("%s.%s: cannot set value" % (self.proxy._name, self.name))

    def __get__(self, obj, obj_type):
        try:
            value = self.proxy[self.name]
        except KeyError:
            # getting from default
            value = self.proxy_default[self.name]
        if isinstance(value, str) and value.startswith(self.OBJECT_PREFIX):
            value = value[len(self.OBJECT_PREFIX) :]
            try:
                return current_session.env_dict[value]
            except KeyError:
                raise AttributeError(
                    f"The object '{self.name}' is not "
                    "found in the globals: Be sure to"
                    " work inside the right session"
                )

        if value == "None":
            # Manages the python None value stored in Redis as a string
            value = None
        return value

    def __set__(self, obj, value):
        return self.assign(value)


class ParametersWardrobe(metaclass=ParametersType):
    DESCRIPTOR = ParamDescriptorWithDefault
    SLOTS = [
        "_proxy",
        "_proxy_default",
        "_instances",
        "_wardr_name",
        "_property_attributes",
        "_not_removable",
        "_connection",
    ]

    def __init__(
        self,
        name,
        default_values=None,
        property_attributes=None,
        not_removable=None,
        connection=None,
        **keys,
    ):
        """
        ParametersWardrobe is a convenient way of storing parameters
        typically to be passed to a function or procedure.
        The advantage is that you can easily create new instances
        in which you can modify only some parameters and
        keep the rest to default.
        Is like having different dresses for different purposes and
        changing them easily.

        All instances are stored in Redis, you will have:
        * A list of Names with the chosen key parameters:name
        * Hash types with key 'parameters:wardrobename:instance_name'
            one for each instance

        Args:
            name: the name of the ParametersWardrobe

        kwargs:
            default_values: dict of default values
            property_attributes: iterable with attribute names implemented
                                 internally as properties (for subclassing)
                                 Those attribute are computed on the fly
            not_removable: list of not removable keys, for example could be
                           default values (that usually should not be removed)
            **keys: other key,value pairs will be directly passed to Redis proxy
        """
        logger.debug(
            """In %s.__init__(%s,
                      default_values=%s,
                      property_attributes=%s,
                      not_removable=%s
                      )""",
            type(self).__name__,
            name,
            default_values,
            property_attributes,
            not_removable,
        )

        if not default_values:
            default_values = {}
        if not property_attributes:
            property_attributes = ()
        if not not_removable:
            not_removable = ()

        self._connection = connection

        # different instance names are stored in a queue where
        # the first item is the currently used one
        self._instances = QueueSetting(
            "parameters:%s" % name, connection=self.connection
        )
        self._wardr_name = name  # name of the ParametersWardrobe
        # adding attribute for creation_date and last_accessed
        self._property_attributes = tuple(property_attributes) + (
            "creation_date",
            "last_accessed",
        )

        self._not_removable = tuple(not_removable)

        # creates the two needed proxies
        _change_to_obj_marshalling(keys)  # allows pickling complex objects
        self._proxy = OrderedHashSetting(
            self._hash("default"), connection=self.connection, **keys
        )
        self._proxy_default = OrderedHashSetting(
            self._hash("default"), connection=self.connection, **keys
        )

        # Remove keys from Redis when reserved for something else
        redis_keys = set(self._proxy_default.keys())
        forbidden_keys = self._reserved_names()
        for k in redis_keys & forbidden_keys:
            # remove when in Redis and forbidden
            self.remove(f".{k}")

        # Managing default written to proxy_default
        skip_keys = redis_keys | forbidden_keys
        for k, v in default_values.items():
            if k not in skip_keys:
                # set default value when not in Redis and not forbidden
                self.add(k, v)

        if "default" not in self._instances:
            # Newly created Wardrobe, switch to default
            self.switch("default")
        else:
            # Existing Wardrobe, switch to last used
            self.switch(self.current_instance, update=False)

    def _reserved_names(self):
        return set(self._property_attributes) | set(self.SLOTS)

    def _hash(self, name):
        """
        Helper for extracting the redis name of parameter instances
        """
        return "parameters:%s:%s" % (self._wardr_name, name)

    def __dir__(self):
        keys_proxy_default = (
            x for x in self._proxy_default.keys() if not x.startswith("_")
        )
        attributes = [
            "add",
            "remove",
            "switch",
            "instances",
            "current_instance",
            "to_dict",
            "from_dict",
            "to_file",
            "from_file",
            "to_beacon",
            "from_beacon",
            "freeze",
            "show_table",
            "creation_date",
            "last_accessed",
            "purge",
        ]
        return list(keys_proxy_default) + attributes + list(self._property_attributes)

    def to_dict(self, export_properties=False):
        """
        Retrieve all parameters inside an instance in a dict form
        If a parameter is not present inside the instance, the
        default will be taken, property (computed) attributes are included.

        Args:
            export_properties: if set to true exports to dict also property attributes
                               default is False

        Returns:
            dictionary with (parameter,value) pairs
        """
        return {
            **self._get_instance("default", get_properties=export_properties),
            **self._get_instance(
                self.current_instance, get_properties=export_properties
            ),
        }

    def from_dict(self, d: dict) -> None:
        """
        Updates the current instance of values from a dictionary.

        You should provide a dictionary that contains the same attribute names as
        current existing inside the ParametersWardrobe you want to update.
        Giving more names will log a WARNING level message.
        Property attributes are ignored.

        Raises:
            AttributeError, TypeError
        """
        logger.debug(
            "In %s(%s).from_dict(%s)", type(self).__name__, self._wardr_name, d
        )
        if not d:
            raise TypeError("You should provide a dictionary")
        backup = self.to_dict(export_properties=True)

        redis_default_attrs = set(self._get_redis_single_instance("default").keys())
        found_attrs = set()

        try:
            for name, value in d.items():
                if name in self._property_attributes:
                    continue
                if name in redis_default_attrs:
                    found_attrs.add(name)  # we keep track of remaining values
                    setattr(
                        self.__class__,
                        name,
                        self.DESCRIPTOR(
                            self._proxy, self._proxy_default, name, value, True
                        ),
                    )
                else:
                    raise AttributeError(
                        f"Attribute '{name}' does not find an equivalent in current instance"
                    )
            if found_attrs != redis_default_attrs:
                logger.warning(
                    "Attribute difference for %s(%s): Given excess(%s)",
                    type(self).__name__,
                    self._wardr_name,
                    found_attrs.difference(redis_default_attrs),
                )
        except Exception as exc:
            self.from_dict(backup)  # rollback in case of exception
            raise exc

    def _to_yml(self, *instances) -> str:
        """
        Dumps to yml string all parameters that are stored in Redis
        No property (computed) parameter is stored.

        Args:
            instances: list of instances to export

        Returns:
            str: instances in yml format
        """
        _instances = {}
        for inst in instances:
            _instances.update(
                {
                    inst: {
                        **self._get_redis_single_instance("default"),
                        **self._get_redis_single_instance(inst),
                    }
                }
            )
        data_to_dump = {"WardrobeName": self._wardr_name, "instances": _instances}

        stream = StringIO()
        yaml = YAML(pure=True)
        yaml.default_flow_style = False
        yaml.dump(data_to_dump, stream=stream)
        return stream.getvalue()

    def to_file(self, fullpath: str, *instances) -> None:
        """
        Dumps to yml file the current instance of parameters
        No property (computed) parameter is written.

        Args:
            fullpath: file full path including name of file
            instances: list of instance names to import
        """
        if not instances:
            instances = [self.current_instance]
        yml_data = self._to_yml(*instances)
        with open(fullpath, "w") as file_out:
            file_out.write(yml_data)

    def _from_yml(self, yml: str, instance_name: str = None) -> None:
        """
        Import a single instance from a yml raw string
        behaviour similar to 'from_dict' but dict manages also
        property attributes, instead yml manages only attributes
        stored on Redis

        Params:
            yml: string containing yml data
            instance_name: the name of the instance that you want to import
        """
        yaml = YAML(pure=True)
        dict_in = yaml.load(yml)
        if dict_in.get("WardrobeName") != self._wardr_name:
            logger.warning("Wardrobe Names are different")
        try:
            dict_in["instances"][instance_name]
        except KeyError:
            raise KeyError(f"Can't find an instance with name {instance_name}")

        self.from_dict(dict_in["instances"][instance_name])

    def from_file(self, fullpath: str, instance_name: str = None) -> None:
        """
        Import a single instance from a file
        """
        with open(fullpath) as file:
            self._from_yml(file, instance_name=instance_name)

    def from_beacon(self, name: str, instance_name: str = None):
        """
        Imports a single instance from Beacon.
        It assumes the Wardrobe is under Beacon subfolder /wardrobe/

        Args:
            name: name of the file (will be saved with .dat extension)
            instance_name: name of the wardrobe instance to dump
        """

        if re.match("[A-Za-z_]+[A-Za-j0-9_-]*", name) is None:
            raise NameError(
                "Name of beacon wardrobe saving file should start with a letter or underscore and contain only letters, numbers, underscore and minus"
            )
        remote_file = remote_open(f"wardrobe/{name}.dat")
        self._from_yml(remote_file, instance_name=instance_name)

    def to_beacon(self, name: str, *instances):
        """
        Export one or more instance to Beacon.
        It will save the Wardrobe under Beacon subfolder `/wardrobe/`

        Args:
            name: name of the file (will be saved with .dat extension)
            instances: arguments passed as comma separated

        Example:

        .. code-block:: python

            materials = ParametersWardrobe("materials")
            materials.switch('copper')

            # exporting current instance
            materials.to_beacon('2019-06-23-materials')

            # exporting a instance giving the name
            materials.to_beacon('2019-06-23-materials', 'copper')

            # exporting all instances
            materials.to_beacon('2019-06-23-materials', *materials.instances)  # uses python list unpacking
        """
        if re.match("[A-Za-z_]+[A-Za-z0-9_-]*", name) is None:
            raise NameError(
                "Name of beacon wardrobe saving file should start with a letter or underscore and contain only letters, numbers, underscore and minus"
            )
        yml_data = self._to_yml(*instances)
        set_config_db_file(f"wardrobe/{name}.dat", yml_data)

    def show_table(self) -> None:
        """
        Shows all data inside ParameterWardrobe different instances

        - Property attributes are identified with an # (hash)
        - parameters taken from default are identified with an * (asterisk)
        - parameters with a name starting with underscore are omitted
        """

        all_instances = self._get_all_instances()
        all_instances_redis = self._get_redis_all_instances()

        column_names = self._instances
        column_repr = (
            self.current_instance + " (SELECTED)",
            *self.instances[1:],
        )  # adds SELECTED to first name

        # gets attribute names, remove underscore attributes
        row_names = (
            k for k in all_instances["default"].keys() if not k.startswith("_")
        )

        data = list()
        data.append(column_repr)  # instance names on first row
        for row_name in row_names:
            row_data = []
            row_data.append(row_name)
            for col in column_names:
                if row_name in self._property_attributes:
                    cell = "# " + str(all_instances[col][row_name])
                elif row_name in all_instances_redis[col].keys():
                    cell = str(all_instances[col][row_name])
                else:
                    cell = "* " + str(all_instances["default"][row_name])

                row_data.append(cell)
            data.append(row_data)

        print(
            """* asterisks means value not stored in database (default is taken)\n# hash means a computed attribute (property)\n\n"""
        )
        print(tabulate(data, headers="firstrow", stralign="right"))

    def __info__(self):
        return self._repr(self._get_instance(self.current_instance))

    def _repr(self, d):
        rep_str = (
            f"Parameters ({self.current_instance}) -"
            + " | ".join(self.instances[1:])
            + "\n\n"
        )
        max_len = max(
            (0,) + tuple(len(key) for key in d.keys() if not key.startswith("_"))
        )
        str_format = "  .%-" + "%ds" % max_len + " = %r\n"
        for key, value in d.items():
            if key.startswith("_"):
                continue
            rep_str += str_format % (key, value)
        return rep_str

    def _get_redis_single_instance(self, name) -> dict:
        """
        Retrieve a single instance of parameters from redis
        """
        name_backup = self._proxy._name
        try:
            if name in self.instances:
                self._proxy._name = self._hash(name)
                results = self._proxy.get_all()
                return results
            return {}
        finally:
            self._proxy._name = name_backup

    def _get_redis_all_instances(self) -> dict:
        """
        Retrieve all parameters of all instances from redis as dict of dicts

        Returns:
            dict of dicts: Example: {'first_instance':{...}, 'second_instance':{...}}
        """
        params_all = {}

        for instance in self.instances:
            params = self._get_redis_single_instance(instance)
            params_all[instance] = {**params}
        return params_all

    def _get_instance(self, name, get_properties=True) -> dict:
        """
        Retrieve all parameters inside an instance
        Taking from default if not present inside the instance
        Property are included

        Args:
            get_properties: if False it will remove property attributes
                            and also creation/modification info
                            stored in _creation_date

        Returns:
            dictionary with (parameter,value) pairs

        Raises:
            NameError
        """
        if name not in self.instances:
            raise NameError(f"The instance name '{name}' does not exist")

        self.switch(name, update=False)

        attrs = list(self._get_redis_single_instance("default").keys())
        instance_ = {}

        if get_properties:
            attrs.extend(list(self._property_attributes))
        else:
            try:
                attrs.remove("_creation_date")
            except Exception:
                pass

        for attr in attrs:
            instance_[attr] = getattr(self, attr)

        self.switch(self.current_instance, update=False)  # back to current instance
        return instance_

    def _get_all_instances(self):
        """
        Retrieve all parameters of all instances from as dict of dicts
        Property are included
        """
        params_all = {}

        for instance in self.instances:
            params = self._get_instance(instance)
            params_all[instance] = {**params}
        return params_all

    def add(self, name, value=None):
        """
        Adds a parameter to all instances storing the value only on
        'default' parameter

        Args:
            name: name of the parameter (Python attribute) accessible
                  with . dot notation
            value: value of the parameter, None is passed as default
                   if omitted

        Raises:
            NameError: Existing attribute name
        """
        logger.debug(
            "In %s(%s).add(%s, value=%s)",
            type(self).__name__,
            self._wardr_name,
            name,
            value,
        )
        if name in self._reserved_names():
            raise NameError(f"Existing class attribute property with this name: {name}")

        if re.match("[A-Za-z_]+[A-Za-z0-9_]*", name) is None:
            raise TypeError(
                "Attribute name should start with a letter or underscore and contain only letters, numbers or underscore"
            )

        if value is None:
            value = "None"

        self.DESCRIPTOR(self._proxy_default, self._proxy_default, name, value, True)
        self._populate(name)

    def _populate(self, name, value=None):
        setattr(
            self.__class__,
            name,
            self.DESCRIPTOR(self._proxy, self._proxy_default, name, value, bool(value)),
        )

    def freeze(self):
        """
        Freezing values for current set: all default taken values will be
        written inside the instance so changes on 'default' instance will not cause
        change on the current instance.

        If you later add another parameter this will still refer to 'default'
        so you will need to freeze again
        """
        redis_params = {
            **self._get_redis_single_instance("default"),
            **self._get_redis_single_instance(self.current_instance),
        }
        for name, value in redis_params.items():
            setattr(
                self.__class__,
                name,
                self.DESCRIPTOR(self._proxy, self._proxy_default, name, value, True),
            )

    def remove(self, param):
        """
        Remove a parameter or an instance of parameters from all instances

        Args:
            param: name of an instance to remove a whole instance
                   .name of a parameter to remove a parameter from all instances

        Examples:
            >>> p = ParametersWardrobe('p')

            >>> p.add('head', 'hat')

            >>> p.switch('casual')

            >>> p.remove('.head')  # with dot to remove a parameter

            >>> p.remove('casual') # without dot to remove a complete instance
        """
        logger.debug(
            "In %s(%s).remove(%s)", type(self).__name__, self._wardr_name, param
        )

        if param.startswith("."):
            # param is an attribute => remove it from all instances
            param = param[1:]
            if param in self._not_removable:
                raise AttributeError(f"parameter '{param}' is non-removable")
            for instance in self.instances:
                pr = OrderedHashSetting(
                    self._hash(instance), connection=self.connection
                )
                pr.remove(param)
        elif param != "default" and param in self.instances:
            # param is an instance => clear instance
            pr = OrderedHashSetting(self._hash(param), connection=self.connection)
            pr.clear()
            self._instances.remove(param)  # removing from Queue
        else:
            raise NameError(f"Can't remove {param}")

    def purge(self):
        """
        Removes completely any reference to the ParametersWardrobe from redis
        """
        for instance in self.instances:
            pr = OrderedHashSetting(self._hash(instance), connection=self.connection)
            pr.clear()
            self._instances.remove(instance)  # removing from Queue

        self._instances.clear()

    def switch(self, name, copy=None, update=True):
        """
        Switches to a new instance of parameters.

        Values of parameters will be retrieved from redis (if existent).
        In case of a non existing instance name, a new instance of parameters will
        be created and It will be populated with name,value pairs from
        the current 'default' instance.
        This is not a copy, but only a reference, so changes on default
        will reflect to the new instance.

        The value of an attribute is stored in Redis after an assigment
        operation (also if assigned value is same as default).

        To freeze the full instance you can use the 'freeze' method.

        Args:
            name: name of instance of parameters to switch to
            copy: name of instance of parameters to copy for initialization

        Returns:
            None
        """

        logger.debug("In %s.switch(%s,copy=%s)", type(self).__name__, name, copy)
        for key, value in dict(self.__class__.__dict__).items():
            if isinstance(value, self.DESCRIPTOR):
                delattr(self.__class__, key)

        self._proxy._name = self._hash(name)

        # if is a new instance we will set the creation date
        if name not in self.instances:
            self._proxy["_creation_date"] = datetime.datetime.now().strftime(
                "%Y-%m-%d %H:%M:%S"
            )

        # adding default
        for key in self._proxy_default.keys():
            self._populate(key)

        # copy values from existing instance
        if copy and copy in self.instances:
            copy_params = self._get_redis_single_instance(copy)
            for key, value in copy_params.items():
                self._populate(key, value=value)

        # removing and prepending the name so it will be the first
        if update:
            with pipeline(self._instances):
                self._instances.remove(name)
                self._instances.prepend(name)

        for key in self._proxy.keys():
            self._populate(key)

    @property
    def connection(self):
        return self._connection

    @property
    def instances(self):
        """
        Returns:
            A list containing all instance names
        """
        return list(self._instances)

    @property
    def current_instance(self):
        """
        Returns:
            Name of the current selected instance
        """
        try:
            return self.instances[0]
        except IndexError:
            raise IOError("Trying to operate on a purged ParameterWardrobe")

    @property
    def last_accessed(self):
        # "idletime" : time in seconds since the last access to the value stored in redis.
        idletime = self._proxy.connection.object("idletime", self._proxy._name)

        last_accessed_time = time.time() - float(idletime)
        return str(
            datetime.datetime.fromtimestamp(last_accessed_time).strftime(
                "%Y-%m-%d %H:%M:%S"
            )
        )

    @property
    def creation_date(self):
        attr_name = "_creation_date"
        if not hasattr(self, attr_name):
            self._proxy[attr_name] = datetime.datetime.now().strftime(
                "%Y-%m-%d %H:%M:%S"
            )
            self._populate(attr_name)
        return getattr(self, attr_name)
