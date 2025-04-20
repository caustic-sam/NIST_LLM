# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

import pytest
import weakref
import gc
import sys
import gevent
import socket
import redis
from gevent import Greenlet
from bliss.common.tango import ApiUtil


def _eprint(*args):
    print(*args, file=sys.stderr, flush=True)


class ResourcesContext:
    """
    This context ensure that every resource created during its execution
    are properly released.

    If a resource is not released at the exit, a warning is displayed,
    and it tries to release it.

    It is not concurrency safe.
    """

    def __init__(self, release, is_released, *resource_classes):
        self.resource_classes = resource_classes
        self.is_released = is_released
        self.release = release
        self.resources_before = weakref.WeakSet()
        self.all_resources_released = None

    def _iter_referenced_resources(self):
        for ob in gc.get_objects():
            try:
                if not isinstance(ob, self.resource_classes):
                    continue
            except ReferenceError:
                continue
            yield ob

    def __enter__(self):
        self.resources_before.clear()
        self.all_resources_released = None
        for ob in self._iter_referenced_resources():
            self.resources_before.add(ob)
        return self

    def resource_repr(self, ob):
        return repr(ob)

    def __exit__(self, exc_type, exc_val, exc_tb):
        resources = []
        for ob in self._iter_referenced_resources():
            if ob in self.resources_before:
                continue
            if not self.is_released(ob):
                _eprint(f"Resource not released: {self.resource_repr(ob)}")
            resources.append(ob)

        self.resources_before.clear()
        self.all_resources_released = all(self.is_released(r) for r in resources)
        if not resources:
            return
        err_msg = f"Resources {self.resource_classes} cannot be released"
        with gevent.Timeout(10, RuntimeError(err_msg)):
            for r in resources:
                self.release(r)


class GreenletsContext(ResourcesContext):
    def __init__(self):
        super().__init__(lambda glt: glt.kill(), lambda glt: glt.ready(), Greenlet)


class SocketsContext(ResourcesContext):
    def __init__(self):
        super().__init__(
            lambda sock: sock.close(), lambda sock: sock.fileno() == -1, socket.socket
        )

    def resource_repr(self, sock):
        try:
            return f"{repr(sock)} connected to {sock.getpeername()}"
        except Exception:
            return f"{repr(sock)} not connected"


class RedisConnectionContext(ResourcesContext):
    def __init__(self):
        super().__init__(
            lambda conn: conn.disconnect(),
            lambda conn: conn._sock.fileno() == -1,
            redis.connection.Connection,
        )

    def resource_repr(self, conn):
        return f"{repr(conn)} connected to {conn._sock.getpeername()}"


def raise_when_test_passed(request, err_msg: str, end_check: bool = True) -> None:
    node = request.node
    if end_check and hasattr(node, "rep_call") and node.rep_call.passed:
        raise RuntimeError(err_msg)
    else:
        _eprint(err_msg)


@pytest.fixture(autouse=True)
def clean_pt_context(request):
    """Make sure the prompt toolkit context was cleaned up"""
    from prompt_toolkit.application import current

    app_session = current._current_app_session.get()
    is_default = app_session._input is None and app_session._output is None

    d = {"end-check": True}
    yield d
    end_check = d.get("end-check", False)

    if is_default:
        if not (app_session._input is None and app_session._output is None):
            raise_when_test_passed(
                request,
                "Prompt-toolkit AppSession was not cleaned up",
                end_check=end_check,
            )


@pytest.fixture(autouse=True)
def clean_louie(request):
    import louie.dispatcher as disp

    disp.connections = {}
    disp.senders = {}
    disp.senders_back = {}
    disp.plugins = []

    d = {"end-check": True}
    yield d
    end_check = d.get("end-check", False)

    try:
        if disp.connections:
            raise_when_test_passed(
                request,
                f"Louie connections not released: {disp.connections}",
                end_check=end_check,
            )
        if disp.senders:
            raise_when_test_passed(
                request,
                f"Louie senders not released: {disp.senders}",
                end_check=end_check,
            )
        if disp.senders_back:
            raise_when_test_passed(
                request,
                f"Louie senders_back not released: {disp.senders_back}",
                end_check=end_check,
            )
        if disp.plugins:
            raise_when_test_passed(
                request,
                f"Louie plugins not released: {disp.plugins}",
                end_check=end_check,
            )
    finally:
        disp.reset()


@pytest.fixture(autouse=True)
def clean_gevent(request):
    """
    Context manager to check that greenlets are properly released during a test.

    It is not concurrency safe. The global context is used to
    check available greenlets.

    If the fixture is used as the last argument if will only test the greenlets
    creating during the test.

    .. code-block:: python

        def test_a(fixture_a, fixture_b, clean_gevent):
            ...

    If the fixture is used as the first argument if will also test greenlets
    created by sub fixtures.

    .. code-block:: python

        def test_b(clean_gevent, fixture_a, fixture_b):
            ...
    """
    d = {"end-check": True, "ignore-cannot-released": False}
    ignore_cannot_released = False
    try:
        with GreenletsContext() as context:
            yield d
            ignore_cannot_released = d.get("ignore-cannot-released", False)
    except RuntimeError:
        if not ignore_cannot_released:
            raise

    end_check = d.get("end-check", False)
    if not context.all_resources_released:
        raise_when_test_passed(
            request, "Not all greenlets were released", end_check=end_check
        )


@pytest.fixture
def clean_socket(request):
    """
    Context manager to check that sockets are properly closed during a test.

    It is not concurrency safe. The global context is used to
    check available sockets.

    If the fixture is used as the last argument if will only test the sockets
    creating during the test.

    .. code-block:: python

        def test_a(fixture_a, fixture_b, clean_gevent):
            ...

    If the fixture is used as the first argument if will also test sockets
    created by sub fixtures.

    .. code-block:: python

        def test_b(clean_socket, fixture_a, fixture_b):
            ...
    """
    d = {"end-check": True}
    with SocketsContext() as context:
        yield d
    end_check = d.get("end-check", False)
    if not context.all_resources_released:
        raise_when_test_passed(
            request, "Not all sockets were released", end_check=end_check
        )


@pytest.fixture(autouse=True)
def clean_tango():
    # close file descriptors left open by Tango (see tango-controls/pytango/issues/324)
    try:
        ApiUtil.cleanup()
    except RuntimeError:
        # no Tango ?
        pass
