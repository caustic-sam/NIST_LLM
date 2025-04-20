import queue
from blisswriter.subscribers.base_subscriber import BaseSubscriber


class _TestSubscriber(BaseSubscriber):
    def __init__(self, q):
        super().__init__("test")
        self._q = q
        self.counter = 0

    def _run(self):
        super()._run()
        while not self._stop_requested:
            try:
                item = self._q.get(timeout=0.1)
            except queue.Empty:
                continue
            if item == "end":
                break
            if item == "error":
                raise RuntimeError("test failure")
            self.counter += 1

    def __enter__(self):
        return self

    def __exit__(self, *_):
        """Ensure threads are cleaned up"""
        self.stop(timeout=3)


def test_base_subscriber():
    q = queue.Queue()
    with _TestSubscriber(q) as subscriber:
        assert subscriber.state == subscriber.state.INIT

        subscriber.start(timeout=3)
        assert subscriber.state == subscriber.state.ON
        for i in range(5):
            q.put(i)
        q.put("end")
        subscriber.join(timeout=3)

        assert subscriber.state == subscriber.state.OFF
        assert subscriber.counter == 5


def test_base_subscriber_stop():
    q = queue.Queue()
    with _TestSubscriber(q) as subscriber:
        subscriber.start(timeout=3)
        assert subscriber.state == subscriber.state.ON
        subscriber.stop(timeout=3)

        assert subscriber.state == subscriber.state.OFF


def test_base_subscriber_fault():
    q = queue.Queue()
    with _TestSubscriber(q) as subscriber:
        subscriber.start(timeout=3)
        q.put("error")
        subscriber.join(timeout=3)

        assert subscriber.state == subscriber.state.FAULT
        assert subscriber.state_reason == "test failure"


def test_base_subscriber_sort():
    q = queue.Queue()
    with _TestSubscriber(q) as subscriber1:
        with _TestSubscriber(q) as subscriber2:
            subscriber2.start(timeout=3)
        assert [subscriber1, subscriber2] == sorted([subscriber2, subscriber1])
