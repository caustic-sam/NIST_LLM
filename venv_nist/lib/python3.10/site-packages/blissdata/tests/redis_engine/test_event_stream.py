import re
import time
import threading
import numpy as np
import pytest

from blissdata.redis_engine.encoding.numeric import NumericStreamEncoder
from blissdata.redis_engine.event_stream import EventStream
from blissdata.redis_engine.exceptions import (
    IndexNotYetThereError,
    NoWritePermission,
    UnknownEncodingError,
)


def event_stream_pair(data_store, dtype):
    # TODO use a context manager to seal the streams in case of error, so the sinks do not hang
    encoder = NumericStreamEncoder(dtype)
    model = data_store._stream_model(encoding=encoder.info())
    name = f"stream_{model.pk}"
    rw_stream = EventStream.create(data_store, name, model)
    ro_stream = EventStream.open(data_store, name, model)
    return rw_stream, ro_stream


def test_event_stream_key(data_store):
    model = data_store._stream_model(encoding={"type": "json"})
    stream = EventStream.open(data_store, "my_stream", model)
    assert stream.name == "my_stream"
    assert re.match("^esrf:stream:[A-Z0-9]{26}$", stream.key)


@pytest.mark.parametrize("dtype", [int, float, np.uint32])
def test_event_stream_type(data_store, dtype):
    input = np.arange(1000, dtype=dtype)
    rw_stream, ro_stream = event_stream_pair(data_store, input.dtype)
    assert rw_stream.dtype == dtype
    assert ro_stream.dtype == dtype

    rw_stream.send(input)
    rw_stream.seal()
    output = ro_stream[:]
    assert input.dtype == dtype
    assert output.dtype == dtype
    assert np.array_equal(input, output)


def test_event_stream_invalid_type(data_store):
    rw_stream, _ = event_stream_pair(data_store, np.int32)

    # invalid size
    with pytest.raises(TypeError):
        rw_stream.send(np.int16(5))

    # invalid kind
    with pytest.raises(TypeError):
        rw_stream.send(np.float32(5))


def test_event_stream_write_permission(data_store):
    input = np.arange(1000)
    rw_stream, ro_stream = event_stream_pair(data_store, input.dtype)
    rw_stream.send(input)
    rw_stream.seal()

    # both ro_stream and rw_stream can read
    assert np.array_equal(input, rw_stream[:])
    assert np.array_equal(input, ro_stream[:])

    # ro_stream can't send nor seal the stream
    with pytest.raises(NoWritePermission):
        ro_stream.send(input)
    with pytest.raises(NoWritePermission):
        ro_stream.seal()


def test_event_stream_unknown_encoding(data_store):
    model = data_store._stream_model(encoding={"type": "teapot"})
    with pytest.raises(UnknownEncodingError):
        EventStream.create(data_store, "my_stream", model)


def test_multiple_calls_to_seal(data_store):
    input = np.arange(1000)
    rw_stream, _ = event_stream_pair(data_store, input.dtype)
    rw_stream.send(input)

    # make a second writing stream to test re-sealing from an unaware object
    external_writer = EventStream.create(data_store, rw_stream.name, rw_stream._model)

    assert rw_stream.seal() == 1000
    assert rw_stream.seal() == 1000
    assert external_writer.seal() == 1000
    assert external_writer.seal() == 1000


def test_event_stream_chunk_slicing(data_store):
    data = np.arange(100)
    rw_stream, ro_stream = event_stream_pair(data_store, data.dtype)

    # produce chunks of different sizes
    rw_stream.send(data[0])  # 1
    rw_stream.send(data[1:3])  # 2
    rw_stream.send(data[3:6])  # 3
    rw_stream.send(data[6:10])  # 4
    rw_stream.send(data[10:20])  # 10
    rw_stream.send(data[20:50])  # 30
    rw_stream.send(data[50:])  # 50
    rw_stream.join()
    assert len(ro_stream) == 100

    assert np.array_equal(data[0], ro_stream[0])
    assert np.array_equal(data[42], ro_stream[42])
    assert np.array_equal(data[:10], ro_stream[:10])
    assert np.array_equal(data[20:30], ro_stream[20:30])
    assert np.array_equal(data[30:20], ro_stream[30:20])
    assert np.array_equal(data[0:10:3], ro_stream[0:10:3])

    data2 = np.arange(100, 200)

    # produce chunks of different sizes
    rw_stream.send(data2[0:50])  # 50
    rw_stream.send(data2[50:80])  # 30
    rw_stream.send(data2[80:90])  # 10
    rw_stream.send(data2[90:94])  # 4
    rw_stream.send(data2[94:97])  # 3
    rw_stream.send(data2[97:99])  # 2
    rw_stream.send(data2[99])  # 1

    rw_stream.seal()
    assert len(ro_stream) == 200

    assert np.array_equal(data2[0], ro_stream[100])
    assert np.array_equal(data2[-1], ro_stream[-1])
    assert np.array_equal(np.concatenate((data, data2)), ro_stream[:])
    assert np.array_equal(np.concatenate((data, data2))[50:150], ro_stream[50:150])


def test_event_stream_negative_index_sealed(data_store):
    data = np.arange(10)

    rw_stream, ro_stream = event_stream_pair(data_store, data.dtype)
    rw_stream.send(data[0:2])
    rw_stream.send(data[2])
    rw_stream.send(data[3:7])
    rw_stream.send(data[7:10])
    rw_stream.seal()
    assert len(ro_stream) == 10

    for i in range(-15, 15):
        if -10 <= i < 10:
            assert np.array_equal(data[i], ro_stream[i])
        else:
            with pytest.raises(IndexError):
                ro_stream[i]

    for i in range(-15, 15):
        for j in range(-15, 15):
            assert np.array_equal(data[i:j], ro_stream[i:j])


def test_event_stream_negative_index_unsealed(data_store):
    data = np.arange(10)

    rw_stream, ro_stream = event_stream_pair(data_store, data.dtype)
    rw_stream.send(data[0:2])
    rw_stream.send(data[2])
    rw_stream.send(data[3:7])
    rw_stream.send(data[7:10])
    rw_stream.join()
    assert len(ro_stream) == 10

    for i in range(-15, 15):
        if 0 <= i < 10:
            assert np.array_equal(data[i], ro_stream[i])
        else:
            with pytest.raises(IndexNotYetThereError):
                ro_stream[i]

    for i in range(-15, 15):
        for j in range(-15, 15):
            if i < 0 or j < 0:
                with pytest.raises(IndexNotYetThereError):
                    ro_stream[i:j]
            else:
                assert np.array_equal(data[i:j], ro_stream[i:j])

    for i in range(-15, 15):
        if i < 0:
            with pytest.raises(IndexNotYetThereError):
                ro_stream[i:]
        else:
            assert np.array_equal(data[i:], ro_stream[i:])

    rw_stream.seal()


def test_event_stream_not_yet_there_index(data_store):
    # fixing issue https://gitlab.esrf.fr/bliss/bliss/-/issues/4389

    rw_stream, ro_stream = event_stream_pair(data_store, int)

    def send_soon():
        for i in range(1000):
            rw_stream.send(i)

    t = threading.Thread(target=send_soon)
    t.start()
    try:
        i = 0
        while i < 1000:
            try:
                ro_stream[i]
                i += 1
            except IndexNotYetThereError:
                # if the race condition exists, an IndexNoMoreThereError is
                # likely to be raised instead (not always).
                pass
    finally:
        rw_stream.seal()
        t.join()


@pytest.mark.timeout(10)
def test_wait_seal(data_store):
    rw_stream, ro_stream = event_stream_pair(data_store, int)

    def seal_soon():
        time.sleep(0.1)
        rw_stream.seal()

    assert not ro_stream.wait_seal(timeout=0.01)
    assert not ro_stream.is_sealed()

    t = threading.Thread(target=seal_soon)
    t.start()
    try:
        assert ro_stream.wait_seal()
        assert ro_stream.is_sealed()
    finally:
        t.join()
