# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

"""
Proxy class to share a TCP connection.

ex: to share a socket connection to a device.
"""

import os
import sys

import errno
import gevent
from gevent import socket, select, event

from bliss.comm.tcp import Tcp
from bliss.config.conductor.client import Lock
from bliss.config.channels import Channel

global RICH
try:
    from rich.console import Console

    console = Console()
    RICH = True
except ModuleNotFoundError:
    RICH = False


def _wait_pid(read_pipe, pid):
    while True:
        r, _, _ = select.select([read_pipe], [], [])
        if r:
            out = os.read(read_pipe, 8192)
            if not out:
                os.waitpid(pid, 0)
                break


global debug_count
debug_count = 0


def print_debug(msg):
    """
    Print debug message.
    Use the same color for messages grouped 2 by 2. (one sent + one received)
    """
    global debug_count

    if RICH:
        if debug_count % 4 > 1:
            console.print(f"[color(153)]{msg}")
        else:
            console.print(f"[color(15)]{msg}")
    else:
        print(msg)

    debug_count += 1


def get_proxy_channel_name(host, port):
    return f"proxy:{host}:{port}"


class Proxy:
    """
    Class that can be used in place of a tcp connection.
    Client-side object.
    """

    TCP = 0

    def __init__(self, config):
        if "tcp" in config:
            tcp_config = config.get("tcp")
            self.external = config.get("external", False)
            if hasattr(config, "clone"):
                self._config = tcp_config.clone()
            else:
                self._config = tcp_config.copy()
            self.__eol = tcp_config.get("eol", "\n")
            self._mode = self.TCP
            cnx = Tcp(**tcp_config)
            self.name = "%s:%d" % (cnx._host, cnx._port)
        else:
            raise NotImplementedError("Proxy: Not managed yet")

        self._cnx = None
        self._join_task = None
        self._url_channel = Channel(get_proxy_channel_name(cnx._host, cnx._port))

    def kill_proxy_server(self):
        """
        Kill server (it should restart automatically).
        """
        self._url_channel.value = None

    def close(self):
        if not self.external:
            self.kill_proxy_server()
            if self._join_task is not None:
                self._join_task.join()
            self._join_task = None

    def __info__(self):
        url = self._url_channel.value
        info = f"TCP PROXY for {self.name}\n"
        if self.external:
            info += f"     External process on port {url}"
        else:
            info += f"     Forked process on port {url}"

        return info

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)

        was_connected = None
        if not name.startswith("_"):
            was_connected = self._check_connection()

        attr = getattr(self._cnx, name)
        if callable(attr) and was_connected is False:

            def wrapper_func(*args, **kwargs):
                try:
                    return attr(*args, **kwargs)
                except socket.error as sock_err:
                    if sock_err.errno == errno.EPIPE:
                        raise socket.error(errno.ECONNREFUSED, "Connection refused")
                else:
                    raise

            return wrapper_func
        return attr

    @property
    def _eol(self):
        return self.__eol

    def _check_connection(self):
        if self._mode == self.TCP:
            if self._cnx is None or not self._cnx._connected:
                if hasattr(self._config, "clone"):
                    local_cfg = self._config.clone()
                else:
                    local_cfg = self._config.copy()
                url = local_cfg.pop("url")
                cnx = Tcp(url, **local_cfg)
                host, port = cnx._host, cnx._port
                if not self.external:
                    proxy_url = self._fork_server(host, port)
                else:
                    proxy_url = self._connect_to_server(host, port)
                self._cnx = Tcp(proxy_url, **local_cfg)
                try:
                    self._cnx.connect()
                except ConnectionRefusedError as cnx_err:
                    raise ConnectionError(
                        f"Failed to connect to tcp_proxy server for {host}:{port}"
                    ) from cnx_err
                return False

    def _connect_to_server(self, host, port):
        local_url = self._url_channel.value
        if local_url is None:
            self._cnx = None
            raise ConnectionError(
                f"No tcp_proxy server running for {host}:{port}. Restart it !!"
            )

        return local_url

    def _fork_server(self, host, port):
        with Lock(self):
            sync = event.Event()

            def port_cbk(proxy_url):
                if not proxy_url:
                    # filter default value
                    return
                sync.set()

            try:
                self._url_channel.register_callback(port_cbk)
                local_url = self._url_channel.value
                if local_url is None:
                    self._join_task = self._real_server_fork(host, port)
                    gevent.sleep(0)
                    sync.wait()
                    local_url = self._url_channel.value
                return local_url
            finally:
                self._url_channel.unregister_callback(port_cbk)

    def _real_server_fork(self, host, port):
        script_name = __file__
        read, write = os.pipe()
        pid = os.fork()
        if pid == 0:  # child
            os.dup2(write, sys.stdout.fileno())
            os.dup2(write, sys.stderr.fileno())
            os.closerange(3, write + 1)
            os.execl(
                sys.executable,
                sys.executable,
                script_name,
                "--port",
                str(port),
                "--host",
                host,
            )
            sys.exit(0)
        else:
            os.close(write)
            wait_greenlet = gevent.spawn(_wait_pid, read, pid)
            wait_greenlet.start()
            return wait_greenlet


def get_host_port_from_beacon_name(name):
    from bliss.config.static import get_config

    beacon_config = get_config()
    config = beacon_config.get_config(name)

    if config is None:
        raise ValueError(f"Cannot find beacon object {name}")
    proxy_config = config.get("tcp-proxy")

    if proxy_config is None:
        raise ValueError(f"No tcp-proxy config for object {name}")
    tcp_config = proxy_config.get("tcp")

    if tcp_config is None:
        raise ValueError(f"Missing tcp section in tcp-proxy for object {name}")
    url = tcp_config.get("url")

    if url is None:
        raise ValueError(f"Missing url in config for object {name}")
    host, port = url.split(":")

    return host, int(port)


def main():  # proxy server part
    import signal

    global pipe_write
    pipe_read, pipe_write = os.pipe()

    def _stop_server(*args):
        # import os
        os.close(pipe_write)

    signal.signal(signal.SIGINT, _stop_server)
    signal.signal(signal.SIGTERM, _stop_server)

    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--host", dest="host", type=str, help="destination host")
    parser.add_argument("--port", dest="port", type=int, help="destination port")
    parser.add_argument(
        "--beacon-name", dest="beacon_name", type=str, help="beacon object name"
    )
    parser.add_argument(
        "-d",
        "--debug",
        dest="debug",
        action="store_true",
        help="activate debug of communications",
    )

    _options = parser.parse_args()

    if _options.debug:
        print("DEBUG ACTIVATED")

    if _options.beacon_name is not None:
        (host, port) = get_host_port_from_beacon_name(_options.beacon_name)
    else:
        if _options.host is not None and _options.port is not None:
            host = _options.host
            port = _options.port
        else:
            raise ValueError("Specficy options (--host AND --port) OR (--beacon-name)")

    tcp = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    tcp.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    tcp.bind(("", 0))
    tcp.listen(16)
    proxy_port = tcp.getsockname()[1]
    proxy_host = socket.gethostname()

    server_url = "%s:%d" % (proxy_host, proxy_port)

    global dont_reset_channel
    dont_reset_channel = False

    def channel_cbk(value):
        global dont_reset_channel
        if value != server_url:
            dont_reset_channel = True
            try:
                os.close(pipe_write)
            except OSError:
                pass

    channel_name = get_proxy_channel_name(host, port)
    channel = Channel(channel_name, value=server_url, callback=channel_cbk)

    runFlag = True
    fd_list = [tcp, pipe_read]
    global client
    global dest
    client = None
    dest = None
    try:
        while runFlag:
            rlist, _, _ = select.select(fd_list, [], [])
            for s in rlist:
                if s == tcp:
                    accept_flag = True
                    try:
                        if dest is None:
                            dest = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                            dest.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                            dest.connect((host, port))
                            fd_list.append(dest)
                    except Exception:
                        dest = None
                        accept_flag = False

                    if client is not None:
                        fd_list.remove(client)
                        client.close()
                        client = None

                    client, addr = tcp.accept()
                    if accept_flag:
                        client.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                        fd_list.append(client)
                    else:
                        client.close()
                        client = None

                elif s == client:
                    try:
                        raw_data = client.recv(16 * 1024)
                    except Exception:
                        raw_data = None
                    if raw_data:
                        if _options.debug:
                            print_debug(f"send:  {raw_data}")
                        dest.sendall(raw_data)
                    else:
                        fd_list.remove(client)
                        client.close()
                        client = None
                elif s == dest:
                    try:
                        raw_data = dest.recv(16 * 1024)
                    except Exception:
                        runFlag = False
                        raw_data = None

                    if raw_data:
                        if _options.debug:
                            print_debug(f" recv: {raw_data}")
                        client.sendall(raw_data)
                    else:
                        dest.close()
                        fd_list.remove(dest)
                        dest = None
                        fd_list.remove(client)
                        client.close()
                        client = None
                elif s == pipe_read:
                    runFlag = False
                    break
    finally:
        if dont_reset_channel is False:
            channel.value = None
            gevent.sleep(0)


if __name__ == "__main__":
    main()
