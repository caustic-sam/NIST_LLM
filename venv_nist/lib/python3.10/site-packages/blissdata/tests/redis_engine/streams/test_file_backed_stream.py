import pytest
import numpy as np

from blissdata.streams.base import EventRange
from blissdata.streams.hdf5_fallback import Hdf5BackedStream
from blissdata.redis_engine.encoding.numeric import NumericStreamEncoder
from blissdata.redis_engine.event_stream import EventStream
from blissdata.redis_engine.exceptions import (
    IndexNoMoreThereError,
    IndexNotYetThereError,
    IndexWontBeThereError,
)


@pytest.fixture
def fb_stream(data_store):
    encoder = NumericStreamEncoder("int")
    model = data_store._stream_model(encoding=encoder.info(), info={"data_path": "foo"})
    event_stream = EventStream.open(data_store, "test_stream", model)
    return Hdf5BackedStream(event_stream, file_path="bar")


def test_stream_definition(data_store, dummy_id):
    # TODO file_back_stream should not depend on scan info (but need backward compat)
    scan = data_store.create_scan(identity=dummy_id, info={"save": True})
    stream_definition = Hdf5BackedStream.make_definition(
        name="test_stream",
        file_path="/dummy/file/path",
        data_path="/dummy/data/path",
        dtype="float",
        shape=(),
    )
    rw_stream = scan.create_stream(stream_definition)
    assert isinstance(rw_stream, Hdf5BackedStream)

    scan.prepare()
    rw_stream.seal()
    loaded_scan = data_store.load_scan(scan.key)
    ro_stream = loaded_scan.streams["test_stream"]
    assert isinstance(ro_stream, Hdf5BackedStream)


def test_getitem(mocker, fb_stream):
    data = list(range(50))

    def get_data(key):
        return data[key]

    event_mock = mocker.patch.object(EventStream, "__getitem__", wraps=get_data)
    file_mock = mocker.patch.object(Hdf5BackedStream, "_get_from_file", wraps=get_data)

    # when events are available, Hdf5BackedStream act as a passthrough
    assert fb_stream[0] == data[0]
    assert fb_stream[-1] == data[-1]
    assert fb_stream[:] == data[:]
    assert fb_stream[15:-3] == data[15:-3]

    assert event_mock.call_count == 4
    assert file_mock.call_count == 0

    event_mock.reset_mock()
    file_mock.reset_mock()

    event_mock.side_effect = IndexNoMoreThereError

    # events_stream now raises IndexNoMoreThereError, fallback to file
    assert fb_stream[0] == data[0]
    assert fb_stream[-1] == data[-1]
    assert fb_stream[:] == data[:]
    assert fb_stream[15:-3] == data[15:-3]

    assert event_mock.call_count == 4
    assert file_mock.call_count == 4

    event_mock.side_effect = IndexNotYetThereError
    with pytest.raises(IndexNotYetThereError):
        fb_stream[0]

    event_mock.side_effect = IndexWontBeThereError
    with pytest.raises(IndexWontBeThereError):
        fb_stream[0]


def test_view_data(fb_stream):
    view = fb_stream._build_view_from_events(
        index=5,
        events=EventRange(5, 0, [5, 6, 7], False),
        last_only=False,
    )
    assert view.index == 5
    assert len(view) == 3
    assert np.array_equal(view.get_data(), [5, 6, 7])

    # expect same result while reaching end of stream
    view = fb_stream._build_view_from_events(
        index=5,
        events=EventRange(5, 0, [5, 6, 7], True),
        last_only=False,
    )
    assert view.index == 5
    assert len(view) == 3
    assert np.array_equal(view.get_data(), [5, 6, 7])


def test_view_expired_data(mocker, fb_stream):
    file_mock = mocker.patch.object(
        Hdf5BackedStream, "_get_from_file", return_value=[5, 6, 7]
    )
    view = fb_stream._build_view_from_events(
        index=5,
        events=EventRange(5, 3, [], False),
        last_only=False,
    )
    assert view.index == 5
    assert len(view) == 3
    assert np.array_equal(view.get_data(), [5, 6, 7])
    file_mock.assert_called_once_with(slice(5, 8))


def test_view_partially_expired_data(mocker, fb_stream):
    file_mock = mocker.patch.object(
        Hdf5BackedStream, "_get_from_file", return_value=[5, 6, 7]
    )
    view = fb_stream._build_view_from_events(
        index=5,
        events=EventRange(5, 3, [8, 9], False),
        last_only=False,
    )
    assert view.index == 5
    assert len(view) == 5
    assert np.array_equal(view.get_data(), [5, 6, 7, 8, 9])
    file_mock.assert_called_once_with(slice(5, 8))


def test_view_last_only(mocker, fb_stream):
    view = fb_stream._build_view_from_events(
        index=5,
        events=EventRange(10, 0, [10, 11, 12], False),
        last_only=True,
    )
    assert view.index == 10
    assert len(view) == 3
    assert np.array_equal(view.get_data(), [10, 11, 12])
