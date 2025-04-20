# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

from __future__ import annotations

import os
import sys
import typing
import types
import functools
import builtins
import warnings
import linecache
import collections
import inspect
import contextlib
import importlib
import shutil
import abc
from treelib import Tree
from types import ModuleType, SimpleNamespace
from tabulate import tabulate
from typing import Optional

from bliss import global_map, is_bliss_shell, _sessions as global_sessions
from bliss.config import static
from bliss.config.settings import SimpleSetting
from bliss.config.channels import EventChannel
from bliss.config.conductor.client import (
    get_default_connection,
    get_python_modules,
    get_text_file,
)
from bliss.common.measurementgroup import ActiveMeasurementGroupProxy
from bliss.common.protected_dict import ProtectedDict
from bliss.common.proxy import Proxy
from bliss.common.logtools import log_warning
from bliss.common.utils import (
    UserNamespace,
    autocomplete_property,
    chunk_col,
    Undefined,
)
from bliss.common import constants
from bliss.common.protocols import ErrorReportInterface
from bliss.common.scans import DEFAULT_CHAIN
from bliss.common.data_store import set_default_data_store
from bliss.icat.client import icat_client_is_disabled
from bliss.scanning import scan_saving
from bliss.scanning import scan_display
from bliss.scanning import scan_meta
from bliss.scanning.toolbox import DefaultAcquisitionChain

if typing.TYPE_CHECKING:
    from bliss.shell.cli.bliss_repl import BlissRepl


_SESSION_IMPORTERS = set()
sessions = {}


def dup_meth_and_inject_env_dict(func, env_dict):
    # see comment in "load_script" method of Session object
    # the code below is a hack, to make a new method on-the-fly from "func",
    # with injected "env_dict" in method vars
    custom_func = types.FunctionType(
        func.__code__,
        func.__globals__,
        name=func.__name__,
        argdefs=func.__defaults__,
        closure=func.__closure__,
    )
    custom_func = functools.update_wrapper(custom_func, func)
    custom_func.__globals__["env_dict"] = env_dict
    custom_func.__kwdefaults__ = func.__kwdefaults__
    return types.MethodType(custom_func, func.__self__)


class _StringImporter:
    BASE_MODULE_NAMESPACE = "bliss.session"

    def __init__(self, path, session_name, in_load_script=False):
        self._error_report = None
        self._modules = dict()
        session_module_namespace = "%s.%s" % (self.BASE_MODULE_NAMESPACE, session_name)
        for module_name, file_path in get_python_modules(path):
            self._modules["%s.%s" % (session_module_namespace, module_name)] = file_path
            if in_load_script:
                self._modules[module_name] = file_path
        if self._modules:
            self._modules[self.BASE_MODULE_NAMESPACE] = None
            self._modules["%s.%s" % (self.BASE_MODULE_NAMESPACE, session_name)] = None

    def find_module(self, fullname, path):
        if fullname in self._modules:
            return self
        return None

    def set_error_report(self, error_report):
        self._error_report = error_report

    def load_module(self, fullname, module_dict=None):
        """Load and execute a Python module hosted in Beacon

        Args:
        - fullname: module name
        - module_dict: new module globals dict

        Return value:
        - newly loaded module, success flag

        Success flag is set to False, if the execution of the module failed. Exceptions
        are reported via the except hook in place, but do not prevent from
        having the module loaded and properly registered in linecache (to retrieve
        source code)
        """
        if fullname not in self._modules.keys():
            raise ImportError(fullname)

        filename = self._modules.get(fullname)
        if filename:
            s_code = get_text_file(filename)
        else:
            filename = "%s (__init__ memory)" % fullname
            s_code = ""  # empty __init__.py

        new_module = ModuleType(fullname)
        new_module.__loader__ = self
        module_filename = "beacon://%s" % filename
        new_module.__file__ = module_filename
        new_module.__name__ = fullname
        if filename.find("__init__") > -1:
            new_module.__path__ = []
            new_module.__package__ = fullname
        else:
            new_module.__package__ = fullname.rpartition(".")[0]
        c_code = compile(s_code, module_filename, "exec")
        if module_dict is not None:
            new_module.__dict__.update(
                {k: v for k, v in module_dict.items() if not k.startswith("__")}
            )
        success = False
        try:
            exec(c_code, new_module.__dict__)
        except Exception:
            self._error_report.display_exception(*sys.exc_info())
        else:
            success = True
        sys.modules[fullname] = new_module
        linecache.updatecache(module_filename, new_module.__dict__)
        return new_module, success

    def get_source(self, fullname):
        if fullname not in self._modules.keys():
            raise ImportError(fullname)
        filename = self._modules.get(fullname)
        return get_text_file(filename) if filename else ""


class ConfigProxy(Proxy):
    def __init__(self, target, env_dict):
        object.__setattr__(self, "_ConfigProxy__env_dict", env_dict)
        super().__init__(target, init_once=True)

    def get(self, name):
        """This is the same as the canonical static config.get,
        except that it adds the object to the corresponding session env dict"""
        obj = self.__wrapped__.get(name)
        self.__env_dict[name] = obj
        return obj


class StandardErrorReport(ErrorReportInterface):
    def __init__(self):
        self._is_loading_config = False

    @property
    def is_loading_config(self):
        return self._is_loading_config

    @is_loading_config.setter
    def is_loading_config(self, loading: bool):
        self._is_loading_config = loading

    def display_exception(self, exc_type, exc_value, tb):
        sys.excepthook(exc_type, exc_value, tb)


class SessionHook(abc.ABC):
    """Provides hooks for session loading

    This is used to decouple the session loading and it's display.
    """

    def session_loading(self, session: Session):
        """Called when session is about to start loading objects"""
        ...

    def object_loading(self, session: Session, object_name: str):
        """Called when an object is about to be loaded"""
        ...

    def object_error_during_loading(
        self, session, object_name: str, exception: Exception
    ):
        """Called when object loading was failed"""
        ...

    def object_loaded(self, session: Session, object_name: str):
        """Called when object was loaded successfully"""
        ...

    def objects_loaded(self, session: Session, item_count: int):
        """Called when every objects of the session were loaded"""
        ...

    def session_loaded(self, session: Session):
        """Called when the session was loaded"""
        ...


class DefaultSessionLoading(SessionHook):
    """
    Display the session loading with print.
    """

    def __init__(self, verbose: bool = True):
        self.__verbose = verbose
        self.__error_count = 0
        self.__error_item_list: list[str] = []
        self.__warning_item_list: list[str] = []
        self.__success_item_list: list[str] = []

    def session_loading(self, session: Session):
        """Called when session is about to start loading objects"""
        self.__print = session.env_dict.get("print", builtins.print)

    def object_loading(self, session: Session, object_name: str):
        """Called when an object is about to be loaded"""
        self.__print(f"Initializing: {object_name}")

    def object_error_during_loading(
        self, session, object_name: str, exception: Exception
    ):
        """Called when object loading was failed"""
        if self.__verbose:
            exc_type, exc_value, tb = (
                type(exception),
                exception,
                exception.__traceback__,
            )
            session.error_report.display_exception(exc_type, exc_value, tb)
            self.__error_count += 1
            self.__error_item_list.append(object_name)

    def object_loaded(self, session: Session, object_name: str):
        """Called when object was loaded successfully"""
        if self.__verbose:
            item_node = session.config.get_config(object_name)
            if item_node.plugin is None:
                self.__warning_item_list.append(object_name)
            else:
                self.__success_item_list.append(object_name)

    def objects_loaded(self, session: Session, item_count: int):
        """Called when every objects of the session were loaded"""
        # Maximal length of objects names (min 5).
        print = self.__print

        display_width = shutil.get_terminal_size().columns
        if len(session.object_names) == 0:
            max_length = 5
            print("There are no objects declared in the session's config file.")
        else:
            max_length = max([len(x) for x in session.object_names])
        # Number of items displayable on one line.
        item_number = int(display_width / max_length) + 1

        # SUCCESS
        success_count = len(self.__success_item_list)
        if success_count > 0:
            self.__success_item_list.sort(key=str.casefold)
            print(
                f"OK: {len(self.__success_item_list)}/{item_count}"
                f" object{'s' if success_count > 1 else ''} successfully initialized.",
                flush=True,
            )
            print(
                tabulate(
                    chunk_col(self.__success_item_list, item_number), tablefmt="plain"
                )
            )
            print("")

        # WARNING
        self.__warning_count = len(self.__warning_item_list)
        if self.__warning_count > 0:
            self.__warning_item_list.sort(key=str.casefold)
            print(
                f"WARNING: {len(self.__warning_item_list)} object{'s' if self.__warning_count > 1 else ''}"
                f" initialized with **default** plugin:"
            )
            print(
                tabulate(
                    chunk_col(self.__warning_item_list, item_number), tablefmt="plain"
                )
            )
            print("")

        # ERROR
        if self.__error_count > 0:
            self.__error_item_list.sort(key=str.casefold)
            print(
                f"ERROR: {self.__error_count} object{'s' if self.__error_count > 1 else ''} failed to initialize:"
            )
            print(
                tabulate(
                    chunk_col(self.__error_item_list, item_number), tablefmt="plain"
                )
            )
            print("")

            if self.__error_count == 1:
                print("To learn about failure, type: 'last_error()'")
            else:
                print(
                    f"To learn about failures, type: 'last_error(X)' for X in [0..{self.__error_count-1}]"
                )
            print("")

    def session_loaded(self, session: Session):
        pass


class Session:
    """
    Bliss session.

    Sessions group objects with a setup.

    YAML file example:

    .. code-block::

         - plugin: session          # could be defined in parents
           class: Session
           name: super_mario        # session name

           # 'config-objects' contains
           # object name you want to export
           # either in yaml compact list
           config-objects: [seby,diode2]
           # or standard yaml list
           config-objects:
           - seby
           - diode2
           # if config-objects key doesn't exist,
           # session will export all objects;
           # 'exclude-objects' can be used to exclude objects
           exclude-objects: [seby]

           # you can also include other session
           # with the 'include-sessions'
           include-sessions: [luigi]

           # finally a setup file can be defined to be
           # executed for the session.
           # All objects or functions defined in the
           # setup file will be exported in the environment.
           # The file path is relative to the session yaml file
           # location if it starts with a './'
           # otherwise it is absolute from the root of the
           # beacon file data base.
           setup-file: ./super_mario.py

           # A svg synoptic (Web shell) can be added:
           synoptic:
             svg-file: super_mario.svg
    """

    def __init__(self, name, config_tree):
        self.__name = name
        self.__env_dict = {}
        self.__setup_globals = SimpleNamespace()
        self.__scripts_module_path = None
        self.__setup_file = None
        self.__synoptic_file = None
        self.__config_objects_names = []
        self.__exclude_objects_names = []
        self.__children_tree = None
        self.__include_sessions = []
        self.__map = None
        self.__log = None
        self.__scans = collections.deque(maxlen=20)
        self.__active_mg = ActiveMeasurementGroupProxy()
        self.__scan_saving = scan_saving.ScanSavingProxy()
        self.__user_scan_meta = scan_meta.create_user_scan_meta()
        self.__default_acquisition_chain = DefaultAcquisitionChain()
        self.__user_script_homedir = SimpleSetting("%s:user_script_homedir" % self.name)
        self.__data_policy_events = EventChannel(f"{self.name}:esrf_data_policy")
        self.__scan_debug_mode = None
        self.scan_display = None
        self.error_report = StandardErrorReport()

        self.__output_getter = None
        """Getter to retrieve the ptpython app output attached to this session"""

        self.__bliss_repl: BlissRepl | None = None
        """Link the BlissRepl used by this session, if one"""

        # configure default blissdata service
        beacon_connection = get_default_connection()
        redis_url = beacon_connection.get_redis_data_server_connection_address().url
        set_default_data_store(redis_url)

        self._config_tree = config_tree
        self.init(config_tree)

    def init(self, config_tree):
        try:
            self.__scripts_module_path = os.path.normpath(
                os.path.join(os.path.dirname(config_tree.filename), "scripts")
            )
        except AttributeError:
            # config_tree has no .filename
            self.__scripts_module_path = None

        try:
            setup_file_path = config_tree["setup-file"]
        except KeyError:
            self.__setup_file = None
        else:
            try:
                self.__setup_file = os.path.normpath(
                    os.path.join(os.path.dirname(config_tree.filename), setup_file_path)
                )
            except TypeError:
                self.__setup_file = None
            else:
                self.__scripts_module_path = os.path.join(
                    os.path.dirname(self.__setup_file), "scripts"
                )

        # convert windows-style path to linux-style
        if self.__scripts_module_path:
            self.__scripts_module_path = self._scripts_module_path.replace("\\", "/")

        try:
            self.__synoptic_file = config_tree.get("synoptic").get("svg-file")
        except AttributeError:
            self.__synoptic_file = None

        self.__config_objects_names = config_tree.get("config-objects")
        self.__exclude_objects_names = config_tree.get("exclude-objects", list())
        self.__children_tree = None
        self.__include_sessions = config_tree.get("include-sessions")
        self.__config_aliases = config_tree.get("aliases", [])
        self.__icat_metadata = None
        self.__icat_metadata_config = config_tree.get("icat-metadata")
        self.__default_user_script_homedir = config_tree.get("default-userscript-dir")
        if self.__default_user_script_homedir and not self._get_user_script_home():
            self._set_user_script_home(self.__default_user_script_homedir)
        self.__scan_saving_config = config_tree.get(
            "scan_saving", self.config.root.get("scan_saving", {})
        )

    @property
    def name(self):
        return self.__name

    @property
    def scans(self):
        return self.__scans

    @property
    def active_mg(self):
        return self.__active_mg

    @property
    def scan_saving(self):
        return self.__scan_saving

    @property
    def scan_saving_config(self):
        return self.__scan_saving_config

    @property
    def default_acquisition_chain(self):
        return self.__default_acquisition_chain

    @property
    def user_scan_meta(self):
        return self.__user_scan_meta

    @property
    def local_config(self):
        """Return the config of this object"""
        return self._config_tree

    @property
    def config(self):
        """Return the whole beacon config"""
        if isinstance(self.env_dict, ProtectedDict):
            return ConfigProxy(static.get_config, self.env_dict.wrapped_dict)
        return ConfigProxy(static.get_config, self.env_dict)

    @property
    def setup_globals(self):
        return self.__setup_globals

    @property
    @contextlib.contextmanager
    def temporary_config(self):
        """
        Create a context to export temporary some devices.
        """
        # store current config status
        cfg = static.get_config()
        name2instancekey = set(cfg._name2instance.keys())
        name2cache = cfg._name2cache.copy()

        # reload is not permited in temporary config
        previous_reload = cfg.reload

        def reload(*args):
            raise RuntimeError("Not permitted under temporary config context")

        cfg.reload = reload

        try:
            yield self.config
        finally:
            # rollback config
            cfg.reload = previous_reload
            diff_keys = set(cfg._name2instance.keys()) - name2instancekey
            for key in diff_keys:
                cfg._name2instance.pop(key)
                self.__env_dict.pop(key, None)
            cfg_name2cache_key = set(cfg._name2cache)
            prev_name2cache_key = set(name2cache)
            added_keys = cfg_name2cache_key - prev_name2cache_key
            removed_key = prev_name2cache_key - cfg_name2cache_key
            # remove added cache
            for key in added_keys:
                cfg._name2cache.pop(key)
            # re-insert removed cache
            for key in removed_key:
                cfg._name2cache[key] = name2cache[key]

    @property
    def setup_file(self):
        return self.__setup_file

    @property
    def synoptic_file(self):
        return self.__synoptic_file

    @property
    def _scripts_module_path(self):
        return self.__scripts_module_path

    @autocomplete_property
    def icat_metadata(self):
        if self.__icat_metadata is not None:
            return self.__icat_metadata
        if self.__icat_metadata_config:
            from bliss.icat.metadata import ICATmetadata

            self.__icat_metadata = ICATmetadata(self.__icat_metadata_config)
            return self.__icat_metadata

    @property
    def scan_debug_mode(self):
        return self.__scan_debug_mode

    @scan_debug_mode.setter
    def scan_debug_mode(self, value):
        self.__scan_debug_mode = value

    def _child_session_iter(self):
        sessions_tree = self.sessions_tree
        for child_session in reversed(
            list(sessions_tree.expand_tree(mode=Tree.WIDTH))[1:]
        ):
            yield child_session

    def _aliases_info(self, cache={"aliases": {}, "config_id": None}):
        aliases = cache["aliases"]
        config_id = id(self.__config_aliases)
        if cache["config_id"] != config_id:
            aliases.clear()
            cache["config_id"] = config_id
        if aliases:
            return aliases

        for child_session in self._child_session_iter():
            aliases.update(child_session._aliases_info())

        for alias_cfg in self.__config_aliases:
            cfg = alias_cfg.clone()
            aliases[cfg.pop("original_name")] = cfg

        return aliases

    @property
    def object_names(self, cache={"objects_names": [], "config_id": None}):
        objects_names = cache["objects_names"]
        config_id = id(self.__config_objects_names)
        if cache["config_id"] != config_id:
            objects_names.clear()
            cache["config_id"] = config_id
        if objects_names:
            return objects_names

        names_list = list()
        for child_session in self._child_session_iter():
            names_list.extend(child_session.object_names)

        session_config = self.config.get_config(self.name)

        if self.__config_objects_names is None:
            names_list = list()
            for name in self.config.names_list:
                cfg = self.config.get_config(name)
                if cfg.get("class", "").lower() == "session":
                    continue
                if cfg.get_inherited("plugin") == "default":
                    continue
                names_list.append(name)
        else:
            names_list.extend(self.__config_objects_names[:])
            # Check if other session in config-objects
            for name in names_list:
                object_config = self.config.get_config(name)

                if object_config is None:
                    log_warning(
                        self,
                        f"In {session_config.filename} of session '{self.name}':"
                        + f" object '{name}' does not exist. Ignoring it.",
                    )
                    names_list.remove(name)
                else:
                    class_name = object_config.get("class", "")
                    if class_name.lower() == "session":
                        warnings.warn(
                            f"Session {self.name} 'config-objects' list contains session "
                            + f"{name}, ignoring (hint: add session in 'include-sessions' list)",
                            RuntimeWarning,
                        )
                        names_list.remove(name)

        for name in self.__exclude_objects_names:
            try:
                names_list.remove(name)
            except (ValueError, AttributeError):
                pass
        seen = set()
        objects_names.clear()
        objects_names.extend(x for x in names_list if not (x in seen or seen.add(x)))
        return objects_names

    @property
    def sessions_tree(self):
        """
        return children session as a tree
        """
        if self.__children_tree is None:
            children = {self.name: (1, list())}
            tree = Tree()
            tree.create_node(tag=self.name, identifier=self)
            tree = self._build_children_tree(tree, self, children)
            multiple_ref_child = [
                (name, parents) for name, (ref, parents) in children.items() if ref > 1
            ]
            if multiple_ref_child:
                msg = "Session %s as cyclic references to sessions:\n" % self.name
                msg += "\n".join(
                    "session %s is referenced in %r" % (session_name, parents)
                    for session_name, parents in multiple_ref_child
                )
                raise RuntimeError(msg)
            self.__children_tree = tree
        return self.__children_tree

    def _build_children_tree(self, tree, parent, children):
        if self.__include_sessions is not None:
            for session_name in self.__include_sessions:
                nb_ref, parents = children.get(session_name, (0, list()))
                nb_ref += 1
                children[session_name] = (nb_ref, parents)
                parents.append(self.name)
                if nb_ref > 1:  # avoid cyclic reference
                    continue

                child = self.config.get(session_name)
                tree.create_node(tag=session_name, identifier=child, parent=parent)
                child._build_children_tree(tree, child, children)
        return tree

    @property
    def env_dict(self):
        return self.__env_dict

    def _emit_event(self, event, **kwargs):
        if event in scan_saving.ESRFDataPolicyEvent:
            self.__data_policy_events.post(dict(event_type=event, value=kwargs))
        else:
            raise NotImplementedError

    def _set_scan_saving(self, cls=None):
        """Defines the data policy, which includes the electronic logbook"""
        if cls is None:
            cls = scan_saving.BasicScanSaving
        self.__scan_saving._init(cls, self.name, self.name)
        if (
            isinstance(self.scan_saving, scan_saving.ESRFScanSaving)
            and icat_client_is_disabled()
        ):
            log_warning(
                self,
                "The ICAT client is disabled in the beacon configuration. Datasets will not be registered and e-logbook messages are lost.",
            )

    @property
    def _config_scan_saving_class(self) -> Optional[scan_saving.BasicScanSaving]:
        scan_saving_class_name = self.__scan_saving_config.get("class")
        if not isinstance(scan_saving_class_name, str) or not scan_saving_class_name:
            return
        parts = scan_saving_class_name.split(".")
        if len(parts) == 0:
            return
        if len(parts) == 1:
            module = scan_saving
        else:
            try:
                module = __import__(".".join(parts[:-1]))
            except ImportError:
                return
        try:
            return getattr(module, parts[-1])
        except AttributeError:
            return

    def set_error_report(self, error_report):
        self.error_report = error_report

    def _set_scan_display(self):
        self.scan_display = scan_display.ScanDisplay(self.name)
        if is_bliss_shell():
            self.env_dict["SCAN_DISPLAY"] = self.scan_display
        if isinstance(self.env_dict, ProtectedDict):
            self.env_dict._protect("SCAN_DISPLAY")

    def enable_esrf_data_policy(self):
        self._set_scan_saving(cls=scan_saving.ESRFScanSaving)
        self._emit_event(
            scan_saving.ESRFDataPolicyEvent.Enable,
            data_path=self.scan_saving.get_path(),
        )

    def disable_esrf_data_policy(self):
        self._set_scan_saving()
        self._emit_event(
            scan_saving.ESRFDataPolicyEvent.Disable,
            data_path=self.scan_saving.get_path(),
        )

    def load_script(self, script_module_name, session=None):
        """
        load a script name script_module_name and export all public
        (not starting with _) object and function in env_dict.
        just print exception but not throwing it.

        Args:
            script_module_name the python file you want to load
            session (optional) the session from which to load the script

        Return:
            True if script has been loaded without error

        Warning: this method relies on having 'env_dict' **injected** into its
        globals. This is to give a "context" to the load_script method. The
        more traditional approach of passing it via keyword arg cannot work
        because we do not want 'env_dict' to appear in completion options in the
        shell (see issue #3718). Hopefully one day soon we will remove all
        "load_script"-family features to be closer to "real" Python, although this
        could be perturbating for users who are not programmers, because it would
        put the stress on namespaces and imports vs "one big global scope" (which we
        try hard to emulate, and which is causing headaches !)
        """
        if session is None:
            session = self
        elif isinstance(session, str):
            session = self.config.get(session)

        globals_dict = inspect.currentframe().f_back.f_globals
        if not session._scripts_module_path:
            raise RuntimeError(f"{session.name} session has no script module path")

        try:
            importer = _StringImporter(
                session._scripts_module_path, session.name, in_load_script=True
            )
            importer.set_error_report(self.error_report)

            try:
                sys.meta_path.insert(0, importer)

                module_name = "%s.%s.%s" % (
                    _StringImporter.BASE_MODULE_NAMESPACE,
                    session.name,
                    os.path.splitext(script_module_name)[0],
                )

                success = False
                try:
                    script_module, success = importer.load_module(
                        module_name, globals_dict
                    )
                except ImportError:
                    raise RuntimeError(f"Cannot find module {module_name}")

                self._update_env_dict_from_globals_dict(
                    script_module.__dict__, globals_dict, verbose=False
                )
                self._update_env_dict_from_globals_dict(
                    script_module.__dict__, env_dict, verbose=False  # noqa: F821
                )

                # fmt: off
                if not success:
                    env_dict[f"_{id(self)}_load_script_errors"][  # noqa: F821
                        module_name
                    ] = False
                # fmt: on
                return success
            finally:
                sys.meta_path.remove(importer)
        except Exception as e:
            raise RuntimeError(f"Error while loading '{script_module_name}'") from e

    def _update_env_dict_from_globals_dict(
        self, globals_dict, env_dict=None, verbose=True
    ):
        if env_dict is None:
            env_dict = self.env_dict
        for k, v in globals_dict.items():
            if k.startswith("_"):
                continue
            if k in env_dict and v is not env_dict[k]:
                if isinstance(env_dict[k], UserNamespace) and isinstance(
                    v, UserNamespace
                ):
                    # merge namespaces
                    env_dict[k] = env_dict[k] + v
                    continue
                if verbose:
                    print(f"Replaced [{k}] in session env")
            env_dict[k] = v

    def _get_user_script_home(self):
        return self.__user_script_homedir.get()

    def _set_user_script_home(self, dir):
        self.__user_script_homedir.set(dir)

    def _reset_user_script_home(self):
        if self.__default_user_script_homedir:
            self.__user_script_homedir.set(self.__default_user_script_homedir)
        else:
            self.__user_script_homedir.clear()

    def user_script_homedir(self, new_dir=None, reset=False):
        """
        Set or get local user script home directory

        Args:
            None -> returns current user script home directory
            new_dir (optional) -> set user script home directory to new_dir
            reset (optional) -> reset previously set user script home directory
        """
        if reset:
            self._reset_user_script_home()
        elif new_dir is not None:
            if not os.path.isabs(new_dir):
                raise RuntimeError(f"Directory path must be absolute [{new_dir}]")
            if not os.path.isdir(new_dir):
                raise RuntimeError(f"Invalid directory [{new_dir}]")
            self._set_user_script_home(new_dir)
        else:
            return self._get_user_script_home()

    def user_script_list(self):
        """List python scripts from user script home directory"""
        rootdir = self._get_user_script_home()
        if not rootdir:
            print(
                "First, you need to set a directory with `user_script_homedir(path_to_dir)`"
            )
            raise RuntimeError("User scripts home directory not configured")
        if not os.path.isdir(rootdir):
            raise RuntimeError(f"Invalid directory [{rootdir}]")

        print(f"List of python scripts in [{rootdir}]:")
        for (dirpath, dirnames, filenames) in os.walk(rootdir):
            dirname = dirpath.replace(rootdir, "")
            dirname = dirname.lstrip(os.path.sep)
            for filename in filenames:
                _, ext = os.path.splitext(filename)
                if ext != ".py":
                    continue
                print(f" - {os.path.join(dirname, filename)}")

    def user_script_load(self, scriptname=None, export_global="user"):
        """
        load a script and export all public (= not starting with _)
        objects and functions to current environment or to a namespace.
        (exceptions are printed but not thrown, execution is stopped)

        Args:
            scriptname: the python file to load (script path can be absolute relative to script_homedir)
        Optional args:
            export_global="user" (default): export objects to "user" namespace in session env dict (eg. user.myfunc())
            export_global=False: return a namespace
            export_global=True: export objects to session env dict
        """
        return self._user_script_exec(
            scriptname, load=True, export_global=export_global
        )

    def user_script_run(self, scriptname=None):
        """
        Execute a script without exporting objects or functions to current environment.
        (exceptions are printed but not thrown, execution is stopped)

        Args:
            scriptname: the python file to run (script path can be absolute or relative to script_homedir)
        """
        self._user_script_exec(scriptname, load=False)

    def _user_script_exec(self, scriptname, load=False, export_global=False):
        if not scriptname:
            self.user_script_list()
            return

        if os.path.isabs(scriptname):
            filepath = scriptname
        else:
            if not self._get_user_script_home():
                print(
                    "First, you need to set a directory with `user_script_homedir(path_to_dir)`"
                )
                raise RuntimeError("User scripts home directory not configured")

            homedir = os.path.abspath(self._get_user_script_home())
            filepath = os.path.join(homedir, scriptname)

        _, ext = os.path.splitext(scriptname)
        if not ext:
            filepath += ".py"
        if not os.path.isfile(filepath):
            raise RuntimeError(f"Cannot find [{filepath}] !")
        try:
            with open(filepath, "r") as f:
                f.read()
        except Exception:
            raise RuntimeError(f"Failed to read [{filepath}] !")

        if load:
            print(f"Loading [{filepath}]")
        else:
            print(f"Running [{filepath}]")

        module_name = inspect.getmodulename(filepath)
        spec = importlib.util.spec_from_file_location(module_name, filepath)
        # work around Python bug https://bugs.python.org/issue31772
        spec.loader.path_stats = lambda _: {"mtime": -1, "size": -1}

        module = importlib.util.module_from_spec(spec)
        exec("from bliss.setup_globals import *\n", module.__dict__)
        spec.loader.exec_module(module)

        linecache.updatecache(filepath)
        # disable mtime check
        # see issue 1900 or tests: we do not want to show source
        # from updated file if the file is not "in action"
        size, mtime, lines, fullname = linecache.cache[filepath]
        linecache.cache[filepath] = (
            size,
            None,
            lines,
            fullname,
        )  # this removes mtime check in linecache module

        # case #1: run file
        if not load:
            return

        module_globals = {
            k: v
            for k, v in module.__dict__.items()
            if not k.startswith("_") and self.env_dict.get(k, Undefined) is not v
        }
        module_globals.pop("load_script", None)

        # case #2: export to global env dict
        if export_global is True:
            self._update_env_dict_from_globals_dict(module_globals)
        else:
            if isinstance(export_global, str):
                ns_name = export_global
                if isinstance(self.env_dict.get(ns_name), UserNamespace):
                    # case #3: export and merge to existing namespace in env dict
                    self.env_dict[ns_name] = self.env_dict[ns_name] + module_globals
                    print(f"Merged [{ns_name}] namespace in session.")
                else:
                    # case #4: export to given (non existing) namespace in env dict
                    if ns_name in self.env_dict:
                        print(f"Replaced [{ns_name}] in session env")
                    self.env_dict[ns_name] = UserNamespace(**module_globals)
                    print(f"Exported [{ns_name}] namespace in session.")
            else:
                # case #5: export_global is False, return the namespace
                return UserNamespace(**module_globals)

    def _do_setup(
        self,
        env_dict: typing.Union[dict, None],
        hook: SessionHook,
    ) -> bool:
        """
        Load configuration, and execute the setup script

        Arguments:
            env_dict: globals dictionary (or None to use current session env. dict)
            hook: Hooks to call during the setup

        Return:
            True if setup went without error, False otherwise
        """
        ret = True

        # Session environment
        if env_dict is None:
            env_dict = self.env_dict
        self.__env_dict = env_dict
        self.__env_dict["ERROR_REPORT"] = self.error_report
        if isinstance(self.env_dict, ProtectedDict):
            self.env_dict._protect("ERROR_REPORT")

        # Data policy needs to be defined before instantiating the
        # session objects
        self._set_scan_saving(cls=self._config_scan_saving_class)

        # Instantiate the session objects
        try:
            self.error_report.is_loading_config = True
            self._load_config(hook)
        except Exception:
            ret = False
            self.error_report.display_exception(*sys.exc_info())
        finally:
            self.error_report.is_loading_config = False
            env_dict["config"] = self.config
            if isinstance(env_dict, ProtectedDict):
                env_dict._protect("config")
                env_dict._protect(self.object_names)

        self._register_session_importers(self)

        self._set_scan_display()

        self._additional_variables(env_dict)

        # start populating setup_globals namespace with existing variables
        for name, item in env_dict.items():
            setattr(self.setup_globals, name, item)

        for child_session in self._child_session_iter():
            self._register_session_importers(child_session)
            child_session_ret = child_session._setup(env_dict)
            ret = ret and child_session_ret

        setup_ret = self._setup(env_dict)
        ret = ret and setup_ret

        # protect Aliases
        if isinstance(env_dict, ProtectedDict):
            for alias in env_dict["ALIASES"].names_iter():
                if alias in env_dict:
                    env_dict._protect(alias)

        return ret

    def active_session(self):
        """Active this session as a global session"""
        global_sessions[self.name] = self

    def setup(
        self,
        env_dict: dict | None = None,
        verbose: bool = False,
        hook: SessionHook | None = None,
    ) -> bool:
        """Call _do_setup, but catch exception to display error message via except hook

        In case of SystemExit: the exception is propagated.

        Return: True if setup went without error, False otherwise
        """
        self.active_session()

        if hook is None:
            hook = DefaultSessionLoading(verbose=verbose)

        try:
            ret = self._do_setup(env_dict, hook)
        except SystemExit:
            raise
        except BaseException:
            self.error_report.display_exception(*sys.exc_info())
            return False
        return ret

    @staticmethod
    def _register_session_importers(session):
        """Allows remote scripts to be registered and executed locally"""
        if session.__scripts_module_path and session.name not in _SESSION_IMPORTERS:
            importer = _StringImporter(session.__scripts_module_path, session.name)
            importer.set_error_report(session.error_report)
            sys.meta_path.append(importer)
            _SESSION_IMPORTERS.add(session.name)

    def _additional_variables(self, env_dict):
        """Add additional variables to the session environment"""
        new_globals = {}
        new_globals["SCANS"] = self.scans
        new_globals["DEFAULT_CHAIN"] = DEFAULT_CHAIN
        new_globals["ALIASES"] = global_map.aliases
        new_globals["ACTIVE_MG"] = self.active_mg
        new_globals["SCAN_SAVING"] = self.scan_saving
        if "user_script_homedir" not in new_globals:
            new_globals["user_script_homedir"] = self.user_script_homedir
        if "user_script_list" not in new_globals:
            new_globals["user_script_list"] = self.user_script_list
        if "user_script_load" not in new_globals:
            new_globals["user_script_load"] = self.user_script_load
        if "user_script_run" not in new_globals:
            new_globals["user_script_run"] = self.user_script_run

        env_dict.update(new_globals)
        if isinstance(env_dict, ProtectedDict):
            env_dict._protect(new_globals)

    def _setup(self, env_dict):
        """
        Load and execute setup file.

        Called by _do_setup() which is called by setup().
        Must return True in case of success.
        """
        print = self.env_dict.get("print", builtins.print)

        if self.setup_file is None:
            return True

        # global var to keep track of errors during setup
        env_dict[f"_{id(self)}_load_script_errors"] = {}

        print("%s: Executing setup file..." % self.name)
        setup_file_importer = _StringImporter(
            os.path.normpath(os.path.join(self._scripts_module_path, "..")),
            self.name,
            in_load_script=False,
        )
        setup_file_importer.set_error_report(self.error_report)
        sys.meta_path.insert(0, setup_file_importer)

        setup_file_module = os.path.splitext(os.path.basename(self.setup_file))[0]
        module_name = f"bliss.session.{self.name}.{setup_file_module}"

        # update load_script key in unprotected env_dict
        # (because of nested sessions and protection below)
        if isinstance(env_dict, ProtectedDict):
            unprotected_env_dict = env_dict.wrapped_dict
        else:
            unprotected_env_dict = env_dict

        # for the setup, we want 'load_script' to not complain if some protected objects
        # are redefined
        unprotected_env_dict["load_script"] = dup_meth_and_inject_env_dict(
            self.load_script, unprotected_env_dict
        )

        try:
            # the script module allows to be able to get source code (for prdef)
            script_module, success = setup_file_importer.load_module(
                module_name, env_dict
            )
        except Exception:
            self.error_report.display_exception(*sys.exc_info())
            return False
        else:
            self._update_env_dict_from_globals_dict(
                script_module.__dict__, unprotected_env_dict, verbose=False
            )
        for obj_name, obj in env_dict.items():
            setattr(self.setup_globals, obj_name, obj)

        # after setup, it is not allowed to redefine protected objects in load_script
        unprotected_env_dict["load_script"] = dup_meth_and_inject_env_dict(
            self.load_script, env_dict
        )

        if isinstance(env_dict, ProtectedDict):
            env_dict._protect("load_script")

        return success and not bool(env_dict[f"_{id(self)}_load_script_errors"])

    def close(self):
        self.setup_globals.__dict__.clear()
        for obj_name, obj in self.env_dict.items():
            if obj is self or obj is self.config:
                continue
            try:
                obj.__close__()
            except Exception:
                pass
        self.env_dict.clear()
        # remove session from global dict
        # (it may not be there, if 'close()' is called prior to 'setup()')
        global_sessions.pop(self.name, None)

    def _load_config(self, hook: SessionHook):
        item_count = 0
        hook.session_loading(self)
        for item_name in self.object_names:
            item_count += 1

            # Skip initialization of existing objects.
            if hasattr(self.setup_globals, item_name):
                self.env_dict[item_name] = getattr(self.setup_globals, item_name)
                continue

            hook.object_loading(self, item_name)
            try:
                self.config.get(item_name)
            except Exception as e:
                hook.object_error_during_loading(self, item_name, e)
            else:
                hook.object_loaded(self, item_name)

        hook.objects_loaded(self, item_count)

        # Make aliases
        for item_name, alias_cfg in self._aliases_info().items():
            alias_name = alias_cfg["alias_name"]
            try:
                global_map.aliases.add(alias_name, item_name)
            except Exception:
                self.error_report.display_exception(*sys.exc_info())

        # Get the session itself
        try:
            self.config.get(self.name)
        except Exception:
            self.error_report.display_exception(*sys.exc_info())

        self.setup_globals.__dict__.update(self.env_dict)
        hook.session_loaded(self)

    def resetup(self, verbose=False):
        self.close()

        self.config.reload()

        self.init(self.config.get_config(self.name))

        linecache.clearcache()  # empty Python's source files cache (used by prdef for example)

        self.setup(self.env_dict, verbose)

    def _set_bliss_repl(self, repl: BlissRepl | None):
        """Link this session with a dedicated BlissRepl

        It is supposed to be initialized at the very easly initialization of the session,
        else never.
        """
        self.__bliss_repl = repl

    @property
    def bliss_repl(self) -> BlissRepl | None:
        """Return the dedicated BlissRepl used by this session, else None"""
        return self.__bliss_repl

    def _set_output_getter(self, output_getter):
        self.__output_getter = output_getter

    @property
    def output(self):
        """Return the output stream used by this session, if one.

        This is actually stored in the session `env_dict["__session_output"]`

        It is the responsability of the application to define it properly.
        """
        session_output = self.__output_getter
        if session_output is None:
            return None
        return session_output()


class DefaultSession(Session):
    """Session without config, setup scripts or data policy"""

    def __init__(self):
        super().__init__(constants.DEFAULT_SESSION_NAME, {"config-objects": []})

    def _set_scan_saving(self, cls=None):
        if cls is not None:
            log_warning(self, "No data policy allowed in this session.")
        super()._set_scan_saving(None)

    def enable_esrf_data_policy(self):
        pass

    def disable_esrf_data_policy(self):
        pass

    def _load_config(self, verbose=True):
        pass

    def resetup(self, verbose=False):
        pass
