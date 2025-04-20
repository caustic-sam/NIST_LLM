import time
import json
import socket
from contextlib import contextmanager

import xmltodict

from .misc import eprint
from ...concurrency import Queue
from ...concurrency import spawn
from ...concurrency import GEVENT_PATCHED


def get_open_port():
    s = socket.socket()
    try:
        s.bind(("", 0))
        return s.getsockname()[1]
    finally:
        s.close()


def wait_tcp_online(host, port, timeout=5):
    """Wait for a TCP port with a timeout.

    Raises a `gevent.Timeout` if the port was not found.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        while True:
            try:
                sock.connect((host, port))
                break
            except ConnectionError:
                pass
    finally:
        sock.close()


@contextmanager
def tcp_message_server(data_parser=None, validate_all=True, timeout=5):
    """Start a TCP server and yield a queue of events.
    Data packages are separated by newline characters.
    Supported package encodings are UTF8 (default) and json.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    port = get_open_port()
    sock.bind(("localhost", port))
    sock.listen()

    # Listen to this socket
    messages = Queue()
    stop = False

    def listener():
        nonlocal stop

        buffer = b""
        conn = None
        try:
            while True:
                try:
                    conn, addr = sock.accept()
                    break
                except socket.timeout:
                    time.sleep(0.1)
            while True:
                try:
                    buffer += conn.recv(16384)
                except ConnectionResetError:
                    stop = True
                except socket.timeout:
                    pass
                if buffer:
                    out, sep, buffer = buffer.rpartition(b"\n")
                    if sep:
                        for bdata in out.split(b"\n"):
                            if data_parser == "json":
                                messages.put(json.loads(bdata))
                            elif b'xmlns:tns="http://www.esrf.fr/icat"' in bdata:
                                data = xmltodict.parse(
                                    bdata.decode(),
                                    process_namespaces=True,
                                    namespaces={"http://www.esrf.fr/icat": None},
                                )
                                messages.put(data)
                            else:
                                messages.put(bdata.decode())
                if stop:
                    return
                time.sleep(0.1)
        finally:
            if conn is not None:
                conn.close()

    glistener = spawn(listener)
    try:
        yield port, messages
    finally:
        messages.put(StopIteration)
        stop = True
        if validate_all:
            for msg in iter(messages.get, StopIteration):
                eprint(f"Unvalidated message: {msg}")
        if GEVENT_PATCHED:
            glistener.kill()
        else:
            glistener.join()
        sock.close()
