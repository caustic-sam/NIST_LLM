# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

from __future__ import annotations

import os
import socket
import gevent
import uuid

from flask_openapi3 import OpenAPI, Info
from flask_socketio import SocketIO
from pydantic import BaseModel

from bliss.common.session import Session as BlissSession
from bliss.config.conductor import client
from bliss.shell import log_utils

# from .endpoints.console import Console
from .endpoints.call import CallApi
from .endpoints.object import ObjectApi
from .endpoints.object_type import ObjectTypeApi
from .endpoints.beacon import BeaconApi
from .endpoints.info import InfoApi
from .endpoints.utils import nocache

from .core.async_tasks import AsyncTasks
from .core.object_store import ObjectStore
from .core.object_events import ObjectEvents
from .core.object_factory import ObjectFactory


class ConfigSchema(BaseModel):
    """
    Description of the configuration which can be setup in the yaml session.
    """

    port: str | int = "auto"
    """Port number, or 'auto' (the default)"""

    cors: bool = False
    """Whether to enable the cross-origin resource sharing (CORS)"""

    iosecret: str | None = None
    """SocketIO secret, should be unique"""

    debug: bool = False
    """Enable flask reloader / debugging"""


class RestService:
    """Bliss Rest service served by BLISS.

    Actually the service can be setup from the BLISS session yml.

    If the session is not specified, the service is created only
    for apispec generation.

    .. code-block:: yaml

        - class: Session
          name: demo_session
          rest:
              port: auto
              cors: true
              iosecret: 'foobar2000'
              debug: false
    """

    def __init__(self, session: BlissSession | None):
        self.__session = session
        if self.__session is not None:
            self._config = self.__session.local_config.get("rest", {})
            self.__beacon = client.get_default_connection()
        else:
            self._config = {}
            self.__beacon = None
        self.__greenlet: gevent.Greenlet = None
        self.__app: OpenAPI | None = None
        # self.console_api: Console | None = None
        self._call_api: CallApi | None = None
        self._object_api: ObjectApi | None = None
        self._object_type_api: ObjectTypeApi | None = None
        self._async_tasks: AsyncTasks | None = None
        self._object_store: ObjectStore | None = None
        self._object_events: ObjectEvents | None = None
        self._object_factory: ObjectFactory | None = None
        self._beacon_api: BeaconApi | None = None
        self._info_api: InfoApi | None = None

    @property
    def object_store(self) -> ObjectStore:
        assert self._object_store is not None
        return self._object_store

    @property
    def object_factory(self) -> ObjectFactory:
        assert self._object_factory is not None
        return self._object_factory

    @property
    def async_tasks(self) -> AsyncTasks:
        assert self._async_tasks is not None
        return self._async_tasks

    @property
    def config(self):
        return self._config

    @property
    def app(self) -> OpenAPI:
        assert self.__app is not None
        return self.__app

    @property
    def state(self):
        return "RUNNING" if self.__greenlet is not None else "STOPPED"

    def start(self):
        assert self.__session is not None
        assert self.__beacon is not None
        if self.__greenlet is not None:
            raise RuntimeError("Service already running")
        app, socketio = self._create_server()
        self.__app = app

        port = self.config.get("port", "auto")
        if port == "auto":
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.bind(("localhost", 0))
            port = sock.getsockname()[1]
            sock.close()
        self._port = port

        address = f"http://{socket.gethostname()}:{port}/api"
        self._address = address
        print(f"** Running Bliss Rest Server on {address} **")

        def run():
            with log_utils.filter_warnings():
                socketio.run(app, host="0.0.0.0", port=port, debug=False)

        self.__greenlet = gevent.spawn(run)
        self.__beacon.set_key(f"BLISS_REST_{self.__session.name}", address)

    def stop(self):
        assert self.__session is not None
        assert self.__beacon is not None
        if self.__greenlet is None:
            raise RuntimeError("Service not running")
        self.__greenlet.kill()
        self.__greenlet = None
        self.__beacon.set_key(f"BLISS_REST_{self.__session.name}", "")
        self.__app = None
        self._socketio = None

        # if self.console_api:
        #     self.console_api.disconnect()
        #     self.console_api = None
        if self._call_api is not None:
            self._call_api.disconnect()
            self._call_api = None
        if self._async_tasks is not None:
            self._async_tasks.disconnect()
            self._async_tasks = None
        if self._object_api is not None:
            self._object_api.disconnect()
            self._object_api = None
        if self._object_type_api is not None:
            self._object_type_api.disconnect()
            self._object_type_api = None
        if self._object_events is not None:
            self._object_events.disconnect()
            self._object_events = None
        if self._object_store is not None:
            self._object_store.disconnect()
            self._object_store = None
        if self._object_factory is not None:
            self._object_factory.disconnect()
            self._object_factory = None
        if self._beacon_api is not None:
            self._beacon_api.disconnect()
            self._beacon_api = None
        if self._info_api is not None:
            self._info_api.disconnect()
            self._info_api = None

    def _create_server(self):
        info = Info(
            title="BlissAPI",
            version="1.0.0",
            description="A REST/WebSocket API for BLISS",
        )

        static_folder = self.static_folder()
        app = OpenAPI(
            __name__, info=info, static_url_path="", static_folder=static_folder
        )
        if static_folder:
            self.serve_static(app)

        parsed_config = ConfigSchema(**self.config)

        cors_allowed_origins: str | list = []
        if parsed_config.cors:
            print("restservice: CORS Enabled")
            from flask_cors import CORS

            cors_allowed_origins = "*"
            CORS(app)

        if not parsed_config.iosecret:
            secret = str(uuid.uuid4())
        else:
            secret = parsed_config.iosecret

        app.config["SECRET_KEY"] = secret
        app.config["REST_SERVICE"] = self
        socketio = SocketIO(app, cors_allowed_origins=cors_allowed_origins)

        self._socketio = socketio
        self._async_tasks = AsyncTasks()
        self._object_factory = ObjectFactory()
        self._object_store = ObjectStore(self._object_factory)
        self._object_events = ObjectEvents(self._object_store, socketio)

        self._beacon_api = BeaconApi(app=app, socketio=None)
        self._info_api = InfoApi(app=app, socketio=None)
        # self.console_api = Console(app=app, socketio=socketio)
        self._call_api = CallApi(app=app, socketio=socketio)
        self._object_type_api = ObjectTypeApi(app=app, socketio=socketio)
        self._object_api = ObjectApi(app=app, socketio=socketio)

        return app, socketio

    def static_folder(self):
        try:
            import blisswebui

            static_folder = os.path.join(os.path.dirname(blisswebui.__file__), "static")
            return static_folder
        except ModuleNotFoundError:
            print(
                "restservice: Bliss Web UI not available. To use the UI please install with: `pip install blisswebui`"
            )

    def serve_static(self, app):
        @app.route("/manifest.json")
        def manifest():
            return app.send_static_file("manifest.json")

        @app.route("/favicon.ico")
        def favicon():
            return app.send_static_file("favicon.ico")

        @app.route("/", defaults={"path": ""})
        @app.route("/<string:path>")
        @app.route("/<path:path>")
        @app.route("/layout/<string:path>")
        @nocache
        def index(path):
            return app.send_static_file("index.html")
