# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

from __future__ import annotations

import os
import sys
import logging
import argparse
import socket
import signal
import gevent
import subprocess
import flask
import redis
import time

from contextlib import contextmanager, ExitStack
import gevent.event
from gevent.pywsgi import WSGIServer

from .app_dispatcher import create_app

from bliss.beacon import redis as redis_conf
from bliss.config.conductor import client as client_utils
from bliss.config.conductor import connection as connection_utils

from blissdata.redis_engine.store import DataStore

from ..beacon_server import BeaconServer, RedisServerInfo

try:
    import win32api
except ImportError:
    IS_WINDOWS = False
else:
    IS_WINDOWS = True


BEACON: BeaconServer | None = None

beacon_logger = logging.getLogger("beacon")
tango_logger = beacon_logger.getChild("tango")
redis_logger = beacon_logger.getChild("redis")
redis_data_logger = beacon_logger.getChild("redis_data")
memory_tracker_logger = beacon_logger.getChild("memtracker")
web_logger = beacon_logger.getChild("web")
log_server_logger = beacon_logger.getChild("log_server")
log_viewer_logger = beacon_logger.getChild("log_viewer")


@contextmanager
def pipe():
    rp, wp = os.pipe()
    try:
        yield (rp, wp)
    finally:
        os.close(wp)
        os.close(rp)


def log_tangodb_started(options):
    """Raise exception when tango database not started in 10 seconds"""
    from bliss.tango.clients.utils import wait_tango_db

    assert BEACON is not None

    try:
        wait_tango_db(port=BEACON.options.tango_port, db=2)
    except Exception:
        tango_logger.error("Tango database NOT started")
        raise
    else:
        tango_logger.info("Tango database started")


def ensure_global_beacon_connection(beacon_port):
    """Avoid auto-discovery of port for the global connection object."""
    if client_utils._default_connection is None:
        client_utils._default_connection = connection_utils.Connection(
            "localhost", beacon_port
        )


def stream_to_log(stream, log_func):
    """Forward a stream to a log function"""
    gevent.get_hub().threadpool.maxsize += 1
    while True:
        msg = gevent.os.tp_read(stream, 8192)
        if msg:
            log_func(msg.decode())


@contextmanager
def logged_subprocess(args, logger, **kw):
    """Subprocess with stdout/stderr logging"""
    with pipe() as (rp_out, wp_out):
        with pipe() as (rp_err, wp_err):
            log_stdout = gevent.spawn(stream_to_log, rp_out, logger.info)
            log_stderr = gevent.spawn(stream_to_log, rp_err, logger.error)
            greenlets = [log_stdout, log_stderr]
            proc = subprocess.Popen(args, stdout=wp_out, stderr=wp_err, **kw)
            msg = f"(pid={proc.pid}) {repr(' '.join(args))}"
            beacon_logger.info(f"started {msg}")
            try:
                yield proc
            finally:
                beacon_logger.info(f"terminating {msg}")
                proc.terminate()
                gevent.killall(greenlets)
                beacon_logger.info(f"terminated {msg}")


@contextmanager
def spawn_context(func, *args, **kw):
    g = gevent.spawn(func, *args, **kw)
    try:
        yield
    finally:
        g.kill()


def wait():
    """Wait for exit signal"""

    with pipe() as (rp, wp):

        def sigterm_handler(*args, **kw):
            # This is executed in the hub so use a pipe
            # Find a better way:
            # https://github.com/gevent/gevent/issues/1683
            os.write(wp, b"!")

        event = gevent.event.Event()

        def sigterm_greenlet():
            # Graceful shutdown
            gevent.get_hub().threadpool.maxsize += 1
            gevent.os.tp_read(rp, 1)
            beacon_logger.info("Received a termination signal")
            event.set()

        with spawn_context(sigterm_greenlet):
            # Binds system signals.
            signal.signal(signal.SIGTERM, sigterm_handler)
            if IS_WINDOWS:
                signal.signal(signal.SIGINT, sigterm_handler)
                # ONLY FOR Win7 (COULD BE IGNORED ON Win10 WHERE CTRL-C PRODUCES A SIGINT)
                win32api.SetConsoleCtrlHandler(sigterm_handler, True)
            else:
                signal.signal(signal.SIGHUP, sigterm_handler)
                signal.signal(signal.SIGQUIT, sigterm_handler)

            try:
                event.wait()
            except KeyboardInterrupt:
                beacon_logger.info("Received a keyboard interrupt")
            except Exception as exc:
                sys.excepthook(*sys.exc_info())
                beacon_logger.critical("An unexpected exception occurred:\n%r", exc)


def configure_logging(options):
    """Configure the root logger:

    - set log level according to CLI arguments
    - send DEBUG and INFO to STDOUT
    - send WARNING, ERROR and CRITICAL to STDERR
    """
    log_fmt = "%(levelname)s %(asctime)-15s %(name)s: %(message)s"

    rootlogger = logging.getLogger()
    rootlogger.setLevel(options.log_level.upper())

    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(lambda record: record.levelno < logging.WARNING)
    handler.setFormatter(logging.Formatter(log_fmt))
    rootlogger.addHandler(handler)

    handler = logging.StreamHandler(sys.stderr)
    handler.addFilter(lambda record: record.levelno >= logging.WARNING)
    handler.setFormatter(logging.Formatter(log_fmt))
    rootlogger.addHandler(handler)


@contextmanager
def redis_server_context(
    logger,
    cwd,
    config_file,
    port,
    unixsocket=None,
    timeout=3,
    plugins=[],
):
    proc_args = ["redis-server", config_file, "--port", str(port)]
    if not IS_WINDOWS and unixsocket is not None:
        proc_args += [
            "--unixsocket",
            unixsocket,
            "--unixsocketperm",
            "777",
        ]
        redis_url = f"unix://{unixsocket}"
    else:
        redis_url = f"redis://{socket.gethostname()}:{port}"

    for plugin_path in plugins:
        if os.path.isfile(plugin_path):
            proc_args += ["--loadmodule", plugin_path]
        else:
            raise Exception(f"Redis server plugin not found: {plugin_path}")

    with logged_subprocess(proc_args, logger, cwd=cwd) as proc:
        for _ in range(int(10 * timeout)):
            try:
                red = redis.Redis.from_url(redis_url)
                redis_pid = red.info()["process_id"]
                if redis_pid != proc.pid:
                    raise Exception(
                        f"'{redis_url}' already used by another redis-server (PID:{redis_pid})"
                    )
                break
            except redis.exceptions.ConnectionError:
                time.sleep(0.1)
        else:
            raise Exception(
                f"Failed to start Redis server, '{redis_url}' not reachable after {timeout} seconds."
            )
        yield redis_url


def key_value(key: str) -> list[str]:
    """Extra key exposed by Beacon.

    Have to be in `KEY=VALUE` format
    """
    res = key.split("=", 1)
    if len(res) != 2:
        raise ValueError(f"'{key}' is not a valid KEY=VALUE sequence")
    return res


def abs_path(path: str):
    return os.path.abspath(os.path.expanduser(path))


def create_parser():
    """Create the argument parsing"""

    # Argument parsing
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--db-path",
        "--db_path",
        type=abs_path,
        dest="db_path",
        default=os.environ.get("BEACON_DB_PATH", "./db"),
        help="database path",
    )
    parser.add_argument(
        "--redis-address",
        "--redis_address",
        dest="redis_address",
        help="Use external Redis DB (settings), i.e. redis://host:port",
    )
    parser.add_argument(
        "--redis-port",
        "--redis_port",
        dest="redis_port",
        default=6379,
        type=int,
        help="redis connection port",
    )
    parser.add_argument(
        "--redis-conf",
        "--redis_conf",
        dest="redis_conf",
        default=redis_conf.get_redis_config_path(),
        help="path to alternative redis configuration file",
    )
    parser.add_argument(
        "--redis-data-address",
        "--redis_data_address",
        dest="redis_data_address",
        help="Use external Redis DB (data), i.e. redis://host:port",
    )
    parser.add_argument(
        "--redis-data-port",
        "--redis_data_port",
        dest="redis_data_port",
        default=6380,
        type=int,
        help="redis data connection port",
    )
    parser.add_argument(
        "--redis-data-conf",
        "--redis_data_conf",
        dest="redis_data_conf",
        default=redis_conf.get_redis_data_config_path(),
        help="path to alternative redis configuration file for data server",
    )
    parser.add_argument(
        "--redis-data-socket",
        dest="redis_data_socket",
        default="/tmp/redis_data.sock",
        help="Unix socket for redis (default to /tmp/redis_data.sock)",
    )
    parser.add_argument(
        "--port",
        dest="port",
        type=int,
        default=int(os.environ.get("BEACON_PORT", 25000)),
        help="server port (default to BEACON_PORT environment variable, "
        "otherwise 25000)",
    )
    parser.add_argument(
        "--tango-port",
        "--tango_port",
        dest="tango_port",
        type=int,
        default=0,
        help="tango server port (default to 0: disable)",
    )
    parser.add_argument(
        "--tango-debug-level",
        "--tango_debug_level",
        dest="tango_debug_level",
        type=int,
        default=0,
        help="tango debug level (default to 0: WARNING,1:INFO,2:DEBUG)",
    )
    parser.add_argument(
        "--webapp-port",
        "--webapp_port",
        dest="webapp_port",
        type=int,
        default=9030,
        help="DEPRECATED: Configuration page no more uses a dedicated port. "
        "Use http://<hostname>:<homepage_port>/config instead.",
    )
    parser.add_argument(
        "--homepage-port",
        "--homepage_port",
        dest="homepage_port",
        type=int,
        default=9010,
        help="web port for the homepage (0: disable)",
    )
    parser.add_argument(
        "--log-server-port",
        "--log_server_port",
        dest="log_server_port",
        type=int,
        default=9020,
        help="logger server port (0: disable)",
    )
    parser.add_argument(
        "--log-output-folder",
        "--log_output_folder",
        dest="log_output_folder",
        type=str,
        default="/var/log/bliss",
        help="logger output folder (default is /var/log/bliss)",
    )
    parser.add_argument(
        "--log-size",
        "--log_size",
        dest="log_size",
        type=float,
        default=10,
        help="Size of log rotating file in MegaBytes (default is 10)",
    )
    parser.add_argument(
        "--log-viewer-port",
        "--log_viewer_port",
        dest="log_viewer_port",
        type=int,
        default=9080,
        help="Web port for the log viewer socket (0: disable)",
    )
    parser.add_argument(
        "--redis-socket",
        "--redis_socket",
        dest="redis_socket",
        default="/tmp/redis.sock",
        help="Unix socket for redis (default to /tmp/redis.sock)",
    )
    parser.add_argument(
        "--log-level",
        "--log_level",
        default="INFO",
        type=str,
        choices=["DEBUG", "INFO", "WARN", "ERROR"],
        help="log level",
    )
    parser.add_argument(
        "--key",
        metavar="KEY=VALUE",
        dest="keys",
        default=[],
        type=key_value,
        action="append",
        help="Exported key from the Beacon service",
    )
    return parser


def main(args=None):
    # Monkey patch needed for web server
    # just keep for consistency because it's already patched
    # in __init__ in bliss project
    from gevent import monkey

    monkey.patch_all(thread=False)

    global BEACON
    parser = create_parser()
    options = parser.parse_args(args)

    BEACON = beacon = BeaconServer(options)

    if options.redis_address:
        host, port = options.redis_address.split("redis://")[-1].split(":")
        beacon.redis_info = RedisServerInfo(host, int(port), external=True)
    else:
        beacon.redis_info = RedisServerInfo(
            socket.gethostname(),
            int(options.redis_port),
            external=False,
            uds_socket=options.redis_socket,
        )

    if options.redis_data_address:
        host, port = options.redis_data_address.split("redis://")[-1].split(":")
        beacon.redis_data_info = RedisServerInfo(host, int(port), external=True)
    else:
        beacon.redis_data_info = RedisServerInfo(
            socket.gethostname(),
            int(options.redis_data_port),
            external=False,
            uds_socket=options.redis_data_socket,
        )

    # Feed the key-value database
    for k in options.keys:
        beacon.local_key_storage[k[0]] = k[1]

    # Logging configuration
    configure_logging(options)

    with ExitStack() as context_stack:
        # For sub-processes
        env = dict(os.environ)

        # Start the Beacon server
        ctx = beacon.start_tcp_server()
        tcp_socket = context_stack.enter_context(ctx)
        ctx = beacon.start_uds_server()
        uds_socket = context_stack.enter_context(ctx)
        beacon_port = tcp_socket.getsockname()[1]
        env["BEACON_HOST"] = f"localhost:{beacon_port:d}"

        # Logger server application
        if options.log_server_port > 0:
            # Logserver executable
            args = [sys.executable]
            args += ["-m", "bliss.beacon.services.log_server"]

            # Arguments
            args += ["--port", str(options.log_server_port)]
            if not options.log_output_folder:
                log_folder = os.path.join(str(options.db_path), "logs")
            else:
                log_folder = str(options.log_output_folder)

            # Start log server when the log folder is writeable
            if os.access(log_folder, os.R_OK | os.W_OK | os.X_OK):
                args += ["--log-output-folder", log_folder]
                args += ["--log-size", str(options.log_size)]
                beacon_logger.info(
                    "launching log_server on port: %s", options.log_server_port
                )
                ctx = logged_subprocess(args, log_server_logger, env=env)
                context_stack.enter_context(ctx)

                # Logviewer Web application
                if not IS_WINDOWS and options.log_viewer_port > 0:
                    args = ["tailon"]
                    args += ["-b", f"0.0.0.0:{options.log_viewer_port}"]
                    args += [os.path.join(options.log_output_folder, "*")]
                    ctx = logged_subprocess(args, log_viewer_logger, env=env)
                    context_stack.enter_context(ctx)
            else:
                raise RuntimeError(
                    f"Log path {log_folder} does not exist."
                    " Please create it or specify another one with --log-output-folder option"
                )

        # determine RediSearch and RedisJSON library paths
        librejson = os.path.join(env.get("CONDA_PREFIX", "/usr"), "lib", "librejson.so")
        redisearch = os.path.join(
            env.get("CONDA_PREFIX", "/usr"), "lib", "redisearch.so"
        )

        if not beacon.redis_info.external:
            ctx = redis_server_context(
                redis_logger,
                options.db_path,
                options.redis_conf,
                beacon.redis_info.port,
                beacon.redis_info.uds_socket,
                plugins=[librejson, redisearch],
            )
            context_stack.enter_context(ctx)

        if not beacon.redis_data_info.external:
            ctx = redis_server_context(
                redis_data_logger,
                options.db_path,
                options.redis_data_conf,
                beacon.redis_data_info.port,
                beacon.redis_data_info.uds_socket,
                plugins=[librejson, redisearch],
            )
            redis_data_url = context_stack.enter_context(ctx)

        else:
            redis_data_url = (
                f"redis://{beacon.redis_data_info.host}:{beacon.redis_data_info.port}"
            )

        try:
            # Apply blissdata setup on the fresh Redis database
            _ = DataStore(redis_data_url, init_db=True)
        except Exception as exc:
            if beacon.redis_data_info.external:
                beacon_logger.warning("Cannot initialize the Redis database:\n%r", exc)
            else:
                raise

        redis_data_protected_history = 180
        redis_data_tracker = [
            "memory_tracker",
            "--redis-url",
            redis_data_url,
            "--protected-history",
            str(redis_data_protected_history),
        ]
        ctx = logged_subprocess(redis_data_tracker, memory_tracker_logger)
        context_stack.enter_context(ctx)

        # Start Tango database
        if options.tango_port > 0:
            # Tango database executable
            args = ["NosqlTangoDB"]

            # Arguments
            args += ["-l", str(options.tango_debug_level)]
            args += ["--db_access", "beacon"]
            args += ["--port", str(options.tango_port)]
            args += ["2"]

            # Start tango database
            ctx = logged_subprocess(args, tango_logger, env=env)
            context_stack.enter_context(ctx)
            ctx = spawn_context(log_tangodb_started)
            context_stack.enter_context(ctx)

        # Start processing Beacon requests
        if uds_socket is not None:
            ctx = spawn_context(beacon.uds_server_main, uds_socket)
            context_stack.enter_context(ctx)
        if tcp_socket is not None:
            ctx = spawn_context(beacon.tcp_server_main, tcp_socket)
            context_stack.enter_context(ctx)

        ensure_global_beacon_connection(beacon_port)

        # run the web server for homepage, config and RestAPI apps
        app = create_app(options.log_viewer_port)
        http_server = WSGIServer(("0.0.0.0", options.homepage_port), app)
        ctx = spawn_context(http_server.serve_forever)
        context_stack.enter_context(ctx)
        web_logger.info(
            f"Web server ready at {socket.gethostname()}:{options.homepage_port}"
        )

        # Still create a server to listen on deprecated webapp_port to inform users with new URL.
        # This could be completely removed after some time
        redirect_app = flask.Flask(__name__)

        @redirect_app.route("/", defaults={"path": ""})
        @redirect_app.route("/<path:path>")
        def catch_all(path):
            server = flask.request.server
            assert server
            url = f"http://{server[0]}:{options.homepage_port}/config"
            response = f"<h1 style='text-align: center;''>{'<p>&nbsp;</p>'*3}<strong>Configuration page has moved...</strong></h1>"
            response += f"<h2 style='text-align: center;''>Please use <a href='{url}''>{url}</a> instead.</h2>"
            response += "<p style='text-align: center;''><em>(port number can be omitted on ESRF beamlines)</em></p>"
            return response

        redirect_server = WSGIServer(("0.0.0.0", options.webapp_port), redirect_app)
        ctx = spawn_context(redirect_server.serve_forever)
        context_stack.enter_context(ctx)

        # Wait for exit signal
        wait()


if __name__ == "__main__":
    main()
