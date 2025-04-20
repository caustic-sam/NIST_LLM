import time
import redis
import threading
import numpy as np
import pytest

from .utils import redis_config_ctx
from blissdata.redis_engine.event_stream import EventStream
from blissdata.streams.base import Stream, CursorGroup
from blissdata.redis_engine.exceptions import EndOfStream, IndexNoMoreThereError


def event_stream_pair(data_store, name, dtype, shape=()):
    stream_def = Stream.make_definition(name, dtype, shape)
    model = data_store._stream_model(
        encoding=stream_def.encoder.info(), info=stream_def.info
    )
    event_rw_stream = EventStream.create(data_store, stream_def.name, model)
    event_ro_stream = EventStream.open(data_store, stream_def.name, model)
    return event_rw_stream, event_ro_stream


def stream_pair(data_store, name, dtype, shape=()):
    # TODO use a context manager to seal the streams in case of error, so the sinks do not hang
    event_rw_stream, event_ro_stream = event_stream_pair(data_store, name, dtype, shape)
    rw_stream = Stream(event_rw_stream)
    ro_stream = Stream(event_ro_stream)
    return rw_stream, ro_stream


@pytest.mark.parametrize("last_only", [True, False])
@pytest.mark.parametrize("block", [True, False])
@pytest.mark.parametrize("seal", [True, False])
@pytest.mark.parametrize("group", [True, False])
@pytest.mark.parametrize(
    "batches",
    [
        [],  # empty
        [[0]],  # one
        [[0, 1, 2]],  # three
        [[0], [1, 2, 3]],  # one_three
        [[0, 1, 2], [3]],  # three_one
        [[0], [1], [2], [3], [4], [5], [6], [7], [8], [9]],  # singles
        [[0, 1], [2, 3, 4, 5, 6, 7], [8], [9, 10], [11, 12, 13], [14, 15]],  # mixed
    ],
)
def test_cursor_batching(data_store, last_only, batches, seal, block, group):
    stream_def = Stream.make_definition("my_stream", np.int64)
    model = data_store._stream_model(
        encoding=stream_def.encoder.info(), info=stream_def.info
    )
    stream = Stream(EventStream.create(data_store, stream_def.name, model))

    for batch in batches:
        stream.send(batch)
    if seal:
        stream.seal()
    stream.join()

    if last_only:
        expected_batch = batches[-1:]
    else:
        expected_batch = batches

    if expected_batch:
        expected_data = np.concatenate(expected_batch)
        if last_only:
            expected_data = expected_data[-1:]
    else:
        expected_data = np.array([], dtype=np.int64)

    if group:
        cursor = CursorGroup([stream])
    else:
        cursor = stream.cursor()

    if len(expected_data) == 0 and seal:
        output = {} if group else None
        with pytest.raises(EndOfStream):
            if block:
                output = cursor.read(timeout=0.001, last_only=last_only)
            else:
                output = cursor.read(block=False, last_only=last_only)
    else:
        if block:
            output = cursor.read(timeout=0.001, last_only=last_only)
        else:
            output = cursor.read(block=False, last_only=last_only)

    if group:
        view = output.get(stream)
    else:
        view = output

    if batches:
        assert view.index == expected_data[0]  # because values are the indexes
        assert np.array_equal(view.get_data(), expected_data)
    else:
        assert view is None


@pytest.fixture
def prepare_streams(data_store):
    streams_data = {
        "empty": [],
        "one": [[0]],
        "three": [[0, 1, 2]],
        "one_three": [[0], [1, 2, 3]],
        "three_one": [[0, 1, 2], [3]],
        "singles": [[0], [1], [2], [3], [4], [5], [6], [7], [8], [9]],
        "mixed": [[0, 1], [2, 3, 4, 5, 6, 7], [8], [9, 10], [11, 12, 13], [14, 15]],
    }
    streams = {}
    for name, data in streams_data.items():
        rw_stream, ro_stream = stream_pair(data_store, name, np.int64)
        for batch in data:
            rw_stream.send(batch)
        rw_stream.join()
        streams[name] = ro_stream
    return streams, streams_data


def test_cursor_read_from_origin(prepare_streams):
    streams, expected_data = prepare_streams
    cursor_group = CursorGroup(streams)
    output = cursor_group.read(block=False)
    named_output = {stream.name: view for stream, view in output.items()}
    for name, data in expected_data.items():
        if data:
            assert name in named_output
            view = named_output[name]
            assert view.index == 0
            assert np.array_equal(view.get_data(), np.concatenate(expected_data[name]))
        else:
            assert name not in named_output


def test_cursor_read_from_now(prepare_streams):
    streams, expected_data = prepare_streams

    cursor_group = CursorGroup(streams)
    # skip existing data
    _ = cursor_group.read(block=False, last_only=True)

    output = cursor_group.read(block=False)
    assert output == {}


@pytest.mark.parametrize("block", [True, False])
def test_cursor_block(data_store, block):
    rw_stream, ro_stream = stream_pair(data_store, "stream", np.int64)
    cursor_group = CursorGroup(streams={ro_stream.name: ro_stream})

    data = np.arange(100)

    def send_soon():
        time.sleep(0.5)
        rw_stream.send(data)

    t = threading.Thread(target=send_soon)
    t.start()
    output = cursor_group.read(block=block)
    t.join()

    if block:
        assert set(output.keys()) == {ro_stream}
        view = output[ro_stream]
        assert view.index == 0
        assert np.array_equal(data, view.get_data())
    else:
        assert output == {}


@pytest.mark.parametrize("timeout", [0.5, 2])
def test_cursor_timeout(data_store, timeout):
    rw_stream, ro_stream = stream_pair(data_store, "stream", np.int64)
    cursor_group = CursorGroup(streams={ro_stream.name: ro_stream})

    data = np.arange(100)

    def send_soon():
        time.sleep(1)
        rw_stream.send(data)

    t = threading.Thread(target=send_soon)
    t.start()
    output = cursor_group.read(timeout=timeout)
    t.join()

    if timeout > 1.0:
        assert set(output.keys()) == {ro_stream}
        view = output[ro_stream]
        assert view.index == 0
        assert np.array_equal(data, view.get_data())
    else:
        assert output == {}


def test_cursor_block_exit(data_store):
    """CursorGroup.read(block=True) should return as soon as data arrives in any of its streams"""
    rw_streams = []
    ro_streams = []
    for i in range(3):
        rw_stream, ro_stream = stream_pair(data_store, f"stream_{i}", np.int64)
        rw_streams.append(rw_stream)
        ro_streams.append(ro_stream)

    cursor_group = CursorGroup(streams={s.name: s for s in ro_streams})

    def send_soon():
        time.sleep(0.5)
        rw_streams[1].send([1, 2, 3])
        rw_streams[1].join()
        rw_streams[2].send([4, 5, 6])
        rw_streams[2].join()
        rw_streams[1].send([7, 8, 9])

    t = threading.Thread(target=send_soon)
    t.start()
    output = cursor_group.read()
    t.join()

    assert set(output.keys()) == {ro_streams[1]}
    view = output[ro_streams[1]]
    assert view.index == 0
    assert np.array_equal([1, 2, 3], view.get_data())

    for rw_stream in rw_streams:
        rw_stream.join()
    output = cursor_group.read()

    assert set(output.keys()) == {ro_streams[1], ro_streams[2]}
    view = output[ro_streams[1]]
    assert view.index == 3
    assert np.array_equal([7, 8, 9], view.get_data())
    view = output[ro_streams[2]]
    assert view.index == 0
    assert np.array_equal([4, 5, 6], view.get_data())


def test_cursor_sealed_stream(data_store):
    rw_stream, ro_stream = stream_pair(data_store, "stream", dtype=int)
    cursor_group = CursorGroup({"my_stream": ro_stream})

    rw_stream.send([1, 2, 3])
    _ = cursor_group.read()
    rw_stream.seal()

    with pytest.raises(EndOfStream):
        _ = cursor_group.read()
    assert not cursor_group._active_cursors
    assert ro_stream.event_stream._seal is not None


def test_cursor_multi_bunch(data_store):
    nb_streams = 100

    rw_streams = []
    ro_streams = []
    for i in range(nb_streams):
        rw_stream, ro_stream = stream_pair(data_store, f"stream_{i}", np.int64)
        rw_streams.append(rw_stream)
        ro_streams.append(ro_stream)

    cursor_group = CursorGroup(
        streams={f"my_stream{i}": ro_streams[i] for i in range(nb_streams)}
    )

    data = np.arange(2000)

    def send_soon():
        for slice_index in range(0, 2000, 100):
            for stream in rw_streams:
                stream.send(data[slice_index : slice_index + 100])

    t = threading.Thread(target=send_soon)
    t.start()
    all_data = {stream: np.empty(shape=(0), dtype=np.int64) for stream in ro_streams}
    while True:
        new_data = cursor_group.read(
            timeout=0.1
        )  # TODO if something slow down the test, it can miss data
        for stream, view in new_data.items():
            all_data[stream] = np.concatenate((all_data[stream], view.get_data()))
        if not new_data:
            break
    t.join()

    assert len(all_data) == nb_streams
    for stream_data in all_data.values():
        assert np.array_equal(stream_data, data)
        # TODO FAILED WITH [], [0:2000]


def test_monitor_stream(data_store):
    rw_stream, ro_stream = stream_pair(data_store, "stream", dtype=int)
    cursor_group = CursorGroup({"my_stream": ro_stream})

    # read last batch (less than available)
    rw_stream.send(10)  # 0
    rw_stream.send([20, 21])  # 1, 2
    rw_stream.send([30, 31, 32])  # 3, 4, 5
    rw_stream.join()
    view = cursor_group.read(block=False, last_only=True)[ro_stream]
    assert view.index == 5
    assert np.array_equal(view.get_data(), [32])

    # read at most n last batches (exactly what's available)
    rw_stream.send([40, 41])  # 6, 7
    rw_stream.join()
    view = cursor_group.read(block=False, last_only=True)[ro_stream]
    assert view.index == 7
    assert np.array_equal(view.get_data(), [41])

    # read last batch, but there is nothing new
    assert cursor_group.read(block=False, last_only=True) == {}

    # blocking read of at most n last batches (only one is returned)
    def send_soon():
        time.sleep(0.5)
        rw_stream.send(11)  # 8
        rw_stream.send(21)  # 9
        rw_stream.send(31)  # 10

    t = threading.Thread(target=send_soon)
    t.start()

    view = cursor_group.read(last_only=True)[ro_stream]
    assert view.index == 8
    assert np.array_equal(
        view.get_data(), [11]
    )  # 11 unblock the cursor_group, 21 and 31 arrive after
    t.join()


def test_monitor_sealed_stream(data_store):
    rw_stream, ro_stream = stream_pair(data_store, "stream", dtype=int)
    cursor_group = CursorGroup({"my_stream": ro_stream})

    rw_stream.send([1, 2, 3])
    rw_stream.seal()

    view = cursor_group.read(last_only=True)[ro_stream]
    assert view.index == 2
    assert np.array_equal(view.get_data(), [3])


def test_monitor_sealed_stream_no_new_available(data_store):
    rw_stream, ro_stream = stream_pair(data_store, "stream", dtype=int)
    cursor_group = CursorGroup({"my_stream": ro_stream})

    rw_stream.send([1, 2, 3])
    _ = cursor_group.read(last_only=True)
    rw_stream.seal()

    with pytest.raises(EndOfStream):
        _ = cursor_group.read(last_only=True)


def test_monitor_sealed_stream_no_data_at_all(data_store):
    rw_stream, ro_stream = stream_pair(data_store, "stream", dtype=int)
    cursor_group = CursorGroup({"my_stream": ro_stream})

    rw_stream.seal()
    with pytest.raises(EndOfStream):
        _ = cursor_group.read(last_only=True)


def test_monitor_sealed_stream_during_read(data_store):
    rw_stream, ro_stream = stream_pair(data_store, "stream", dtype=int)
    cursor_group = CursorGroup({"my_stream": ro_stream})

    def seal_soon():
        time.sleep(0.5)
        rw_stream.seal()

    t = threading.Thread(target=seal_soon)
    t.start()

    with pytest.raises(EndOfStream):
        _ = cursor_group.read(last_only=True)
    t.join()


def test_cursor_discontinuity(data_store):
    rw_stream, ro_stream = stream_pair(data_store, "stream", dtype=int)
    cursor_group = CursorGroup([ro_stream])

    rw_stream.send([0, 1, 2, 3])
    rw_stream.join()
    _ = cursor_group.read()

    # break the internal index before writing again
    rw_stream._event_stream._write_index += 1

    rw_stream.send([4, 5, 6, 7])
    rw_stream.join()

    view = cursor_group.read()[ro_stream]
    assert view.index == 4
    assert len(view) == 5
    with pytest.raises(IndexNoMoreThereError):
        view.get_data()


@pytest.mark.timeout(20)
def test_out_of_memory_send(redis_url, data_store):
    with redis_config_ctx(
        redis_url,
        redis_config={
            "maxmemory": "20MB",
            "maxmemory-policy": "noeviction",
        },
    ):
        rw_stream, _ = stream_pair(data_store, "stream", dtype=float, shape=(64,))

        with pytest.raises(redis.exceptions.ResponseError):
            # send too much data for Redis
            for i in range(40000):
                rw_stream.send(np.empty((64,)))
            # If the sink puts commands in the socket fast enough, errors from Redis may
            # have not been received yet. In that case, let's wait a bit an send again.
            while True:
                time.sleep(0.01)
                rw_stream.send(np.empty((64,)))


@pytest.mark.timeout(20)
def test_out_of_memory_join(redis_url, data_store):
    with redis_config_ctx(
        redis_url,
        redis_config={
            "maxmemory": "128MB",
            "maxmemory-policy": "noeviction",
        },
    ):
        rw_stream, _ = stream_pair(data_store, "stream", dtype=float, shape=(64,))

        # send ALMOST too much data for Redis
        while data_store._redis.info()["used_memory"] < 100 * 2**20:  # ~100MB
            rw_stream.send(
                np.empty(
                    (
                        10000,
                        64,
                    )
                )
            )
            rw_stream.join()

        # Overload Redis in one go, so the error can only be raised later (in the join())
        rw_stream.send(np.empty((100000, 64)))
        with pytest.raises(redis.exceptions.ResponseError):
            rw_stream.join()


@pytest.mark.timeout(20)
def test_out_of_memory_seal(redis_url, data_store):
    with redis_config_ctx(
        redis_url,
        redis_config={
            "maxmemory": "128MB",
            "maxmemory-policy": "noeviction",
        },
    ):
        rw_stream, _ = stream_pair(data_store, "stream", dtype=float, shape=(64,))

        # send ALMOST too much data for Redis
        while data_store._redis.info()["used_memory"] < 100 * 2**20:  # ~100MB
            rw_stream.send(
                np.empty(
                    (
                        10000,
                        64,
                    )
                )
            )
            rw_stream.join()

        # Overload Redis in one go, so the error can only be raised later (in the seal())
        rw_stream.send(np.empty((100000, 64)))
        with pytest.raises(redis.exceptions.ResponseError):
            rw_stream.seal()
