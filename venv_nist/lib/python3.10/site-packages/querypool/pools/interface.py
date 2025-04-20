from abc import ABC, abstractmethod
from typing import Any, Callable, Optional


class QueryPool(ABC):
    """
    Query pools let a query run in the background when it
    doesn't return within a given timeout. In that case the
    result of the previous query is returned or raised. If
    there is no result, the default value is returned.
    """

    @abstractmethod
    def __init__(
        self, timeout: Optional[float] = None, maxqueries: Optional[int] = None
    ):
        """
        :param timeout: The default timeout of a call before returning/raising the previous result.
        :param maxqueries: The maximal number of different queries to store results from.
                           A query can differ in terms of function and/or arguments.
        """
        pass

    @abstractmethod
    def execute(
        self,
        query: Callable,
        args: Optional[tuple] = tuple(),
        kwargs: Optional[dict] = None,
        timeout: Optional[float] = None,
        default=None,
    ) -> Any:
        """
        :param query:
        :param args: positional arguments
        :param kwargs: named arguments
        :param timeout: the timeout of a call before returning/raising the previous result
        :param default: the default value in case there is no previous result
        :returns: the result of the query or the default value
        :raises: the exception from the query
        """
        pass

    @abstractmethod
    def wait(self, timeout=None) -> bool:
        """
        :param timeout:
        :returns: `True` when all queries finished, `False` otherwise
        """
        pass

    @abstractmethod
    def cancel(self, timeout=None, block=True) -> Optional[bool]:
        """
        :param block:
        :param timeout: only applies when `block=True`
        :returns: `None` when `block=False`, `True` when all queries are
                  cancelled and `False` otherwise
        """
        pass
