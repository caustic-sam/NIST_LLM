import os
import functools
from time import time
from typing import Optional

import gevent
from tango import CommunicationFailed, ConnectionFailed
from blisswriter.app.register_service import find_session_writer, get_uri

from bliss import current_session
from bliss.common import logtools
from bliss.common.os_utils import is_subdir
from bliss.common.tango import DeviceProxy, DevState, DevFailed
from .nexus import NexusWriter


def _skip_when_fault(method):
    @functools.wraps(method)
    def __skip_when_fault(self, *args, **kwargs):
        if self._fault:
            return True
        return method(self, *args, **kwargs)

    return __skip_when_fault


class ExternalTangoNexusWriter(NexusWriter, name="nexus"):
    """NeXus writer in an external TANGO process. Monitor the health of the writing during the scan."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.__proxy = None
        self._fault = False
        self._state_checked = False
        self._scan_name = ""
        self._warn_msg_dict = dict()

        # For periodical health checks
        self._master_event_callback = self._check_on_master_event
        self._master_event_callback_time = 0.0
        self._master_event_callback_period = 3

    @property
    def configurable(self) -> bool:
        return True  # this is an assumption, it depends on the writer start arguments

    def create_path(self, path: str) -> bool:
        """The root directory is owned by the Nexus writer.
        All other directories are owned by Bliss.
        """
        self._retry(
            self._create_path,
            timeout_msg="Cannot create directory",
            fail_msg="Nexus writer cannot create directory",
            args=(path,),
        )

    def _create_path(self, path: str) -> bool:
        abspath = os.path.abspath(path)
        if os.path.isdir(abspath):
            return True
        if is_subdir(abspath, self._get_root_path()):
            # the Nexus writer can access root_path
            self._proxy.makedirs(abspath)
            return True
        # the Nexus writer might not be able to access this directory
        return super().create_path(abspath)

    def prepare(self, scan):
        # Called at start of scan
        self._fault = False
        self._state_checked = False
        self._scan_name = scan.identifier
        self._retry(
            self._is_writer_on,
            timeout_msg="Cannot check Nexus writer state",
            fail_msg="Nexus writer is not ON or RUNNING",
        )
        self.create_path(self._get_root_path())
        self._retry(
            self._scan_writer_started,
            timeout_msg="Cannot check Nexus writer scan state",
            fail_msg=f"Nexus writer did not receive scan '{self._scan_name}' after {{timeout}}s",
            raise_on_timeout=True,
        )
        self._retry(
            self._check_writer_permissions,
            timeout_msg="Cannot check Nexus writer permissions",
            fail_msg="Nexus writer does not have write permissions",
        )
        self._retry(
            self._check_required_disk_space,
            timeout_msg="Cannot check Nexus writer disk space",
            fail_msg="Not enough free disk space",
        )
        self._master_event_callback_time = time()
        super().prepare(scan)

    def _check_on_master_event(self, *_) -> None:
        if self._master_event_callback_tick:
            self._state_checked = True
            self._check_writer(timeout=0)

    @property
    def _master_event_callback_tick(self) -> bool:
        tm = time()
        tmmax = self._master_event_callback_time + self._master_event_callback_period
        if tm <= tmmax:
            return False
        self._master_event_callback_time = tm
        return True

    def _prepare_scan(self, scan) -> None:
        pass

    @_skip_when_fault
    def finalize(self, scan) -> None:
        if not self._scan_name:
            # No finalization is needed when not prepared
            return
        # We currently do not wait for the writer to finish
        if not self._state_checked:
            self._check_writer(timeout=10)

    def _check_writer(self, timeout=0) -> None:
        """
        :raises RuntimeError:
        """
        self._retry(
            self._warn_low_disk_space,
            timeout_msg="Cannot check Nexus writer scan disk space",
            timeout=timeout,
            raise_on_timeout=False,
        )
        self._retry(
            self._is_scan_notfault,
            timeout_msg="Cannot check Nexus writer scan state",
            timeout=timeout,
            raise_on_timeout=False,
        )

    def _retry(
        self,
        method,
        timeout_msg=None,
        fail_msg=None,
        timeout=10,
        proxy_timeout=3,
        raise_on_timeout=False,  # TODO: default True when the nexus writer is more responsive
        args=None,
        kwargs=None,
    ) -> None:
        """Call `method` until

        * returns True (returning False means it may return True when called again)
        * raises exception (some Tango communication exceptions are ignored)
        * timeout

        When retrying is pointless, the method should raise an
        exception instead of returning `False`.

        :param callable method: returns True or False
        :param str timeout_msg:
        :param str fail_msg:
        :param num timeout: in seconds (try only once when zero)
        :param bool raise_on_timeout: log or raise timeout
        """
        t0 = time()
        if not timeout_msg:
            timeout_msg = "Nexus writer check failed"
        if fail_msg:
            err_msg = fail_msg
        else:
            err_msg = timeout_msg
        cause = None
        first = True
        self._set_proxy_timeout(proxy_timeout)
        if args is None:
            args = tuple()
        if kwargs is None:
            kwargs = dict()
        while (time() - t0) < timeout or first:
            first = False
            try:
                if method(*args, **kwargs):
                    return
            except ConnectionFailed as e:
                cause = e
                err_msg = _deverror_parse(e.args[0], msg=timeout_msg)
                if e.args[0].reason in ["API_DeviceNotExported", "DB_DeviceNotDefined"]:
                    raise_on_timeout = self._fault = True
                    break
            except CommunicationFailed as e:
                cause = e
                err_msg = _deverror_parse(e.args[1], msg=timeout_msg)
            except DevFailed as e:
                cause = e
                err_msg = _deverror_parse(e.args[0], msg=timeout_msg)
                raise_on_timeout = self._fault = True
                break
            except Exception:
                raise_on_timeout = self._fault = True
                raise
            gevent.sleep(0.1)
        err_msg_show = err_msg.format(timeout=timeout)
        if raise_on_timeout:
            raise RuntimeError(err_msg_show) from cause
        else:
            # Do not repeat the same warning
            previous_msgs = self._warn_msg_dict.setdefault(method.__qualname__, set())
            if err_msg in previous_msgs:
                logtools.log_debug(self, err_msg_show)
            else:
                previous_msgs.add(err_msg)
                logtools.log_warning(self, err_msg_show)

    @property
    def _writer_uri(self) -> Optional[str]:
        return find_session_writer(
            current_session.name, beacon_host=os.environ.get("BEACON_HOST")
        )

    @property
    def _full_writer_uri(self) -> str:
        uri = self._writer_uri
        p = self.__proxy
        if p is None:
            return uri
        return get_uri(p)

    @property
    def _proxy(self) -> Optional[DeviceProxy]:
        self._store_proxy()
        if self.__proxy is None:
            raise RuntimeError("No Nexus writer registered for this session")
        return self.__proxy

    def _set_proxy_timeout(self, seconds: float) -> None:
        self._proxy.set_timeout_millis(int(seconds * 1000.0))

    @_skip_when_fault
    def _store_proxy(self) -> None:
        if self.__proxy is None:
            uri = self._writer_uri
            if not uri:
                self._fault = True
                raise RuntimeError("No Nexus writer registered for this session")
            self.__proxy = DeviceProxy(uri)

    def _get_session_state(self) -> DevState:
        return self._proxy.state()

    def _get_session_state_reason(self) -> str:
        return self._proxy.status()

    def _get_scan_state(self) -> DevState:
        return self._proxy.scan_state(self._scan_name)

    def _get_scan_state_reason(self) -> str:
        return self._proxy.scan_state_reason(self._scan_name)

    def _get_scan_has_write_permissions(self) -> bool:
        return self._proxy.scan_has_write_permissions(self._scan_name)

    def _get_scan_disk_space_error(self) -> str:
        return self._proxy.scan_disk_space_error(self._scan_name)

    def _get_scan_disk_space_warning(self) -> str:
        return self._proxy.scan_disk_space_warning(self._scan_name)

    def _get_scan_exists(self) -> bool:
        return self._proxy.scan_exists(self._scan_name)

    def _is_writer_on(self) -> bool:
        """
        :returns bool: state is valid and expected
        :raises RuntimeError: invalid state
        """
        state = self._get_session_state()
        if state in [DevState.ON, DevState.RUNNING]:
            return True
        elif state in [DevState.FAULT, DevState.OFF, DevState.UNKNOWN]:
            reason = self._get_session_state_reason()
            raise RuntimeError(
                "Nexus writer service is in {} state ({}). Call the Nexus writer 'start' method.".format(
                    state.name, reason
                )
            )
        else:
            return False

    @_skip_when_fault
    def _is_scan_finished(self) -> bool:
        """
        :returns bool: state is valid and expected
        :raises RuntimeError: invalid state
        """
        state = self._get_scan_state()
        if state == DevState.OFF:
            return True
        elif state == DevState.FAULT:
            reason = self._get_scan_state_reason()
            raise RuntimeError(
                "Nexus writer is in FAULT state due to {}".format(repr(reason))
            )
        else:
            return False

    def _is_scan_notfault(self) -> bool:
        """
        :returns bool: state is valid and expected
        :raises RuntimeError: invalid state
        """
        state = self._get_scan_state()
        if state == DevState.FAULT:
            reason = self._get_scan_state_reason()
            raise RuntimeError(f"Nexus writer is in FAULT state ({reason})")
        else:
            return True

    def _check_writer_permissions(self) -> bool:
        """Checks whether the writer process has write permissions.

        :returns bool: writer can write
        :raises RuntimeError: invalid state
        """
        if not self._get_scan_has_write_permissions():
            raise RuntimeError("Nexus writer does not have write permissions")
        return True

    def _check_required_disk_space(self) -> bool:
        """
        :returns bool: writer can write
        :raises RuntimeError: not enough disk space
        """
        err_msg = self._get_scan_disk_space_error()
        if err_msg:
            raise RuntimeError(err_msg)
        return True

    def _warn_low_disk_space(self) -> bool:
        """Print a warning when the disk space is lower than the required amount."""
        err_msg = self._get_scan_disk_space_warning()
        if err_msg:
            logtools.log_warning(self, err_msg)
        return True

    def _scan_writer_started(self) -> bool:
        """
        :returns bool: writer exists
        """
        return self._get_scan_exists()

    def _str_state(self, session: bool) -> str:
        if session:
            msg = "Nexus writer"
        else:
            msg = "Nexus writer scan " + repr(self._scan_name)
        try:
            if session:
                state = self._get_session_state()
                reason = self._get_session_state_reason()
            else:
                state = self._get_scan_state()
                reason = self._get_scan_state_reason()
        except CommunicationFailed as e:
            msg += ": cannot get state"
            return _deverror_parse(e.args[1], msg)
        except DevFailed as e:
            msg += ": cannot get state"
            return _deverror_parse(e.args[0], msg)
        else:
            return "{} in {} state ({})".format(msg, state.name, reason)

    @property
    def _str_session_state(self) -> str:
        return self._str_state(True)

    @property
    def _str_scan_state(self) -> str:
        return self._str_state(False)


def _deverror_parse(deverror, msg=None):
    reason = deverror.reason
    desc = deverror.desc.strip()
    if not msg:
        msg = ""
    if "PythonError" in reason:
        msg += f" (Nexus writer {desc})"
    else:
        msg += f" (Nexus writer {reason}: {desc})"
    return msg
