# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

import collections.abc
import os
import pkgutil
import functools
import mimetypes
import importlib

import gevent
import gevent.lock

import flask
import flask.json
import json

from bliss.config.conductor import client
from bliss.config import static
from bliss.config import plugins
from bliss.common import event


def check_config(f):
    functools.wraps(f)

    def wrapper(self, *args, **kwargs):
        self.get_config()
        return f(self, *args, **kwargs)

    return wrapper


class WebConfig:

    EXT_MAP = {
        "": dict(type="text", icon="file-o"),
        "txt": dict(type="text", icon="file-o"),
        "md": dict(type="markdown", icon="file-o"),
        "yml": dict(type="yaml", icon="file-text-o"),
        "yml-err": dict(type="yaml", icon="exclamation-triangle"),
        "py": dict(type="python", icon="file-code-o"),
        "html": dict(type="html", icon="file-code-o"),
        "css": dict(type="css", icon="file-code-o"),
        "js": dict(type="javascript", icon="file-code-o"),
        "png": dict(type="image", icon="file-image-o"),
        "jpg": dict(type="image", icon="file-image-o"),
        "jpeg": dict(type="image", icon="file-image-o"),
        "gif": dict(type="image", icon="file-image-o"),
        "tif": dict(type="image", icon="file-image-o"),
        "tiff": dict(type="image", icon="file-image-o"),
    }

    def __init__(self, logger):
        self.__new_config = False
        self.__lock = gevent.lock.RLock()
        self.__items = None
        self.__tree_items = None
        self.__tree_files = None
        self.__tree_plugins = None
        self.__tree_tags = None
        self.__tree_sessions = None
        self._logger = logger
        event.connect(None, "config_changed", self.__on_config_changed)

    def __on_config_changed(self):
        with self.__lock:
            self.__new_config = True
            self.__items = None
            self.__tree_items = None
            self.__tree_files = None
            self.__tree_plugins = None
            self.__tree_tags = None
            self.__tree_sessions = None

    def get_config(self):
        with self.__lock:
            with gevent.Timeout(30, TimeoutError):
                self._logger.info("Loading beacon configuration ...")
                cfg = static.get_config()
                if self.__new_config:
                    cfg.reload()
                    self.__new_config = False
                self._logger.info("Beacon configuration loaded")
                return cfg

    @property
    def items(self):
        if self.__items is None:
            self.__items = self.__build_items()
        return self.__items

    @property
    def tree_items(self):
        if self.__tree_items is None:
            self.__tree_items = self.__build_tree_items()
        return self.__tree_items

    @property
    def tree_files(self):
        if self.__tree_files is None:
            self.__tree_files = self.__build_tree_files()
        return self.__tree_files

    @property
    def tree_plugins(self):
        if self.__tree_plugins is None:
            self.__tree_plugins = self.__build_tree_plugins()
        return self.__tree_plugins

    @property
    def tree_tags(self):
        if self.__tree_tags is None:
            self.__tree_tags = self.__build_tree_tags()
        return self.__tree_tags

    @property
    def tree_sessions(self):
        if self.__tree_sessions is None:
            self.__tree_sessions = self.__build_tree_sessions()
        return self.__tree_sessions

    def __build_items(self):
        cfg = self.get_config()
        items = {}
        for name in cfg.names_list:
            config = cfg.get_config(name)
            get_tree = _get_config_plugin(config, "get_tree")
            item = None
            if get_tree:
                try:
                    item = get_tree(config, "items")
                except Exception:
                    pass
            if item is None:
                item = dict(type="item", path=name, icon="fa fa-question")
            item["name"] = name
            item["tags"] = _get_config_user_tags(config)
            item["plugin"] = config.get("plugin")
            items[name] = item
        return items

    def __build_tree_sessions(self):
        cfg = self.get_config()
        sessions = {}
        for name, item in self.items.items():
            config = cfg.get_config(name)
            if config.plugin != "session" or config.get("class") == "MeasurementGroup":
                continue
            session_items = {}
            #            for iname, item in self.items.items():

            sessions[name] = item, session_items
        return sessions

    def __build_tree_items(self):
        items = self.items
        result = {}
        for name, item in items.items():
            current_level = result
            db_file = item["path"]
            parts = db_file.split(os.path.sep)
            full_part = ""
            for part in parts[:-1]:
                full_part = os.path.join(full_part, part)
                p_item = items.get(full_part)
                if p_item is None:
                    p_item = dict(type="folder", path=full_part, icon="fa fa-folder")
                current_level.setdefault(part, [p_item, dict()])
                current_level = current_level[part][1]
            current_level.setdefault(parts[-1], [item, dict()])
        return result

    def __build_tree_plugins(self):
        cfg = self.get_config()
        result = {}
        for name, item in self.items.items():
            config = cfg.get_config(name)
            plugin_name = config.plugin or "__no_plugin__"
            plugin_data = result.get(plugin_name)
            if plugin_data is None:
                plugin_data = [
                    dict(type="folder", path=plugin_name, icon="fa fa-folder"),
                    {},
                ]
                result[plugin_name] = plugin_data
            plugin_items = plugin_data[1]
            plugin_items[name] = [item, {}]
        return result

    def __build_tree_tags(self):
        result = {}
        for name, item in self.items.items():
            for tag in item["tags"] or ["(no tag)"]:
                tag_data = result.get(tag)
                if tag_data is None:
                    tag_data = [dict(type="folder", path=tag, icon="fa fa-folder"), {}]
                    result[tag] = tag_data
                tag_items = tag_data[1]
                tag_items[name] = [item, {}]
        return result

    def __build_tree_files(self):
        cfg = self.get_config()

        src, dst = client.get_config_db_tree(), {}
        self.__build_tree_files__(src, dst)

        items = {}
        for name in cfg.names_list:
            config = cfg.get_config(name)
            get_tree = _get_config_plugin(config, "get_tree")
            item = None
            if get_tree:
                try:
                    item = get_tree(config, "files")
                except Exception:
                    pass
            if item is None:
                item = dict(
                    type="item",
                    path=os.path.join(config.filename, name),
                    icon="fa fa-question",
                )

            item["name"] = name
            item["tags"] = _get_config_user_tags(config)
            items[item["path"]] = name, item

        for path in sorted(items):
            name, item = items[path]
            path = item["path"]
            parent = dst
            # search file node where item is defined
            for pitem in path.split(os.path.sep):
                try:
                    parent = parent[pitem][1]
                except KeyError:
                    break
            parent[name] = [item, {}]
        return dst

    def __build_tree_files__(self, src, dst, path=""):
        for name, data in src.items():
            if name.startswith(".") or name.endswith("~") or name.endswith(".rdb"):
                continue
            item_path = os.path.join(path, name)
            sub_items = {}
            if data is None:  # a file
                ext_info = self.get_file_info(item_path)
                meta = dict(
                    type="file", path=item_path, icon="fa fa-" + ext_info["icon"]
                )
            else:
                meta = dict(type="folder", path=item_path, icon="fa fa-folder")
                self.__build_tree_files__(data, sub_items, path=item_path)
            dst[name] = [meta, sub_items]
        return dst

    @check_config
    def get_file_info(self, file_name):
        ext = file_name.rpartition(os.path.extsep)[2]
        if "." not in file_name:
            ext = ""

        # checking for invalid yaml files
        cfg = self.get_config()
        if file_name in cfg.invalid_yaml_files:
            ext = "yml-err"

        return self.EXT_MAP.setdefault(ext, dict(type=ext, icon="question"))


def _get_config_plugin(cfg, member=None):
    if cfg is None:
        return
    if cfg.plugin in ("default", None):
        return
    return __get_plugin(cfg.plugin, member=member)


def __get_plugin(name, member=None):
    try:
        mod = importlib.import_module(f"..plugins.{name}", package=__name__)
    except ImportError:
        # plugin has an error
        return None
    if member:
        try:
            return getattr(mod, member)
        except AttributeError:
            # plugin has no member
            return None
    return mod


def __get_plugin_importer():
    plugins_path = os.path.dirname(plugins.__file__)
    return pkgutil.ImpImporter(path=plugins_path)


def __get_plugin_names():
    return [name for name, _ in __get_plugin_importer().iter_modules()]


def __get_plugins():
    result = {}
    for name in __get_plugin_names():
        plugin = __get_plugin(name)
        if plugin:
            result[name] = plugin
    return result


def _get_config_user_tags(config_item):
    user_tag = config_item.get(static.ConfigNode.USER_TAG_KEY, [])
    if not isinstance(user_tag, collections.abc.MutableSequence):
        user_tag = [user_tag]
    return user_tag


def config_node_to_json(x):
    encoded = json.dumps(x, cls=static.ConfigNodeDictEncoder)

    encoded = encoded.replace("-Infinity", '"-Inf!"')
    encoded = encoded.replace("Infinity", '"Infinity"')
    encoded = encoded.replace("-Inf!", "-Infinity")
    encoded = encoded.replace("NaN", '"NaN"')

    return encoded


def create_app():
    app = flask.Flask(__name__)
    __config = WebConfig(app.logger)

    @app.route("/")
    def index():
        cfg = __config.get_config()
        node = cfg.root
        institute = node.get("institute", node.get("synchrotron"))
        laboratory = node.get("laboratory", node.get("beamline"))
        full_name = " - ".join(filter(None, (institute, laboratory)))
        return flask.render_template(
            "index.html",
            name=full_name,
            institute=institute,
            laboratory=laboratory,
            icon=node.get("icon", "static/res/logo.png"),
            config=cfg,
        )

    @app.route("/main/")
    def main():
        cfg = __config.get_config()
        get_main = __get_plugin(cfg.root.plugin or "beamline", "get_main")
        if get_main:
            return get_main(cfg)
        else:
            return flask.json.dumps(dict(html="<h1>ups!</h1>"))

    @app.route("/db_files")
    def db_files():
        db_files, _ = zip(*client.get_config_db_files())
        return flask.json.dumps(db_files)

    @app.route("/db_tree")
    def db_tree():
        db_tree = client.get_config_db_tree()
        return flask.json.dumps(db_tree)

    @app.route("/db_file/<path:filename>", methods=["PUT", "GET"])
    def get_db_file(filename):
        if flask.request.method == "PUT":
            # browsers encode newlines as '\r\n' so we have to undo that crap
            content = flask.request.form["file_content"].replace("\r\n", "\n")
            client.set_config_db_file(filename, content)
            event.send(None, "config_changed")
            return flask.json.dumps(
                dict(message=f"{filename} successfully saved", type="success")
            )
        else:
            content = client.get_config_file(filename).decode("utf-8")
            return flask.json.dumps(dict(name=filename, content=content))

    @app.route("/db_file_invalid/<path:filename>")
    def get_db_file_invalid(filename):
        cfg = __config.get_config()
        if filename in cfg.invalid_yaml_files:
            result = dict(message=cfg.invalid_yaml_files[filename], type="danger")
        else:
            result = dict(message="ok", type="success")
        return flask.json.dumps(result)

    @app.route("/db_file_editor/<path:filename>")
    def get_db_file_editor(filename):
        ftype, _ = mimetypes.guess_type(filename)

        if ftype is None or ftype.startswith("text/"):
            try:
                content = client.get_config_file(filename).decode()
                file_info = __config.get_file_info(filename)
                html = flask.render_template(
                    "editor.html",
                    name=filename,
                    ftype=file_info["type"],
                    content=content,
                )
            except UnicodeDecodeError:
                html = f"failed to decode {filename}"
        # elif ftype.startswith("image/"):
        #     content = client.get_config_file(filename)
        #     data = base64.b64encode(content).decode()
        #     html = f"<img src='data:{ftype};base64,{data}' style='max-width: 100%; max-height: 100%'>"
        else:
            html = f"{ftype} mime type not handled"

        return flask.json.dumps(dict(html=html, name=filename))

    @app.route("/items/")
    def items():
        cfg = __config.get_config()

        db_files, _ = map(list, zip(*client.get_config_db_files()))

        for name in cfg.names_list:
            config = cfg.get_config(name)
            db_files.append(os.path.join(config.filename, name))

        result = dict()
        for db_file in db_files:
            current_level = result
            for part in db_file.split(os.path.sep):
                current_level.setdefault(part, [db_file, dict()])
                current_level = current_level[part][1]
        return flask.Reponse(
            config_node_to_json(result),
            mimetype="application/json",
        )

    def get_item(cfg):
        name = cfg.get("name")
        item = dict(name=name, tags=_get_config_user_tags(cfg))
        plugin = _get_config_plugin(cfg, "get_item")
        if plugin:
            cfg_dict = plugin(cfg)
            if cfg_dict.get("type") == "session":
                items_cfg_list = cfg_dict["items"]
                cfg_dict["items"] = []
                for item_cfg in items_cfg_list:
                    cfg_dict["items"].append(get_item(item_cfg))
            item.update(cfg_dict)
        return item

    def default_plugin(obj_cfg):
        return flask.render_template(
            "default_plugin.html",
            name=obj_cfg.get("name"),
            filename=obj_cfg.filename,
        )

    @app.route("/item/<name>")
    def item(name):
        cfg = __config.get_config()
        obj_cfg = cfg.get_config(name)
        return flask.Response(
            config_node_to_json(get_item(obj_cfg)),
            mimetype="application/json",
        )

    @app.route("/tree/<view>")
    def tree(view):
        if view == "files":
            result = __config.tree_files
        elif view == "items":
            result = __config.tree_items
        elif view == "plugins":
            result = __config.tree_plugins
        elif view == "tags":
            result = __config.tree_tags
        elif view == "sessions":
            result = __config.tree_sessions
        else:
            result = dict(message="unknown view", type="error")
        return flask.Response(
            config_node_to_json(result),
            mimetype="application/json",
        )

    @app.route("/page/<name>")
    def get_item_config(name):
        cfg = __config.get_config()
        obj_cfg = cfg.get_config(name)
        plugin = _get_config_plugin(obj_cfg, "get_html")
        if plugin and obj_cfg.get("class") != "MeasurementGroup":
            obj_cfg = plugin(obj_cfg)
        else:
            obj_cfg = default_plugin(obj_cfg)
        return flask.json.dumps(dict(html=obj_cfg, name=name))

    @app.route("/config/reload")
    def reload_config():
        cfg = __config.get_config()
        cfg.reload()
        event.send(None, "config_changed")
        return flask.json.dumps(
            dict(message="Configuration fully reloaded!", type="success")
        )

    @app.route("/plugins")
    def list_plugins():
        return flask.json.dumps(__get_plugin_names())

    @app.route("/plugin/<name>/<action>", methods=["GET", "POST", "PUT"])
    def handle_plugin_action(name, action):
        plugin = __get_plugin(name, member=action)
        if not plugin:
            return ""
        return plugin(__config.get_config(), flask.request)

    @app.route("/add_folder", methods=["POST"])
    def add_folder():
        cfg = __config.get_config()
        folder = flask.request.form["folder"]

        filename = os.path.join(folder, "__init__.yml")
        node = static.ConfigNode(cfg.root, filename=filename)
        node.save()
        return flask.json.dumps(dict(message="Folder created!", type="success"))

    @app.route("/add_file", methods=["POST"])
    def add_file():
        cfg = __config.get_config()
        filename = flask.request.form["file"]
        node = static.ConfigNode(cfg.root, filename=filename)
        node.save()
        return flask.json.dumps(dict(message="File created!", type="success"))

    @app.route("/remove_file", methods=["POST"])
    def remove_file():
        filename = flask.request.form["file"]
        client.remove_config_file(filename)
        return flask.json.dumps(dict(message="File deleted!", type="success"))

    @app.route("/copy_file", methods=["POST"])
    def copy_file():
        cfg = __config.get_config()
        src_path = flask.request.form["src_path"]
        dst_path = flask.request.form["dst_path"]

        # if destination is a directory (ends in '/'), append the
        # filename coming from source
        if dst_path.endswith(os.path.pathsep):
            dst_path = os.path.join(dst_path, os.path.split(src_path)[1])

        node = static.ConfigNode(cfg.root, filename=dst_path)
        node.save()

        db_files = dict(client.get_config_db_files())

        html = flask.render_template(
            "editor.html", name=dst_path, content=db_files[src_path]
        )
        result = dict(
            name=dst_path,
            html=html,
            type="warning",
            message="File copied from <i>{0}</i> to <i>{1}</i>. <br/>"
            "You <b>must</b> edit content and change element "
            "names to clear name conflicts<br/>"
            "Don't forget to <b>save</b> in order for the changes "
            "to take effect!".format(src_path, dst_path),
        )
        return flask.json.dumps(result)

    @app.route("/move_path", methods=["POST"])
    def move_path():
        src_path = flask.request.form["src_path"]
        dst_path = flask.request.form["dst_path"]
        client.move_config_path(src_path, dst_path)
        msg = "Moved from <i>{0}</i> to <i>{1}</i>".format(src_path, dst_path)
        return flask.json.dumps(dict(message=msg, type="success"))

    return app
