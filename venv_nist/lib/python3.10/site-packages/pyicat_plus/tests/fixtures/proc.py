import os
import psutil
import pytest
from .misc import eprint
from ...concurrency import wait_process


__all__ = ["cleanup_subprocesses"]


@pytest.fixture
def cleanup_subprocesses():
    yield
    if os.name == "nt":
        current_process = psutil.Process()
        for p in current_process.children():
            wait_terminate(p)


def wait_terminate(process, timeout=10):
    """Try to terminate or kill a process.

    :param process: A process object from `subprocess` or `psutil`, or an PID int
    :param timeout: Timeout to way before using a kill signal
    :raises AssertionError: If the kill fails
    """
    if isinstance(process, int):
        try:
            name = str(process)
            process = psutil.Process(process)
        except Exception:
            # PID is already dead
            return
    else:
        name = repr(" ".join(process.args))
        if process.poll() is not None:
            eprint(f"Process {name} already terminated with code {process.returncode}")
            return
    process.terminate()
    terminated = wait_process(process, 2 * timeout / 3)
    if terminated:
        return
    process.kill()
    terminated = wait_process(process, timeout / 3)
    assert terminated, f"Process {name} doesn't finish: try to kill it..."
