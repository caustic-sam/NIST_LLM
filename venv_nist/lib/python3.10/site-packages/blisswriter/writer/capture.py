from contextlib import contextmanager
from typing import Generator


@contextmanager
def capture_exceptions() -> Generator[Generator[None, None, None], None, None]:
    exceptions = list()

    @contextmanager
    def capture() -> Generator[None, None, None]:
        try:
            yield
        except Exception as e:
            exceptions.append(e)

    capture.failed = exceptions
    with capture():
        yield capture

    if exceptions:
        raise exceptions[0]
