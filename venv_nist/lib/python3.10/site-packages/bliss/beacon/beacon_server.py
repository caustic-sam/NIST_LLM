# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

from __future__ import annotations

import os
import platform
import weakref
import typing
from collections.abc import Callable
import logging
import socket
import errno
import contextlib
import tempfile
import gevent
from dataclasses import dataclass
from gevent.socket import cancel_wait_ex

from .beacon_api import BeaconApi

try:
    import win32api  # noqa
except ImportError:
    IS_WINDOWS = False
else:
    IS_WINDOWS = True


_logger = logging.getLogger("beacon")


class _WaitStolenReply(object):
    def __init__(
        self, beacon: BeaconServer, stolen_lock: dict[socket.socket, list[bytes]]
    ):
        self._stolen_lock = {}
        for client, objects in stolen_lock.items():
            self._stolen_lock[client] = b"|".join(objects)
        self._beacon = beacon

    def __enter__(self):
        for client, message in self._stolen_lock.items():
            event = gevent.event.Event()
            client2sync = self._beacon.waitstolen.setdefault(message, dict())
            client2sync[client] = event
            self._beacon._emit_stolen_message(client, message)
        return self

    def __exit__(self, *args, **keys):
        for client, message in self._stolen_lock.items():
            client2sync = self._beacon.waitstolen.pop(message, None)
            if client2sync is not None:
                client2sync.pop(client, None)
            if client2sync:
                self._beacon.waitstolen[message] = client2sync

    def wait(self, timeout):
        with gevent.Timeout(
            timeout, RuntimeError("some client(s) didn't reply to stolen lock")
        ):
            for client, message in self._stolen_lock.items():
                client2sync = self._beacon.waitstolen.get(message)
                if client2sync is not None:
                    sync = client2sync.get(client)
                    if sync is not None:
                        sync.wait()


@dataclass
class RedisServerInfo:
    host: str
    port: int
    external: bool
    uds_socket: typing.Optional[str] = None


class BeaconServer:
    """Beacon server state"""

    def __init__(self, options: typing.Any):
        self.waitstolen: dict[bytes, dict[socket.socket, gevent.event.Event]] = {}
        self.options = options
        self.redis_info: RedisServerInfo
        self.redis_data_info: RedisServerInfo
        self.lock_object: dict[bytes, tuple[socket.socket, int, int]] = {}
        self.client_to_object = weakref.WeakKeyDictionary[socket.socket, set[bytes]]()
        self.client_to_name = weakref.WeakKeyDictionary[socket.socket, bytes]()
        self.waiting_lock = weakref.WeakKeyDictionary[socket.socket, list[bytes]]()
        self.uds_port_name: str
        self.local_key_storage: dict[str, str] = {}
        self._stolen_callbacks: list[Callable] = []
        self._beacon_api = BeaconApi(self)

    def add_stolen_callback(self, callback: Callable):
        self._stolen_callbacks.append(callback)

    def _emit_stolen_message(self, client_id, message: bytes):
        for c in self._stolen_callbacks:
            c(client_id, message)

    def get_config_path(self, file_path: str) -> str:
        """The provided path should be a sub-path of `db_path`. It can be an absolute or
        relative path. Returns the absolute path."""
        sfile_path = os.path.expanduser(file_path)
        root_path = self.options.db_path
        if not sfile_path.startswith(root_path):
            # Make sure `file_path` is a relative path
            while sfile_path.startswith(os.sep):
                sfile_path = sfile_path[1:]
            sfile_path = os.path.join(self.options.db_path, sfile_path)
        file_path = os.path.abspath(file_path)
        if ".." in os.path.relpath(sfile_path, self.options.db_path):
            # Not allowed to access files above `db_path`
            raise PermissionError(sfile_path)
        return sfile_path

    def lock(
        self,
        client_id: socket.socket,
        priority: int,
        lock_obj: list[bytes],
        raw_message,
    ) -> bool:
        """Return true if the lock was taken"""
        all_free = True
        for obj in lock_obj:
            socket_id, compteur, lock_prio = self.lock_object.get(obj, (None, 0, 0))
            if socket_id and socket_id != client_id:
                if priority > lock_prio:
                    continue
                all_free = False
                break

        if all_free:
            stolen_lock: dict[socket.socket, list[bytes]] = {}
            for obj in lock_obj:
                socket_id, compteur, lock_prio = self.lock_object.get(
                    obj, (client_id, 0, priority)
                )
                if socket_id != client_id:  # still lock
                    pre_obj = stolen_lock.get(socket_id, None)
                    if pre_obj is None:
                        stolen_lock[socket_id] = [obj]
                    else:
                        pre_obj.append(obj)
                    self.lock_object[obj] = (client_id, 1, priority)
                    objset = self.client_to_object.get(socket_id, set())
                    objset.remove(obj)
                else:
                    compteur += 1
                    new_prio = lock_prio > priority and lock_prio or priority
                    self.lock_object[obj] = (client_id, compteur, new_prio)

            try:
                with _WaitStolenReply(self, stolen_lock) as w:
                    w.wait(3.0)
            except RuntimeError:
                _logger.warning("Some client(s) didn't reply to the stolen lock")

            obj_already_locked = self.client_to_object.get(client_id, set())
            self.client_to_object[client_id] = set(lock_obj).union(obj_already_locked)
            return True
        else:
            self.waiting_lock[client_id] = lock_obj
            return False

    def unlock(
        self, client_id: socket.socket, priority: int, unlock_obj: list[bytes]
    ) -> list[bytes]:
        unlock_object: list[bytes] = []
        client_locked_obj = self.client_to_object.get(client_id, None)
        if client_locked_obj is None:
            return unlock_object

        for obj in unlock_obj:
            socket_id, compteur, prio = self.lock_object.get(obj, (None, 0, 0))
            if socket_id and socket_id == client_id:
                compteur -= 1
                if compteur <= 0:
                    self.lock_object.pop(obj)
                    try:
                        client_locked_obj.remove(obj)
                        self.lock_object.pop(obj)
                    except KeyError:
                        pass
                    unlock_object.append(obj)
                else:
                    self.lock_object[obj] = (client_id, compteur, prio)

        return unlock_object

    @contextlib.contextmanager
    def start_tcp_server(self):
        """Part of the 'Beacon server'"""
        tcp = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        tcp.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            tcp.bind(("", self.options.port))
        except OSError as e:
            if e.errno == errno.EADDRINUSE:
                raise RuntimeError(f"Port {self.options.port} already in use") from e
            else:
                raise e
        tcp.listen(512)  # limit to 512 clients
        try:
            yield tcp
        finally:
            tcp.close()

    @contextlib.contextmanager
    def start_uds_server(self):
        """Part of the 'Beacon server'"""
        if IS_WINDOWS:
            self.uds_port_name = None
            yield None
            return
        path = tempfile._get_default_tempdir()
        random_name = next(tempfile._get_candidate_names())
        uds_port_name = os.path.join(path, f"beacon_{random_name}.sock")
        self.uds_port_name = uds_port_name
        try:
            uds = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            uds.bind(uds_port_name)
            os.chmod(uds_port_name, 0o777)
            uds.listen(512)
            try:
                yield uds
            finally:
                uds.close()
        finally:
            try:
                os.unlink(uds_port_name)
            except Exception:
                pass

    def tcp_server_main(self, sock: socket.socket):
        """Beacon server: listen on TCP port"""
        port = sock.getsockname()[1]
        _logger.info("start listening on TCP port %s", port)
        _logger.info("configuration path: %s", self.options.db_path)
        try:
            while True:
                try:
                    new_socket, addr = sock.accept()
                except cancel_wait_ex:
                    return
                if platform.system() != "Windows":
                    new_socket.setsockopt(socket.SOL_IP, socket.IP_TOS, 0x10)
                localhost = addr[0] == "127.0.0.1"
                gevent.spawn(
                    self._beacon_api.serve_client_forever, new_socket, localhost
                )
        finally:
            _logger.info("stop listening on TCP port %s", port)

    def uds_server_main(self, sock: socket.socket):
        """Beacon server: listen on UDS socket"""
        _logger.info("start listening on UDS socket %s", self.uds_port_name)
        try:
            while True:
                try:
                    new_socket, addr = sock.accept()
                except cancel_wait_ex:
                    return
                gevent.spawn(self._beacon_api.serve_client_forever, new_socket, True)
        finally:
            _logger.info("stop listening on UDS socket %s", self.uds_port_name)
