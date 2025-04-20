# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

from __future__ import annotations

import os
import sys
import json
import logging
import pkgutil
import socket
import codecs
import shutil
import traceback
import select
import functools
import typing

from ruamel.yaml import YAML, YAMLError

from bliss.common import event
from . import protocol

if typing.TYPE_CHECKING:
    from .beacon_server import BeaconServer


_logger = logging.getLogger("beacon.api")


def __remove_empty_tree(base_dir: str, keep_empty_base: bool = True):
    """
    Helper to remove empty directory tree.

    If *base_dir* is *None* (meaning start at the beacon server base directory),
    the *keep_empty_base* is forced to True to prevent the system from removing
    the beacon base path

    :param base_dir: directory to start from [default is None meaning start at
                     the beacon server base directory
    :type base_dir: str
    :param keep_empty_base: if True (default) doesn't remove the given
                            base directory. Otherwise the base directory is
                            removed if empty.
    """
    for dir_path, dir_names, file_names in os.walk(base_dir, topdown=False):
        if keep_empty_base and dir_path == base_dir:
            continue
        if file_names:
            continue
        for dir_name in dir_names:
            full_dir_name = os.path.join(dir_path, dir_name)
            if not os.listdir(full_dir_name):  # check if directory is empty
                os.removedirs(full_dir_name)


TreeNode = typing.Union[dict[str, "TreeNode"], None]
"""None for files, else the structure of the directory"""


def _get_directory_structure(base_dir: str) -> TreeNode:
    """
    Helper that creates a nested dictionary that represents the folder structure of base_dir
    """
    result: dict[str, TreeNode] = {}
    base_dir = base_dir.rstrip(os.sep)
    start = base_dir.rfind(os.sep) + 1
    for path, dirs, files in os.walk(base_dir, followlinks=True, topdown=True):
        # with topdown=True, the search can be pruned by altering 'dirs'
        dirs[:] = [d for d in dirs if d not in (".git",)]
        folders = path[start:].split(os.sep)
        subdir = dict.fromkeys((f for f in files if "~" not in f))
        parent = functools.reduce(dict.get, folders[:-1], result)
        parent[folders[-1]] = subdir
    assert len(result) == 1
    _, tree = result.popitem()
    return tree


class BeaconApi:
    """Served beacon API"""

    def __init__(self, beacon: BeaconServer):
        self.beacon: BeaconServer = beacon
        self.beacon.add_stolen_callback(self._send_lock_stolen)

    def _send_redis_info(self, client_id: socket.socket, local_connection: bool):
        if local_connection and self.beacon.redis_info.uds_socket:
            port = self.beacon.redis_info.uds_socket
            host = "localhost"
        else:
            port = str(self.beacon.redis_info.port)
            host = self.beacon.redis_info.host

        contents = b"%s:%s" % (host.encode(), port.encode())
        client_id.sendall(protocol.message(protocol.REDIS_QUERY_ANSWER, contents))

    def _send_redis_data_server_info(
        self, client_id: socket.socket, message: bytes, local_connection: bool
    ):
        try:
            message_key, _ = message.split(b"|")
        except ValueError:
            # message is bad, skip it
            _logger.error("Unsupported message %a", message, exc_info=True)
            return

        if local_connection and self.beacon.redis_data_info.uds_socket:
            port = self.beacon.redis_data_info.uds_socket
            host = "localhost"
        else:
            port = str(self.beacon.redis_data_info.port)
            host = self.beacon.redis_data_info.host
        contents = b"%s|%s|%s" % (message_key, host.encode(), port.encode())
        client_id.sendall(protocol.message(protocol.REDIS_DATA_SERVER_OK, contents))

    def _send_config_file(self, client_id: socket.socket, message: bytes):
        _logger.debug("send_config_file %a", message)
        try:
            message_key, bfile_path = message.split(b"|")
        except ValueError:
            # message is bad, skip it
            _logger.error("Unsupported message %a", message, exc_info=True)
            return
        file_path = self.beacon.get_config_path(bfile_path.decode())
        try:
            with open(file_path, "rb") as f:
                buffer = f.read()
                client_id.sendall(
                    protocol.message(
                        protocol.CONFIG_GET_FILE_OK, b"%s|%s" % (message_key, buffer)
                    )
                )
        except IOError:
            client_id.sendall(
                protocol.message(
                    protocol.CONFIG_GET_FILE_FAILED,
                    b"%s|File doesn't exist" % (message_key),
                )
            )

    def _send_set_key(self, client_id: socket.socket, message: bytes):
        _logger.debug("send_set_key %a", message)
        try:
            message_key, cmd_key, cmd_value = message.split(b"|", 2)
            key_name = cmd_key.decode("utf-8")
            value = cmd_value.decode("utf-8")
        except ValueError as e:
            client_id.sendall(
                protocol.message(
                    protocol.KEY_SET_FAILED,
                    b"%s|%s" % (message_key, e.args[0]),
                )
            )
            return
        self.beacon.local_key_storage[key_name] = value
        contents = b"%s" % (message_key)
        client_id.sendall(protocol.message(protocol.KEY_SET_OK, contents))

    def _send_get_key(self, client_id: socket.socket, message: bytes):
        _logger.debug("send_get_key %a", message)
        try:
            message_key, cmd_key = message.split(b"|", 1)
            key_name = cmd_key.decode("utf-8")
        except ValueError as e:
            client_id.sendall(
                protocol.message(
                    protocol.KEY_GET_FAILED,
                    b"%s|%s" % (message_key, e.args[0]),
                )
            )
            return
        value = self.beacon.local_key_storage.get(key_name, None)
        if value is None:
            client_id.sendall(
                protocol.message(
                    protocol.KEY_GET_UNDEFINED,
                    b"%s" % (message_key),
                )
            )
            return
        contents = b"%s|%s" % (message_key, value.encode("utf-8"))
        client_id.sendall(protocol.message(protocol.KEY_GET_OK, contents))

    def _send_db_base_path(self, client_id: socket.socket, message: bytes):
        try:
            message_key, _ = message.split(b"|")
        except ValueError:
            _logger.error("Unsupported message %a", message, exc_info=True)
            return
        client_id.sendall(
            protocol.message(
                protocol.CONFIG_GET_DB_BASE_PATH_OK,
                bytes(f"{int(message_key)}|{self.beacon.options.db_path}", "utf-8"),
            )
        )

    def _send_config_db_files(self, client_id: socket.socket, message: bytes):
        _logger.debug("send_config_db_files %a", message)
        try:
            message_key, bpath = message.split(b"|")
        except ValueError:
            # message is bad, skip it
            _logger.error("Unsupported message %a", message, exc_info=True)
            return
        path = self.beacon.get_config_path(bpath.decode())
        yaml = YAML(pure=True)
        try:
            for root, dirs, files in os.walk(path, followlinks=True):
                try:
                    files.remove("__init__.yml")
                except ValueError:  # init not in files list
                    pass
                else:
                    try:
                        with open(os.path.join(root, "__init__.yml"), "rt") as f:
                            yaml_content = yaml.load(f)
                        skipped_by_bliss = yaml_content.get("bliss_ignored", False)
                    except (YAMLError, AttributeError):
                        skipped_by_bliss = False
                    if skipped_by_bliss:
                        # This part of the resource tree was not provided for BLISS
                        _logger.debug("Skip %s", root)
                        # Stop the recursive walk
                        dirs.clear()
                        continue
                    files.insert(0, "__init__.yml")
                for filename in files:
                    if filename.startswith("."):
                        continue
                    basename, ext = os.path.splitext(filename)
                    if ext == ".yml":
                        full_path = os.path.join(root, filename)
                        rel_path = full_path[len(self.beacon.options.db_path) + 1 :]
                        try:
                            with codecs.open(full_path, "r", "utf-8") as f:
                                raw_buffer = f.read().encode("utf-8")
                                msg = protocol.message(
                                    protocol.CONFIG_DB_FILE_RX,
                                    b"%s|%s|%s"
                                    % (message_key, rel_path.encode(), raw_buffer),
                                )
                                client_id.sendall(msg)
                        except Exception as e:
                            sys.excepthook(*sys.exc_info())
                            client_id.sendall(
                                protocol.message(
                                    protocol.CONFIG_DB_FAILED,
                                    b"%s|%s" % (message_key, repr(e).encode()),
                                )
                            )
        except Exception as e:
            sys.excepthook(*sys.exc_info())
            client_id.sendall(
                protocol.message(
                    protocol.CONFIG_DB_FAILED,
                    b"%s|%s" % (message_key, repr(e).encode()),
                )
            )
        finally:
            client_id.sendall(
                protocol.message(protocol.CONFIG_DB_END, b"%s|" % (message_key))
            )

    def _send_config_db_tree(self, client_id: socket.socket, message: bytes):
        _logger.debug("send_config_db_tree %a", message)
        try:
            message_key, blook_path = message.split(b"|")
        except ValueError:
            # message is bad, skip it
            _logger.error("Unsupported message %a", message, exc_info=True)
            return
        look_path = self.beacon.get_config_path(blook_path.decode())
        try:
            tree = _get_directory_structure(look_path)
            msg = (
                protocol.CONFIG_GET_DB_TREE_OK,
                b"%s|%s" % (message_key, json.dumps(tree).encode()),
            )
        except Exception as e:
            sys.excepthook(*sys.exc_info())
            msg = (
                protocol.CONFIG_GET_DB_TREE_FAILED,
                b"%s|Failed to get tree: %s" % (message_key, str(e).encode()),
            )
        client_id.sendall(protocol.message(*msg))

    def _send_uds_connection(self, client_id: socket.socket, client_hostname: bytes):
        sclient_hostname = client_hostname.decode()
        try:
            if self.beacon.uds_port_name and sclient_hostname == socket.gethostname():
                client_id.sendall(
                    protocol.message(
                        protocol.UDS_OK, self.beacon.uds_port_name.encode()
                    )
                )
            else:
                client_id.sendall(protocol.message(protocol.UDS_FAILED))
        except BaseException:
            sys.excepthook(*sys.exc_info())

    def _send_lock_stolen(self, client_id, message: bytes):
        client_id.sendall(protocol.message(protocol.LOCK_STOLEN, message))

    def _send_who_locked(self, client_id, message: bytes):
        message_key, *names = message.split(b"|")
        if not names:
            names = [n for n in self.beacon.lock_object.keys()]

        for name in names:
            socket_id, _compteur, _priority = self.beacon.lock_object.get(
                name, (None, None, None)
            )
            if socket_id is None:
                continue
            msg = b"%s|%s|%s" % (
                message_key,
                name,
                self.beacon.client_to_name.get(socket_id, b"Unknown"),
            )
            client_id.sendall(protocol.message(protocol.WHO_LOCKED_RX, msg))
        client_id.sendall(
            protocol.message(protocol.WHO_LOCKED_END, b"%s|" % message_key)
        )

    def _send_log_server_address(self, client_id: socket.socket, message: bytes):
        message_key, *names = message.split(b"|")
        port = self.beacon.options.log_server_port
        host = socket.gethostname().encode()
        if not port:
            # lo log server
            client_id.sendall(
                protocol.message(
                    protocol.LOG_SERVER_ADDRESS_FAIL,
                    b"%s|%s" % (message_key, b"no log server"),
                )
            )
        else:
            client_id.sendall(
                protocol.message(
                    protocol.LOG_SERVER_ADDRESS_OK,
                    b"%s|%s|%d" % (message_key, host, port),
                )
            )

    def _send_unknow_message(self, client_id: socket.socket, message: bytes):
        client_id.sendall(protocol.message(protocol.UNKNOW_MESSAGE, message))

    def _send_module(
        self, client_id: socket.socket, message_key, path, parent_name=None
    ):
        for info in pkgutil.walk_packages([path]):
            module_name = (
                info.name if parent_name is None else "%s.%s" % (parent_name, info.name)
            )
            mdl = info.module_finder.find_module(info.name)
            if mdl is None:
                _logger.warning("Module '%s' can't be imported", info.name)
                continue

            client_id.sendall(
                protocol.message(
                    protocol.CONFIG_GET_PYTHON_MODULE_RX,
                    b"%s|%s|%s"
                    % (
                        message_key,
                        module_name.encode(),
                        mdl.get_filename().encode(),
                    ),
                )
            )
            if info.ispkg:
                self._send_module(
                    client_id, message_key, os.path.join(path, info.name), module_name
                )

    def _send_python_module(self, client_id: socket.socket, message: bytes):
        try:
            message_key, bstart_module_path = message.split(b"|")
        except ValueError:
            client_id.sendall(
                protocol.message(
                    protocol.CONFIG_GET_PYTHON_MODULE_FAILED,
                    b"%s|Can't split message (%s)" % (message_key, message),
                )
            )
            return

        start_module_path = self.beacon.get_config_path(bstart_module_path.decode())

        self._send_module(client_id, message_key, start_module_path)
        client_id.sendall(
            protocol.message(
                protocol.CONFIG_GET_PYTHON_MODULE_END, b"%s|" % message_key
            )
        )

    def _remove_config_file(self, client_id: socket.socket, message: bytes):
        try:
            message_key, bfile_path = message.split(b"|")
        except ValueError:
            # message is bad, skip it
            _logger.error("Unsupported message %a", message, exc_info=True)
            return
        file_path = self.beacon.get_config_path(bfile_path.decode())
        try:
            if os.path.isfile(file_path):
                os.remove(file_path)
            elif os.path.isdir(file_path):
                shutil.rmtree(file_path)

            # walk back in directory tree removing empty directories. Do this to
            # prevent future rename operations to inadvertely ending up inside a
            # "transparent" directory instead of being renamed
            __remove_empty_tree(self.beacon.options.db_path)
            msg = (protocol.CONFIG_REMOVE_FILE_OK, b"%s|0" % (message_key,))
        except IOError:
            msg = (
                protocol.CONFIG_REMOVE_FILE_FAILED,
                b"%s|File/directory doesn't exist" % message_key,
            )
        else:
            event.send(None, "config_changed")

        client_id.sendall(protocol.message(*msg))

    def _move_config_path(self, client_id: socket.socket, message: bytes):
        # should work on both files and folders
        # it can be used for both move and rename
        try:
            message_key, bsrc_path, bdst_path = message.split(b"|")
        except ValueError:
            # message is bad, skip it
            _logger.error("Unsupported message %a", message, exc_info=True)
            return
        src_path = self.beacon.get_config_path(bsrc_path.decode())
        dst_path = self.beacon.get_config_path(bdst_path.decode())

        try:
            # make sure the parent directory exists
            parent_dir = os.path.dirname(dst_path)
            if not os.path.isdir(parent_dir):
                os.makedirs(parent_dir)
            shutil.move(src_path, dst_path)

            # walk back in directory tree removing empty directories. Do this to
            # prevent future rename operations to inadvertely ending up inside a
            # "transparent" directory instead of being renamed
            __remove_empty_tree(self.beacon.options.db_path)
            msg = (protocol.CONFIG_MOVE_PATH_OK, b"%s|0" % (message_key,))
        except IOError as ioe:
            msg = (
                protocol.CONFIG_MOVE_PATH_FAILED,
                b"%s|%s: %s"
                % (message_key, ioe.filename.encode(), ioe.strerror.encode()),
            )
        else:
            event.send(None, "config_changed")
        client_id.sendall(protocol.message(*msg))

    def _write_config_db_file(self, client_id: socket.socket, message: bytes):
        first_pos = message.find(b"|")
        second_pos = message.find(b"|", first_pos + 1)

        if first_pos < 0 or second_pos < 0:  # message malformed
            msg = protocol.message(
                protocol.CONFIG_SET_DB_FILE_FAILED,
                b"%s|%s" % (message, b"Malformed message"),
            )
            client_id.sendall(msg)
            return

        message_key = message[:first_pos]
        file_path = message[first_pos + 1 : second_pos].decode()
        content = message[second_pos + 1 :]
        file_path = self.beacon.get_config_path(file_path)
        file_dir = os.path.dirname(file_path)
        if not os.path.isdir(file_dir):
            os.makedirs(file_dir)
        try:
            with open(file_path, "wb") as f:
                f.write(content)
                msg = protocol.message(
                    protocol.CONFIG_SET_DB_FILE_OK, b"%s|0" % message_key
                )
        except BaseException:
            msg = protocol.message(
                protocol.CONFIG_SET_DB_FILE_FAILED,
                b"%s|%s" % (message_key, traceback.format_exc().encode()),
            )
        else:
            event.send(None, "config_changed")
        client_id.sendall(msg)

    def _get_set_client_id(
        self, client_id: socket.socket, message_type: int, message: bytes
    ):
        message_key, message = message.split(b"|")
        if message_type is protocol.CLIENT_SET_NAME:
            self.beacon.client_to_name[client_id] = message
        msg = b"%s|%s" % (message_key, self.beacon.client_to_name.get(client_id, b""))
        client_id.sendall(protocol.message(protocol.CLIENT_NAME_OK, msg))

    def _release_all_locks(self, client_id: socket.socket):
        objset = self.beacon.client_to_object.pop(client_id, set())
        for obj in objset:
            self.beacon.lock_object.pop(obj)
        # Inform waiting client
        tmp_dict = dict(self.beacon.waiting_lock)
        for client_sock, tlo in tmp_dict.items():
            try_lock_object = set(tlo)
            if try_lock_object.intersection(objset):
                self.beacon.waiting_lock.pop(client_sock)
                try:
                    client_sock.sendall(protocol.message(protocol.LOCK_RETRY))
                except OSError:
                    # maybe this client is dead or whatever
                    continue

    def _clean(self, client_id: socket.socket):
        self._release_all_locks(client_id)

    def serve_client_forever(self, client_id: socket.socket, local_connection: bool):
        tcp_data = b""
        try:
            stopFlag = False
            while not stopFlag:
                try:
                    raw_data = client_id.recv(16 * 1024)
                except BaseException:
                    break

                if raw_data:
                    tcp_data = b"%s%s" % (tcp_data, raw_data)
                else:
                    break

                data = tcp_data

                while data:
                    try:
                        message_type, message, data = protocol.unpack_message(data)
                        if message_type == protocol.LOCK:
                            lock_objects = message.split(b"|")
                            prio = int(lock_objects.pop(0))
                            if self.beacon.lock(client_id, prio, lock_objects, message):
                                client_id.sendall(
                                    protocol.message(protocol.LOCK_OK_REPLY, message)
                                )
                        elif message_type == protocol.UNLOCK:
                            lock_objects = message.split(b"|")
                            prio = int(lock_objects.pop(0))
                            unlocked_objects = self.beacon.unlock(
                                client_id, prio, lock_objects
                            )
                            sunlocked_objects = set(unlocked_objects)
                            tmp_dict = dict(self.beacon.waiting_lock)
                            for client_sock, tlo in tmp_dict.items():
                                try_lock_object = set(tlo)
                                if try_lock_object.intersection(sunlocked_objects):
                                    self.beacon.waiting_lock.pop(client_sock)
                                    client_sock.sendall(
                                        protocol.message(protocol.LOCK_RETRY)
                                    )
                        elif message_type == protocol.LOCK_STOLEN_OK_REPLY:
                            client2sync = self.beacon.waitstolen.get(message)
                            if client2sync is not None:
                                sync = client2sync.get(client_id)
                                if sync is not None:
                                    sync.set()
                        elif message_type == protocol.REDIS_QUERY:
                            self._send_redis_info(client_id, local_connection)
                        elif message_type == protocol.REDIS_DATA_SERVER_QUERY:
                            self._send_redis_data_server_info(
                                client_id, message, local_connection
                            )
                        elif message_type == protocol.CONFIG_GET_FILE:
                            self._send_config_file(client_id, message)
                        elif message_type == protocol.CONFIG_GET_DB_BASE_PATH:
                            self._send_db_base_path(client_id, message)
                        elif message_type == protocol.CONFIG_GET_DB_FILES:
                            self._send_config_db_files(client_id, message)
                        elif message_type == protocol.CONFIG_GET_DB_TREE:
                            self._send_config_db_tree(client_id, message)
                        elif message_type == protocol.CONFIG_SET_DB_FILE:
                            self._write_config_db_file(client_id, message)
                        elif message_type == protocol.CONFIG_REMOVE_FILE:
                            self._remove_config_file(client_id, message)
                        elif message_type == protocol.CONFIG_MOVE_PATH:
                            self._move_config_path(client_id, message)
                        elif message_type == protocol.CONFIG_GET_PYTHON_MODULE:
                            self._send_python_module(client_id, message)
                        elif message_type == protocol.UDS_QUERY:
                            self._send_uds_connection(client_id, message)
                        elif message_type == protocol.KEY_SET:
                            self._send_set_key(client_id, message)
                        elif message_type == protocol.KEY_GET:
                            self._send_get_key(client_id, message)
                        elif message_type in (
                            protocol.CLIENT_SET_NAME,
                            protocol.CLIENT_GET_NAME,
                        ):
                            self._get_set_client_id(client_id, message_type, message)
                        elif message_type == protocol.WHO_LOCKED:
                            self._send_who_locked(client_id, message)
                        elif message_type == protocol.LOG_SERVER_ADDRESS_QUERY:
                            self._send_log_server_address(client_id, message)
                        else:
                            self._send_unknow_message(client_id, message)
                    except ValueError:
                        sys.excepthook(*sys.exc_info())
                        break
                    except protocol.IncompleteMessage:
                        r, _, _ = select.select([client_id], [], [], 0.5)
                        if not r:  # if timeout, something wired, close the connection
                            data = b""
                            stopFlag = True
                        break
                    except BaseException:
                        sys.excepthook(*sys.exc_info())
                        _logger.error("Error with client id %r, close it", client_id)
                        raise

                tcp_data = data
        except BaseException:
            sys.excepthook(*sys.exc_info())
        finally:
            try:
                self._clean(client_id)
            finally:
                client_id.close()
