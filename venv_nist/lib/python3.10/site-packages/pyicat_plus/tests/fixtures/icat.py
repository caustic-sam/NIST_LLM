import sys
import socket
import subprocess
from contextlib import contextmanager

import pytest

from . import proc
from . import tcp
from ...concurrency import flush_queue
from ...client.archive import IcatArchiveStatusClient
from ...client.elogbook import IcatElogbookClient
from ...client.metadata import IcatMetadataClient
from ...client.update_metadata import IcatUpdateMetadataClient
from ...client.add_files import IcatAddFilesClient
from ...client.main import IcatClient
from ...client.icatplus_restricted import IcatPlusRestrictedClient


__all__ = [
    # Backends
    "icat_data_dir",
    "cleanup_backend",
    "stomp_server",
    "icatplus_server",
    "activemq_rest_server",
    "icat_backend",
    # Read from the backend
    "icat_subscriber",
    "icat_subscriber_archive",
    "session_icat_logbook_subscriber",
    "icat_logbook_subscriber",
    "icat_subscriber_update_metadata",
    "icat_subscriber_add_files",
    # Write to the backend
    "session_icat_publisher",
    "icat_publisher",
    "session_elogbook_client",
    "elogbook_client",
    "session_elogbook_ebs_client",
    "elogbook_ebs_client",
    "icat_metadata_client",
    "icat_archive_client",
    "icat_main_client",
    "icat_update_metadata_client",
    "icat_add_files_client",
    "icatplus_restricted_client",
]


@pytest.fixture(scope="session")
def icat_data_dir(tmpdir_factory):
    """Directory where the ICAT dataset backend icat_subscriber can store state."""
    yield tmpdir_factory.mktemp("icat_backend")


@pytest.fixture()
def cleanup_backend(icat_data_dir):
    for dataset in icat_data_dir.listdir():
        dataset.remove()


@pytest.fixture(scope="session")
def stomp_server():
    """One of the ICAT backends (for dataset metadata)"""
    port = tcp.get_open_port()
    # Add arguments ["--debug", "TEXT"] for debugging
    p = subprocess.Popen(["coilmq", "-b", "127.0.0.1", "-p", str(port)])
    tcp.wait_tcp_online("localhost", port)
    try:
        yield "localhost", port
    finally:
        proc.wait_terminate(p)


@pytest.fixture(scope="session")
def activemq_rest_server():
    """One of the ICAT backends (for queue monitoring)"""
    port = tcp.get_open_port()
    p = subprocess.Popen(
        [
            sys.executable,
            "-u",
            "-m",
            "pyicat_plus.tests.servers.activemq_rest_server",
            f"--port={port}",
        ]
    )
    tcp.wait_tcp_online("localhost", port)
    try:
        yield "localhost", port
    finally:
        proc.wait_terminate(p)


@pytest.fixture(scope="session")
def icat_backend(stomp_server, activemq_rest_server, icatplus_server):
    """Starts all icat backends"""
    host, port = stomp_server
    metadata_urls = [f"{host}:{port}"]
    port, _ = icatplus_server
    elogbook_url = f"http://localhost:{port}"
    elogbook_token = "elogbook-00000000-0000-0000-0000-000000000000"
    host, port = activemq_rest_server
    metadata_queue_monitor_port = port
    icat_servers = {
        "metadata_urls": metadata_urls,
        "elogbook_url": elogbook_url,
        "elogbook_token": elogbook_token,
        "metadata_queue_monitor_port": metadata_queue_monitor_port,
        "elogbook_timeout": 5,
        "feedback_timeout": 5,
        "queue_timeout": 5,
        "add_files_urls": metadata_urls,
    }
    yield icat_servers


@pytest.fixture(scope="session")
def icat_subscriber(stomp_server, icat_data_dir):
    with _icat_subscriber(stomp_server, icat_data_dir, "/queue/icatIngest") as messages:
        yield messages


@pytest.fixture(scope="session")
def icat_subscriber_archive(stomp_server, icat_data_dir):
    with _icat_subscriber(
        stomp_server, icat_data_dir, "/queue/icatArchiveRestoreStatus"
    ) as messages:
        yield messages


@pytest.fixture(scope="session")
def icat_subscriber_update_metadata(stomp_server, icat_data_dir):
    with _icat_subscriber(
        stomp_server, icat_data_dir, "/queue/icatUpdateDatasetMetadata"
    ) as messages:
        yield messages


@pytest.fixture(scope="session")
def icat_subscriber_add_files(stomp_server, icat_data_dir):
    with _icat_subscriber(
        stomp_server, icat_data_dir, "/queue/icatDataFiles"
    ) as messages:
        yield messages


@contextmanager
def _icat_subscriber(stomp_server, icat_data_dir, queue):
    """Receive messages from the stomp_server backend"""
    with tcp.tcp_message_server() as (port_out, messages):
        host, port = stomp_server
        p = subprocess.Popen(
            [
                sys.executable,
                "-u",
                "-m",
                "pyicat_plus.tests.servers.stomp_subscriber",
                f"--host={host}",
                f"--port={port}",
                f"--port_out={port_out}",
                f"--queue={queue}",
                f"--icat_data_dir={icat_data_dir}",
            ]
        )
        try:
            assert messages.get(timeout=5) == "LISTENING"
            yield messages
        finally:
            proc.wait_terminate(p)


@pytest.fixture(scope="session")
def icatplus_server(icat_subscriber, icat_data_dir):
    """ICAT backend for the e-logbook and dataset queries.
    The icat_subscriber is needed to save published datasets
    in files so that the icatplus_server can use it when
    asked for the all published datasets.
    """
    with tcp.tcp_message_server("json") as (port_out, messages):
        port = tcp.get_open_port()
        p = subprocess.Popen(
            [
                sys.executable,
                "-u",
                "-m",
                "pyicat_plus.tests.servers.icatplus_server",
                f"--port={port}",
                f"--port_out={port_out}",
                f"--icat_data_dir={icat_data_dir}",
            ]
        )
        tcp.wait_tcp_online("localhost", port)
        try:
            assert messages.get(timeout=5) == {"STATUS": "LISTENING"}
            yield port, messages
        finally:
            proc.wait_terminate(p)


@pytest.fixture(scope="session")
def session_icat_logbook_subscriber(icatplus_server):
    """Receive messages from the icatplus_server backend"""
    _, messages = icatplus_server
    return messages


@pytest.fixture()
def icat_logbook_subscriber(session_icat_logbook_subscriber, cleanup_backend):
    """Receive messages from the icatplus_server backend"""
    with assert_no_messages(session_icat_logbook_subscriber):
        yield session_icat_logbook_subscriber


@contextmanager
def assert_no_messages(messages):
    for _ in flush_queue(messages):
        pass
    try:
        yield
    finally:
        try:
            assert messages.empty(), "not all messages have been validated"
        finally:
            for msg in flush_queue(messages):
                print(f"\nUnvalidated message: {msg}")


@pytest.fixture(scope="session")
def session_icat_publisher(stomp_server):
    """Sends messages to the /queue/icatIngest queue of stomp_server"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    port_in = tcp.get_open_port()
    sock.bind(("localhost", port_in))
    sock.listen()

    # Redirect messages from socket to STOMP server
    host, port = stomp_server
    p = subprocess.Popen(
        [
            sys.executable,
            "-u",
            "-m",
            "pyicat_plus.tests.servers.stomp_publisher",
            f"--host={host}",
            f"--port={port}",
            f"--port_in={port_in}",
            "--queue=/queue/icatIngest",
        ]
    )
    try:
        conn, addr = sock.accept()
        with conn:
            yield conn
    finally:
        sock.close()
        proc.wait_terminate(p)


@pytest.fixture()
def icat_publisher(session_icat_publisher, cleanup_backend):
    """Sends messages to the /queue/icatIngest queue of stomp_server"""
    yield session_icat_publisher


@pytest.fixture(scope="session")
def session_elogbook_client(icatplus_server):
    """Client to publish e-logbook messages and a queue that
    receives everything the ICAT backend receives.
    """
    port, messages = icatplus_server
    client = IcatElogbookClient(f"http://localhost:{port}")
    yield client, messages


@pytest.fixture()
def elogbook_client(session_elogbook_client, cleanup_backend):
    """Client to publish e-logbook messages and a queue that
    receives everything the ICAT backend receives.
    """
    client, messages = session_elogbook_client
    with assert_no_messages(messages):
        yield client, messages


@pytest.fixture(scope="session")
def session_elogbook_ebs_client(icatplus_server):
    """Client to publish e-logbook messages and a queue that
    receives everything the ICAT backend receives.
    """
    port, messages = icatplus_server
    client = IcatElogbookClient(
        f"http://localhost:{port}",
        tags=["testtag", "machine"],
        software="testsoft",
        source="ebs",
        type="broadcast",
    )
    yield client, messages


@pytest.fixture()
def elogbook_ebs_client(session_elogbook_ebs_client, cleanup_backend):
    """Client to publish e-logbook messages and a queue that
    receives everything the ICAT backend receives.
    """
    client, messages = session_elogbook_ebs_client
    with assert_no_messages(messages):
        yield client, messages


@pytest.fixture()
def icat_metadata_client(
    stomp_server, activemq_rest_server, icat_subscriber, cleanup_backend
):
    """Client to publish ICAT metadata and a queue that
    receives everything the ICAT backend receives.
    """
    _, jport = activemq_rest_server
    host, port = stomp_server
    messages = icat_subscriber
    client = IcatMetadataClient(queue_urls=[f"{host}:{port}"], monitor_port=jport)
    yield client, messages
    client.disconnect()


@pytest.fixture()
def icat_archive_client(
    stomp_server, activemq_rest_server, icat_subscriber_archive, cleanup_backend
):
    """Client to update dataset archive/restore status and a queue that
    receives everything the ICAT backend receives.
    """
    _, jport = activemq_rest_server
    host, port = stomp_server
    messages = icat_subscriber_archive
    client = IcatArchiveStatusClient(queue_urls=[f"{host}:{port}"], monitor_port=jport)
    yield client, messages
    client.disconnect()


@pytest.fixture()
def icat_update_metadata_client(
    stomp_server, activemq_rest_server, icat_subscriber_update_metadata, cleanup_backend
):
    """Client to update dataset sample and a queue that
    receives everything the ICAT backend receives.
    """
    _, jport = activemq_rest_server
    host, port = stomp_server
    messages = icat_subscriber_update_metadata
    client = IcatUpdateMetadataClient(queue_urls=[f"{host}:{port}"], monitor_port=jport)
    yield client, messages
    client.disconnect()


@pytest.fixture()
def icat_add_files_client(
    stomp_server, activemq_rest_server, icat_subscriber_add_files, cleanup_backend
):
    """Client to add missing files and a queue that
    receives everything the ICAT backend receives.
    """
    _, jport = activemq_rest_server
    host, port = stomp_server
    messages = icat_subscriber_add_files
    client = IcatAddFilesClient(queue_urls=[f"{host}:{port}"], monitor_port=jport)
    yield client, messages
    client.disconnect()


@pytest.fixture()
def icat_main_client(icat_backend, icat_subscriber, cleanup_backend):
    """Client to update dataset archive/restore status and a queue that
    receives everything the ICAT backend receives.
    """
    messages = icat_subscriber
    client = IcatClient(**icat_backend)
    yield client, messages


@pytest.fixture()
def icatplus_restricted_client(icatplus_server):
    """Client to access the resctricted ICAT+ API."""
    # No session fixture because the client stores the ICAT session after login.
    port, messages = icatplus_server
    client = IcatPlusRestrictedClient(f"http://localhost:{port}")
    yield client, messages
