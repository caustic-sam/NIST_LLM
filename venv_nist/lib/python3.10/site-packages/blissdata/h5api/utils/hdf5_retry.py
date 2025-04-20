import inspect
from functools import wraps
from collections.abc import Callable, Iterator
from typing import Any

from silx.io import h5py_utils

try:
    is_h5py_exception = h5py_utils.is_h5py_exception
except AttributeError:
    is_h5py_exception = h5py_utils._is_h5py_exception

from silx.utils.retry import RetryTimeoutError, RetryError


class RetryWithoutResetError(RetryError):
    """Retriable exception which does not require a reset. See :math:`retry_with_reset`."""

    pass


def retry_with_reset(method: Callable) -> Callable:
    """Retry the :math:`method` function until it no longer raises a retriable
    exception (see :math:`_exception_is_retriable`) or until the timeout limit
    is reached which means a `RetryTimeoutError` exception is raised (which
    is not retriable). Non-retriable exceptions are not handled.

    The :math:`reset` method of the instance to which :math:`method` belongs
    is called in the following situations

    - after each retriable exception, unless the exception is `RetryWithoutResetError`
    - when the timeout limit is reached, so when `RetryTimeoutError` is raised
    """
    return _reset_on_retry_timeout(
        h5py_utils.retry(retry_on_error=_exception_is_retriable)(
            _reset_on_retry_error(method)
        )
    )


def stop_iter_on_retry_timeout(method: Iterator) -> Iterator:
    """Stop iterating over :math:`method` when it raises `RetryTimeoutError`."""

    @wraps(method)
    def wrapper(self, *args, **kw):
        try:
            yield from method(self, *args, **kw)
        except RetryTimeoutError:
            pass

    return wrapper


def return_on_retry_timeout(default: Any) -> Callable[[Callable], Callable]:
    """Return :math:`default` when :math:`method` raises `RetryTimeoutError`."""

    def decorator(method: Callable) -> Callable:
        @wraps(method)
        def wrapper(self, *args, **kw):
            try:
                return method(self, *args, **kw)
            except RetryTimeoutError:
                return default

        return wrapper

    return decorator


def _exception_is_retriable(e: BaseException) -> bool:
    """Check whether the exception should be retried: :math:`h5py` error or `RetryError`."""
    return is_h5py_exception(e) or isinstance(e, RetryError)


def _reset_on_retry_error(method: Callable) -> Callable:
    """Call the :math:`reset` method of the instance to which :math:`method`
    belongs when it raises a retriable exception (see :math:`_exception_is_retriable`),
    unless the exception is `RetryWithoutResetError`."""
    if inspect.isgeneratorfunction(method):

        @wraps(method)
        def wrapper(self, *args, start_index: int = 0, **kw):
            try:
                yield from method(self, *args, start_index=start_index, **kw)
            except RetryWithoutResetError:
                raise  # retriable but we don't want to reset
            except Exception as e:
                if _exception_is_retriable(e):
                    self.reset()
                raise

    else:

        @wraps(method)
        def wrapper(self, *args, **kw):
            try:
                return method(self, *args, **kw)
            except RetryWithoutResetError:
                raise  # retriable but we don't want to reset
            except Exception as e:
                if _exception_is_retriable(e):
                    self.reset()
                raise

    return wrapper


def _reset_on_retry_timeout(method: Callable) -> Callable:
    """Call the :math:`reset` method of the instance to which :math:`method`
    belongs when :math:`method` raises a `RetryTimeoutError` exception."""
    if inspect.isgeneratorfunction(method):

        @wraps(method)
        def wrapper(self, *args, start_index: int = 0, **kw):
            kw.update(self._retry_options)
            try:
                yield from method(self, *args, start_index=start_index, **kw)
            except RetryTimeoutError:
                self.reset()
                raise

    else:

        @wraps(method)
        def wrapper(self, *args, **kw):
            kw.update(self._retry_options)
            try:
                return method(self, *args, **kw)
            except RetryTimeoutError:
                self.reset()
                raise

    return wrapper
