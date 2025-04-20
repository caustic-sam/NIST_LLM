import psutil
import gevent
import sys
import contextlib
import subprocess
from bliss.tango.clients.utils import wait_tango_device


def eprint(*args):
    print(*args, file=sys.stderr, flush=True)


def wait_for(stream, target: bytes, timeout=None):
    """Wait for a specific bytes sequence from a stream.

    Arguments:
        stream: The stream to read
        target: The sequence to wait for
    """
    data = b""
    target = target.encode()
    while target not in data:
        char = stream.read(1)
        if not char:
            raise RuntimeError(
                "Target {!r} not found in the following stream:\n{}".format(
                    target, data.decode()
                )
            )
        data += char


@contextlib.contextmanager
def start_tango_server(*cmdline_args, check_children=False, **kwargs):
    """
    Arguments:
        check_children: If true, children PID are also checked during the terminating
    """
    device_fqdn = kwargs["device_fqdn"]
    exception = None
    for _ in range(3):
        p = subprocess.Popen(cmdline_args)
        try:
            dev_proxy = wait_tango_device(**kwargs)
        except Exception as e:
            exception = e
            wait_terminate(p)
        else:
            break
    else:
        raise RuntimeError(f"could not start {device_fqdn}") from exception

    try:
        # FIXME: This have to be cleaned up by returning structured data
        # Expose the server PID as a proxy attribute
        object.__setattr__(dev_proxy, "server_pid", p.pid)
        yield dev_proxy
    finally:
        wait_terminate(p, check_children=check_children)


def wait_terminate(process, timeout=10, check_children=False):
    """
    Try to terminate a process then kill it.

    This ensure the process is terminated.

    Arguments:
        process: A process object from `subprocess` or `psutil`, or an PID int
        timeout: Timeout to way before using a kill signal
        check_children: If true, check children pid and force there termination
    Raises:
        gevent.Timeout: If the kill fails
    """
    children = []
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

    if check_children:
        if not isinstance(process, psutil.Process):
            process = psutil.Process(process.pid)
        children = process.children(recursive=True)

    process.terminate()
    try:
        with gevent.Timeout(timeout):
            # gevent timeout have to be used here
            # See https://github.com/gevent/gevent/issues/622
            process.wait()
    except gevent.Timeout:
        eprint(f"Process {name} doesn't finish: try to kill it...")
        process.kill()
        with gevent.Timeout(10):
            # gevent timeout have to be used here
            # See https://github.com/gevent/gevent/issues/622
            process.wait()

    if check_children:
        for i in range(10):
            _done, alive = psutil.wait_procs(children, timeout=1)
            if not alive:
                break
            for p in alive:
                try:
                    if i < 3:
                        p.terminate()
                    else:
                        p.kill()
                except psutil.NoSuchProcess:
                    pass
        else:
            raise RuntimeError(
                "Timeout expired after 10 seconds. Process %s still alive." % alive
            )
