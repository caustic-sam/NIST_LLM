# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

from __future__ import annotations
import platform
import os
import sys
import weakref
import gevent
import typing
from gevent import socket, event, queue, lock

from functools import wraps
from collections import namedtuple
from collections.abc import Sequence

from bliss.common.greenlet_utils import protect_from_kill, AllowKill
from bliss.common.utils import Undefined
from bliss.beacon import protocol
from bliss.redis import RedisConnectionManager
from bliss.redis import RedisAddress


class StolenLockException(RuntimeError):
    """This exception is raise in case of a stolen lock"""


def check_connect(func):
    @wraps(func)
    def f(self, *args, **keys):
        self.connect()
        return func(self, *args, **keys)

    return f


class ConnectionException(Exception):
    def __init__(self, *args, **kwargs):
        Exception.__init__(self, *args, **kwargs)


RedisPoolId = namedtuple("RedisPoolId", ["db"])
RedisProxyId = namedtuple("RedisProxyId", ["db", "caching"])


class Connection:
    """A Beacon connection is created and destroyed like this:

        connection = Connection(host=..., port=...)
        connection.connect()  # not required
        connection.close()  # closes all Redis connections as well

    When `host` is not provided, it falls back to environment variable BEACON_HOST.
    When `port` is not provided, it falls back to environment variable BEACON_PORT.

    The Beacon connection also manages all Redis connections.
    Use `get_redis_proxy` to create a connection or use an existing one.
    Use `close_all_redis_connections` to close all Redis connections.

    Beacon locks: the methods `lock`, `unlock` and  `who_locked` provide
    a mechanism to acquire and release locks in the Beacon server.

    Beacon manages configuration files (YAML) and python modules. This class
    allows fetching and manipulating those.
    """

    CLIENT_NAME = f"{socket.gethostname()}:{os.getpid()}"

    class WaitingLock:
        """
        Context to wait until a set of device names can be locked.

        Arguments:
            cnt: Connection to use
            priority: Priority to check if the lock can be stolen
                      (the biggest priority takes the lock)
            device_name: A list of device name
        """

        def __init__(self, cnt: Connection, priority: int, device_name: Sequence[str]):
            self._cnt = weakref.ref(cnt)
            raw_names = [name.encode() for name in device_name]
            self._msg = b"%d|%s" % (priority, b"|".join(raw_names))
            self._queue = queue.Queue()

        def msg(self):
            return self._msg

        def get(self):
            return self._queue.get()

        def __enter__(self):
            cnt = self._cnt()
            if cnt is None:
                raise RuntimeError("Connection already released")
            pm = cnt._pending_lock.get(self._msg, [])
            if not pm:
                cnt._pending_lock[self._msg] = [self._queue]
            else:
                pm.append(self._queue)
            return self

        def __exit__(self, *args):
            cnt = self._cnt()
            if cnt is None:
                raise RuntimeError("Connection already released")
            pm = cnt._pending_lock.pop(self._msg, [])
            if pm:
                try:
                    pm.remove(self._queue)
                except ValueError:
                    pass
                cnt._pending_lock[self._msg] = pm

    class WaitingQueue(object):
        def __init__(self, cnt: Connection):
            self._cnt = weakref.ref(cnt)
            self._message_key = str(cnt._message_key).encode()
            cnt._message_key += 1
            self._queue = queue.Queue()

        def message_key(self):
            return self._message_key

        def get(self):
            return self._queue.get()

        def queue(self):
            return self._queue

        def __enter__(self):
            cnt = self._cnt()
            if cnt is None:
                raise RuntimeError("Connection already released")
            cnt._message_queue[self._message_key] = self._queue
            return self

        def __exit__(self, *args):
            cnt = self._cnt()
            if cnt is None:
                raise RuntimeError("Connection already released")
            cnt._message_queue.pop(self._message_key, None)

    def __init__(self, host=None, port=None):
        host = host or os.environ.get("BEACON_HOST") or "localhost"
        try:
            host, port = host.split(":")
        except ValueError:
            pass

        port = port or os.environ.get("BEACON_PORT")
        try:
            port = int(port)
        except (ValueError, TypeError) as e:
            if isinstance(e, TypeError) or not os.access(port, os.R_OK):
                raise RuntimeError(
                    "Beacon port is missing or invalid, please provide it with $BEACON_HOST=<host:port> or as CLI argument."
                )

        # Beacon connection
        self._host = host
        self._port = port
        # self._port_number is here to keep trace of port number
        # as self._port can be replaced by unix socket name.
        self._port_number = port
        self._socket = None
        self._connect_lock = lock.Semaphore()
        self._connected = event.Event()
        self._send_lock = lock.Semaphore()
        self._uds_query_event = event.Event()
        self._redis_query_event = event.Event()
        self._message_key = 0
        self._message_queue = {}
        self._clean_beacon_cache()
        self._raw_read_task = None
        self._redis_connection_manager = None
        self._redis_connection_manager_create_lock = lock.Semaphore()
        self._log_server_host: str | None = None
        self._log_server_port: str | None = None
        self._redis_data_address: RedisAddress | None = None

        # Beacon locks
        self._pending_lock = {}

        # Count how many time an object has been locked in the
        # current process per greenlet:
        self._lock_counters: weakref.WeakKeyDictionary[
            gevent.Greenlet, dict[str, int]
        ] = weakref.WeakKeyDictionary()

    def get_address(self) -> str:
        return f"{self._host}:{self._port_number}"

    def close(self, timeout=None):
        """Disconnection from Beacon and Redis"""
        if self._raw_read_task is not None:
            self._raw_read_task.kill(timeout=timeout)
            self._raw_read_task = None

    @property
    def uds(self):
        """
        False: UDS not supported by this platform
        None: Port not defined
        str: Port number
        """
        if sys.platform in ["win32", "cygwin"]:
            return False
        else:
            try:
                int(self._port)
            except ValueError:
                return self._port
            else:
                return None

    def connect(self):
        """Find the Beacon server (if not already known) and make the
        TCP or UDS connection.
        """
        with self._connect_lock:
            if self._connected.is_set():
                return

            # UDS connection
            if self.uds:
                self._socket = self._uds_connect(self.uds)
            # TCP connection
            else:
                self._socket = self._tcp_connect(self._host, self._port)

            # Spawn read task
            self._raw_read_task = gevent.spawn(self._raw_read)
            self._raw_read_task.name = "BeaconListenTask"

            # Run the UDS query
            if self.uds is None:
                self._uds_query()

            self.on_connected()

            self._connected.set()

    def on_connected(self):
        """Executed whenever a new connection is made"""
        self._set_get_clientname(name=self.CLIENT_NAME, timeout=3)

    def _tcp_connect(self, host, port):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

        if platform.system() != "Windows":
            sock.setsockopt(socket.SOL_IP, socket.IP_TOS, 0x10)

        try:
            sock.connect((host, port))
        except IOError:
            raise RuntimeError(
                "Conductor server on host `{}:{}' does not reply (check beacon server)".format(
                    host, port
                )
            )
        return sock

    def _uds_connect(self, uds_path):
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.connect(uds_path)
        return sock

    def _uds_query(self, timeout=3.0):
        self._uds_query_event.clear()
        self._sendall(
            protocol.message(protocol.UDS_QUERY, socket.gethostname().encode())
        )
        self._uds_query_event.wait(timeout)

    @check_connect
    def lock(self, *devices_name: str, priority=50, timeout=10):
        """Lock a set of device names.

        The locks (device name) can be anything, but we use to use the
        name of existing BLISS devices.

        Argument:
            devices_name: List of device name to lock
            priority: Priority to check if the lock can be stolen
                      (the biggest priority takes the lock)
            timeout: Time to wait in case the lock is not available
        """
        if not devices_name:
            return
        with self.WaitingLock(self, priority, devices_name) as wait_lock:
            with gevent.Timeout(
                timeout, RuntimeError("lock timeout (%s)" % str(devices_name))
            ):
                while True:
                    self._sendall(protocol.message(protocol.LOCK, wait_lock.msg()))
                    status = wait_lock.get()
                    if status == protocol.LOCK_OK_REPLY:
                        break
        self._increment_lock_counters(devices_name)

    @check_connect
    def unlock(self, *devices_name: str, priority=50, timeout=10):
        """Unlock a set of device names.

        The locks (device name) can be anything, but we use to use the
        name of existing BLISS devices.

        Argument:
            devices_name: List of device name to unlock
            priority: Priority to check if the lock can be stolen
                      (the biggest priority takes the lock)
            timeout: Time to wait in case the lock is not available
        """
        if not devices_name:
            return
        raw_names = [name.encode() for name in devices_name]
        msg = b"%d|%s" % (priority, b"|".join(raw_names))
        with gevent.Timeout(
            timeout, RuntimeError("unlock timeout (%s)" % str(devices_name))
        ):
            self._sendall(protocol.message(protocol.UNLOCK, msg))
        self._decrement_lock_counters(devices_name)

    def _increment_lock_counters(self, devices_name: Sequence[str]):
        """Keep track of locking per greenlet"""
        locked_objects = self._lock_counters.setdefault(gevent.getcurrent(), dict())
        for device in devices_name:
            nb_lock = locked_objects.get(device, 0)
            locked_objects[device] = nb_lock + 1

    def _decrement_lock_counters(self, devices_name: Sequence[str]):
        """Keep track of locking per greenlet"""
        locked_objects = self._lock_counters.setdefault(gevent.getcurrent(), dict())
        max_lock = 0
        for device in devices_name:
            nb_lock = locked_objects.get(device, 0)
            nb_lock -= 1
            if nb_lock > max_lock:
                max_lock = nb_lock
            locked_objects[device] = nb_lock
        if max_lock <= 0:
            self._lock_counters.pop(gevent.getcurrent(), None)

    @check_connect
    def get_redis_connection_address(self, timeout=3.0):
        """Get the Redis host and port from Beacon. Cached for the duration
        of the Beacon connection.
        """
        if self._redis_settings_address is None:
            with gevent.Timeout(
                timeout, RuntimeError("Can't get redis connection information")
            ):
                while self._redis_settings_address is None:
                    self._redis_query_event.clear()
                    self._sendall(protocol.message(protocol.REDIS_QUERY))
                    self._redis_query_event.wait()

        return self._redis_settings_address

    def get_redis_proxy(self, db: int = 0, caching: bool = False, shared: bool = True):
        """Get a greenlet-safe proxy to a Redis database.

        :param int db: Redis database too which we need a proxy
        :param bool caching: client-side caching
        :param bool shared: use a shared proxy held by the Beacon connection
        """
        return self.redis_connection_manager.get_db_proxy(
            db=db, caching=caching, shared=shared
        )

    def close_all_redis_connections(self):
        if self._redis_connection_manager is not None:
            self._redis_connection_manager.close_all_connections()

    @property
    def redis_connection_manager(self):
        with self._redis_connection_manager_create_lock:
            if self._redis_connection_manager is None:
                addresses = {
                    0: self.get_redis_connection_address(),
                    1: self.get_redis_data_server_connection_address(),
                }
                self._redis_connection_manager = RedisConnectionManager(addresses)
            return self._redis_connection_manager

    @check_connect
    def get_config_file(self, file_path, timeout=3.0):
        with gevent.Timeout(timeout, RuntimeError("Can't get configuration file")):
            with self.WaitingQueue(self) as wq:
                msg = b"%s|%s" % (wq.message_key(), file_path.encode())
                self._sendall(protocol.message(protocol.CONFIG_GET_FILE, msg))
                # self._socket.sendall(protocol.message(protocol.CONFIG_GET_FILE, msg))
                value = wq.get()
                if isinstance(value, RuntimeError):
                    raise value
                else:
                    return value

    @check_connect
    def get_config_db_tree(self, base_path="", timeout=3.0):
        with gevent.Timeout(timeout, RuntimeError("Can't get configuration tree")):
            with self.WaitingQueue(self) as wq:
                msg = b"%s|%s" % (wq.message_key(), base_path.encode())
                self._sendall(protocol.message(protocol.CONFIG_GET_DB_TREE, msg))
                value = wq.get()
                if isinstance(value, RuntimeError):
                    raise value
                else:
                    import json

                    return json.loads(value)

    @check_connect
    def remove_config_file(self, file_path, timeout=3.0):
        with gevent.Timeout(timeout, RuntimeError("Can't remove configuration file")):
            with self.WaitingQueue(self) as wq:
                msg = b"%s|%s" % (wq.message_key(), file_path.encode())
                self._sendall(protocol.message(protocol.CONFIG_REMOVE_FILE, msg))
                for rx_msg in wq.queue():
                    print(rx_msg)

    @check_connect
    def move_config_path(self, src_path, dst_path, timeout=3.0):
        with gevent.Timeout(timeout, RuntimeError("Can't move configuration file")):
            with self.WaitingQueue(self) as wq:
                msg = b"%s|%s|%s" % (
                    wq.message_key(),
                    src_path.encode(),
                    dst_path.encode(),
                )
                self._sendall(protocol.message(protocol.CONFIG_MOVE_PATH, msg))
                for rx_msg in wq.queue():
                    print(rx_msg)

    @check_connect
    def get_config_db(self, base_path="", timeout=30.0):
        return_files = []
        with gevent.Timeout(timeout, RuntimeError("Can't get configuration file")):
            with self.WaitingQueue(self) as wq:
                msg = b"%s|%s" % (wq.message_key(), base_path.encode())
                self._sendall(protocol.message(protocol.CONFIG_GET_DB_FILES, msg))
                for rx_msg in wq.queue():
                    if isinstance(rx_msg, RuntimeError):
                        raise rx_msg
                    file_path, file_value = self._get_msg_key(rx_msg)
                    if file_path is None:
                        continue
                    return_files.append((file_path.decode(), file_value.decode()))
        return return_files

    @check_connect
    def get_config_db_path(self, timeout=3.0):
        with gevent.Timeout(timeout, RuntimeError("Cannot get config path")):
            with self.WaitingQueue(self) as wq:
                msg = b"%s|" % wq.message_key()
                self._sendall(protocol.message(protocol.CONFIG_GET_DB_BASE_PATH, msg))
                for rx_msg in wq.queue():
                    if isinstance(rx_msg, RuntimeError):
                        raise rx_msg
                    retval, _ = self._get_msg_key(rx_msg)
                    return retval.decode()

    @check_connect
    def set_config_db_file(self, file_path, content, timeout=3.0):
        with gevent.Timeout(timeout, RuntimeError("Can't set config file")):
            with self.WaitingQueue(self) as wq:
                msg = b"%s|%s|%s" % (
                    wq.message_key(),
                    file_path.encode(),
                    content.encode(),
                )
                self._sendall(protocol.message(protocol.CONFIG_SET_DB_FILE, msg))
                for rx_msg in wq.queue():
                    raise rx_msg

    @check_connect
    def get_python_modules(self, base_path="", timeout=3.0):
        return_module = []
        with gevent.Timeout(timeout, RuntimeError("Can't get python modules")):
            with self.WaitingQueue(self) as wq:
                msg = b"%s|%s" % (wq.message_key(), base_path.encode())
                self._sendall(protocol.message(protocol.CONFIG_GET_PYTHON_MODULE, msg))
                for rx_msg in wq.queue():
                    if isinstance(rx_msg, RuntimeError):
                        raise rx_msg
                    module_name, full_path = self._get_msg_key(rx_msg)
                    return_module.append((module_name.decode(), full_path.decode()))
        return return_module

    @check_connect
    def get_log_server_address(self, timeout=3.0):
        """Get the log host and port from Beacon. Cached for the duration
        of the Beacon connection.
        """
        if self._log_server_host is None:
            with gevent.Timeout(
                timeout, RuntimeError("Can't retrieve log server port")
            ):
                with self.WaitingQueue(self) as wq:
                    msg = b"%s|" % wq.message_key()
                    self._socket.sendall(
                        protocol.message(protocol.LOG_SERVER_ADDRESS_QUERY, msg)
                    )
                    for rx_msg in wq.queue():
                        if isinstance(rx_msg, RuntimeError):
                            raise rx_msg
                        host, port = self._get_msg_key(rx_msg)
                        self._log_server_host = host.decode()
                        self._log_server_port = port.decode()
                        break
        return self._log_server_host, self._log_server_port

    @check_connect
    def get_redis_data_server_connection_address(self, timeout=3.0) -> RedisAddress:
        """Get the Redis data host and port from Beacon. Cached for the duration
        of the Beacon connection.
        """
        if self._redis_data_address is None:
            with gevent.Timeout(
                timeout, RuntimeError("Can't get redis data server information")
            ):
                with self.WaitingQueue(self) as wq:
                    msg = b"%s|" % wq.message_key()
                    self._socket.sendall(
                        protocol.message(protocol.REDIS_DATA_SERVER_QUERY, msg)
                    )
                    for rx_msg in wq.queue():
                        if isinstance(rx_msg, RuntimeError):
                            raise rx_msg
                        address = rx_msg.replace(b"|", b":").decode()
                        self._redis_data_address = RedisAddress.factory(address)
                        break
        return self._redis_data_address

    @check_connect
    def get_key(
        self, name: str, default=Undefined, timeout=3.0
    ) -> typing.Optional[str]:
        """Returns the value of a key from beacon, if exists

        Arguments:
            name: Name of the key to read
            default: The default value to return if the key is not defined
        Raises
            KeyError: If the key does not exist and no default value is defined
            gevent.Timeout: If the beacon request timeout
        """
        with gevent.Timeout(timeout, RuntimeError("Can't get beacon key")):
            with self.WaitingQueue(self) as wq:
                msg = b"%s|%s" % (wq.message_key(), name.encode())
                self._sendall(protocol.message(protocol.KEY_GET, msg))
                value = wq.get()
                if isinstance(value, RuntimeError):
                    raise value
                else:
                    if value is None:
                        if default is not Undefined:
                            return default
                        raise KeyError(f"Beacon key '{name}' is undefined")
                    return value.decode()

    @check_connect
    def set_key(self, name: str, value: str, timeout=3.0):
        with gevent.Timeout(timeout, RuntimeError("Can't set beacon key")):
            with self.WaitingQueue(self) as wq:
                msg = b"%s|%s|%s" % (wq.message_key(), name.encode(), value.encode())
                self._sendall(protocol.message(protocol.KEY_SET, msg))
                value = wq.get()
                if isinstance(value, RuntimeError):
                    raise value
                else:
                    return None

    @check_connect
    def set_client_name(self, name, timeout=3.0):
        self._set_get_clientname(name=name, timeout=timeout)

    @check_connect
    def get_client_name(self, timeout=3.0):
        return self._set_get_clientname(timeout=timeout)

    def who_locked(self, *names, timeout=3.0):
        name2client = dict()
        with gevent.Timeout(timeout, RuntimeError("Can't get who lock client name")):
            with self.WaitingQueue(self) as wq:
                raw_names = [b"%s" % wq.message_key()] + [n.encode() for n in names]
                msg = b"|".join(raw_names)
                self._sendall(protocol.message(protocol.WHO_LOCKED, msg))
                for rx_msg in wq.queue():
                    if isinstance(rx_msg, RuntimeError):
                        raise rx_msg
                    name, client_info = rx_msg.split(b"|")
                    name2client[name.decode()] = client_info.decode()
        return name2client

    def _set_get_clientname(self, name=None, timeout=3.0):
        """Give a name for this client to the Beacon server (optional)
        and return the name under which this client is know by Beacon.
        """
        if name:
            timeout_msg = "Can't set client name"
            msg_type = protocol.CLIENT_SET_NAME
            name = name.encode()
        else:
            timeout_msg = "Can't get client name"
            msg_type = protocol.CLIENT_GET_NAME
            name = b""
        with gevent.Timeout(timeout, RuntimeError(timeout_msg)):
            with self.WaitingQueue(self) as wq:
                msg = b"%s|%s" % (wq.message_key(), name)
                self._sendall(protocol.message(msg_type, msg))
                rx_msg = wq.get()
                if isinstance(rx_msg, RuntimeError):
                    raise rx_msg
                return rx_msg.decode()

    def _lock_mgt(self, fd, messageType, message):
        if messageType == protocol.LOCK_OK_REPLY:
            events = self._pending_lock.get(message, [])
            if not events:
                fd.sendall(protocol.message(protocol.UNLOCK, message))
            else:
                e = events.pop(0)
                e.put(messageType)
            return True
        elif messageType == protocol.LOCK_RETRY:
            for m, l in self._pending_lock.items():
                for e in l:
                    e.put(messageType)
            return True
        elif messageType == protocol.LOCK_STOLEN:
            stolen_object_lock = set(message.split(b"|"))
            greenlet_to_objects = self._lock_counters.copy()
            for greenlet, locked_objects in greenlet_to_objects.items():
                locked_object_name = set(
                    (name for name, nb_lock in locked_objects.items() if nb_lock > 0)
                )
                if locked_object_name.intersection(stolen_object_lock):
                    try:
                        greenlet.kill(exception=StolenLockException)
                    except AttributeError:
                        pass
            fd.sendall(protocol.message(protocol.LOCK_STOLEN_OK_REPLY, message))
            return True
        return False

    def _get_msg_key(self, message):
        pos = message.find(b"|")
        if pos < 0:
            return message, None
        return message[:pos], message[pos + 1 :]

    def _sendall(self, msg):
        with self._send_lock:
            self._socket.sendall(msg)

    def _raw_read(self):
        self.__raw_read()

    @protect_from_kill
    def __raw_read(self):
        """This listens to Beacon indefinitely (until killed or socket error).
        Closes Beacon and Redis connections when finished.
        """
        try:
            data = b""
            while True:
                with AllowKill():
                    raw_data = self._socket.recv(16 * 1024)
                if not raw_data:
                    break
                data = b"%s%s" % (data, raw_data)
                while data:
                    try:
                        messageType, message, data = protocol.unpack_message(data)
                    except protocol.IncompleteMessage:
                        break
                    try:
                        # print 'rx',messageType
                        if self._lock_mgt(self._socket, messageType, message):
                            continue
                        elif messageType in (
                            protocol.CONFIG_GET_FILE_OK,
                            protocol.CONFIG_GET_DB_TREE_OK,
                            protocol.CONFIG_GET_DB_BASE_PATH_OK,
                            protocol.CONFIG_DB_FILE_RX,
                            protocol.CONFIG_GET_PYTHON_MODULE_RX,
                            protocol.CLIENT_NAME_OK,
                            protocol.WHO_LOCKED_RX,
                            protocol.LOG_SERVER_ADDRESS_OK,
                            protocol.REDIS_DATA_SERVER_OK,
                            protocol.KEY_GET_OK,
                            protocol.KEY_SET_OK,
                            protocol.KEY_GET_UNDEFINED,
                        ):
                            message_key, value = self._get_msg_key(message)
                            queue = self._message_queue.get(message_key)
                            if queue is not None:
                                queue.put(value)
                        elif messageType in (
                            protocol.CONFIG_GET_FILE_FAILED,
                            protocol.CONFIG_DB_FAILED,
                            protocol.CONFIG_SET_DB_FILE_FAILED,
                            protocol.CONFIG_GET_DB_TREE_FAILED,
                            protocol.CONFIG_REMOVE_FILE_FAILED,
                            protocol.CONFIG_MOVE_PATH_FAILED,
                            protocol.CONFIG_GET_PYTHON_MODULE_FAILED,
                            protocol.WHO_LOCKED_FAILED,
                            protocol.LOG_SERVER_ADDRESS_FAIL,
                            protocol.REDIS_DATA_SERVER_FAILED,
                            protocol.KEY_GET_FAILED,
                            protocol.KEY_SET_FAILED,
                        ):
                            message_key, value = self._get_msg_key(message)
                            queue = self._message_queue.get(message_key)
                            if queue is not None:
                                queue.put(RuntimeError(value.decode()))
                        elif messageType in (
                            protocol.CONFIG_DB_END,
                            protocol.CONFIG_SET_DB_FILE_OK,
                            protocol.CONFIG_REMOVE_FILE_OK,
                            protocol.CONFIG_MOVE_PATH_OK,
                            protocol.CONFIG_GET_PYTHON_MODULE_END,
                            protocol.WHO_LOCKED_END,
                        ):
                            message_key, value = self._get_msg_key(message)
                            queue = self._message_queue.get(message_key)
                            if queue is not None:
                                queue.put(StopIteration)
                        elif messageType == protocol.REDIS_QUERY_ANSWER:
                            address = message.decode()
                            self._redis_settings_address = RedisAddress.factory(address)
                            self._redis_query_event.set()
                        elif messageType == protocol.UDS_OK:
                            try:
                                uds_path = message.decode()
                                sock = self._uds_connect(uds_path)
                            except socket.error:
                                raise
                            else:
                                self._socket.close()
                                self._socket = sock
                                self._port = uds_path
                            finally:
                                self._uds_query_event.set()
                        elif messageType == protocol.UDS_FAILED:
                            self._uds_query_event.set()
                        elif messageType == protocol.UNKNOW_MESSAGE:
                            message_key, value = self._get_msg_key(message)
                            queue = self._message_queue.get(message_key)
                            error = RuntimeError(
                                f"Beacon server ({self._host}) don't know this command ({value})"
                            )
                            if queue is not None:
                                queue.put(error)
                    except BaseException:
                        sys.excepthook(*sys.exc_info())
        except socket.error:
            pass
        except gevent.GreenletExit:
            pass
        except BaseException:
            sys.excepthook(*sys.exc_info())
        finally:
            with self._connect_lock:
                self._close_beacon_connection()
                self.close_all_redis_connections()

    def _close_beacon_connection(self):
        """Result of `close` of a socket error (perhaps closed)"""
        if self._socket:
            self._socket.close()
            self._socket = None
        self._connected.clear()
        self._clean_beacon_cache()

    def _clean_beacon_cache(self):
        """Clean all cached results from Beacon queries"""
        self._redis_settings_address = None
        self._redis_data_address = None
        self._log_server_host = None
        self._log_server_port = None

    @check_connect
    def __str__(self):
        return "Connection({0}:{1})".format(self._host, self._port)
