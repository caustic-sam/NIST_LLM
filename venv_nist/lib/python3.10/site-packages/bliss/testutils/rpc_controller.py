import os
import sys
import subprocess
import contextlib
from bliss.comm.rpc import Client


@contextlib.contextmanager
def controller_in_another_process(
    beacon_host: str, beacon_port: int, controller_name: str
):
    """
    Context manager to handle the live cycle of a remote server running a BLISS
    controller and a proxy connected to it.

    It yields a proxy to the controller which allow to interact with basic getter
    and setters.

    Arguments:
        beacon_host: Beacon host
        beacon_port: Beacon port
        controller_name: Name of the BLISS controller to instantiate
    """
    env = {"PYTHONPATH": os.getcwd(), "TANGO_HOST": os.environ.get("TANGO_HOST", "")}
    p = subprocess.Popen(
        [
            sys.executable,
            "-u",
            os.path.join(os.path.dirname(__file__), "servers/rpc_controller_server.py"),
            f"{beacon_host}",
            f"{beacon_port}",
            controller_name,
        ],
        # allow child process to import from local dir as its parent (e.g. "import tests")
        env=env,
        stdout=subprocess.PIPE,
    )
    line = p.stdout.readline()  # synchronize process start
    try:
        port = int(line)
    except ValueError:
        raise RuntimeError("server didn't start")

    remote_controller = Client(f"tcp://localhost:{port}")

    try:
        yield remote_controller
    finally:
        p.terminate()
        remote_controller._rpc_connection.close()
