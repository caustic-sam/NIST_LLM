#!/usr/bin/env python
# -*- coding: utf-8 -*-
from __future__ import annotations
from abc import ABC, abstractmethod
from functools import partial
import logging
import typing
from collections.abc import Callable
from typing import Any, Generic, TypeVar

from flask_openapi3 import OpenAPI, APIView, Tag
from flask_socketio import SocketIO
from flask import current_app

if typing.TYPE_CHECKING:
    from .rest_service import RestService

logger = logging.getLogger(__name__)


def doc(**kwargs):
    """Doc Decorator

    Wrappers `api_view.doc()`
    """

    def wrap(f: callable):
        f.__api_doc__ = kwargs
        return f

    return wrap


class CoreBase(ABC):
    """CoreBase is the class that all application modules inherit from

    Kwargs"""

    _base_url = None
    _namespace = None

    def __init__(self, *, app: OpenAPI, socketio: SocketIO):
        self.app = app
        self.socketio = socketio

        class_name = self.__class__.__name__
        if self._base_url is None:
            self._base_url = class_name.lower()
            logger.debug(
                f"`base_url` is empty, defaulting to class name: {self._base_url}"
            )

        self._api_view = APIView(
            url_prefix="/api/" + self._base_url,
            view_tags=[Tag(name=class_name)],
        )

        self.setup()
        app.register_api_view(self._api_view, view_kwargs={"parent": self})

        if self._namespace is None:
            logger.debug(
                f"namespace is empty, defaulting to base_url: {self._base_url}"
            )
            self._namespace = self._base_url

        if socketio is not None:
            self.on("connect", namespace=f"/{self._namespace}")(
                partial(self._on_socket_connect, self._namespace)
            )
            self.on("disconnect", namespace=f"/{self._namespace}")(
                partial(self._on_socket_disconnect, self._namespace)
            )

    @property
    def rest_service(self) -> RestService:
        return self.app.config["REST_SERVICE"]

    def _on_socket_connect(self, namespace: str):
        logger.info(f"SocketIO connect: `{namespace}`")

    def _on_socket_disconnect(self, namespace: str):
        logger.info(f"SocketIO disconnect: `{namespace}`")

    def register_route(self, route_class: object, url: str) -> None:
        """Register a flask route with a route class

        Args:
            route_class (class): The route class
            url (str): The url to map this class to
        """
        for k in ["post", "get", "put", "patch", "delete"]:
            fn = getattr(route_class, k, None)
            if fn and hasattr(fn, "__api_doc__"):
                fn = self._api_view.doc(**fn.__api_doc__)(fn)

        self._api_view.route(url)(route_class)

    def emit(self, key: str, *args, namespace: str | None = None, **kwargs) -> None:
        """Convenience method to emit a socketio event.

        If the emit fails, a log is emitted but the method do not raise any
        exception.
        """
        if not namespace:
            namespace = f"/{self._namespace}"
        try:
            self.socketio.emit(key, *args, namespace=namespace, **kwargs)
        except Exception:
            logger.error("Error while emitting socketio", exc_info=True)

    def on(self, key: str, namespace: str | None = None) -> Callable:
        """Convenience method to register a callback for a socketio event"""
        if not namespace:
            namespace = f"/{self._namespace}"

        return self.socketio.on(key, namespace=namespace)

    @abstractmethod
    def setup(self):
        """Setup Initialiser

        Abstract method for any setup for the child class
        """
        pass

    def disconnect(self):
        """Called when the service is about to be closed"""
        pass


P = TypeVar("P")


class CoreResource(Generic[P]):
    """CoreResource is the base resource class that all flask Resources inherit from

    Kwargs:
        view_kwargs: View kwargs containing `parent`
    """

    def __init__(self, view_kwargs: dict[str, Any]):
        self.parent: P = view_kwargs["parent"]

    @property
    def rest_service(self) -> RestService:
        return current_app.config["REST_SERVICE"]
