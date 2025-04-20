import pytest
import numpy
import redis
from blissdata.redis_engine.scan import ScanState
from blissdata.streams.base import Stream
from blissdata.redis_engine.exceptions import (
    ScanNotFoundError,
    UnauthorizeStateTransition,
)


def test_creation(data_store, dummy_id):
    rw_scan = data_store.create_scan(dummy_id)
    ro_scan = data_store.load_scan(rw_scan.key)
    assert ro_scan.state is ScanState.CREATED
    for key, val in dummy_id.items():
        assert getattr(ro_scan, key) == val


def test_json_size_exception(data_store, dummy_id):
    with pytest.raises(RuntimeError) as exc_info:
        _ = data_store.create_scan(dummy_id, info={"large_key": "X" * 2**20})
    assert "metadata is taking" in str(exc_info)
    _ = data_store.create_scan(dummy_id, info={"large_key": "X" * (2**19)})


@pytest.mark.parametrize("force", [True, False])
def test_deletion(data_store, dummy_id, force):
    rw_scan = data_store.create_scan(dummy_id)

    if force:
        data_store.delete_scan(rw_scan.key, force=force)
    else:
        with pytest.raises(RuntimeError):  # TODO choose a more specific exception
            data_store.delete_scan(rw_scan.key, force=force)
        # terminate the scan first to delete smoothly
        rw_scan.close()
        data_store.delete_scan(rw_scan.key, force=force)

    # scan appears to be deleted
    with pytest.raises(ScanNotFoundError):
        data_store.load_scan(rw_scan.key)

    # scan content is actually set to expire, allowing functions that
    # have not yet realized the scan is deleted to terminate.
    assert data_store._redis.ttl(rw_scan.key) > 0
    assert data_store._redis.ttl(rw_scan._model.state_stream.key()) > 0
    for stream in rw_scan.streams.values():
        assert data_store._redis.ttl(stream.key) > 0


def test_state_transition(data_store, dummy_id):
    rw_scan = data_store.create_scan(dummy_id)

    ro_scan = data_store.load_scan(rw_scan.key)
    assert ro_scan.state is ScanState.CREATED

    rw_scan.prepare()

    # ro_scan is not aware of the change until it updates
    assert ro_scan.state is ScanState.CREATED
    assert ro_scan.update()
    assert ro_scan.state is ScanState.PREPARED


def test_forbidden_state_transition(data_store, dummy_id):
    rw_scan = data_store.create_scan(dummy_id)

    ro_scan = data_store.load_scan(rw_scan.key)
    assert ro_scan.state is ScanState.CREATED

    with pytest.raises(UnauthorizeStateTransition):
        rw_scan.stop()

    # transition blocked, nothing has changed
    assert ro_scan.state is ScanState.CREATED
    assert not ro_scan.update(timeout=0.1)
    assert ro_scan.state is ScanState.CREATED


def test_distributed_stream_publishing(data_store, dummy_id, caplog):
    # Create a scan and create a stream in it
    rw_scan = data_store.create_scan(dummy_id)
    stream_definition = Stream.make_definition("shared_stream", int)
    local_stream = rw_scan.create_stream(stream_definition)
    rw_scan.prepare()

    # from another place... load the scan and open its stream in RW mode
    ro_scan = data_store.load_scan(rw_scan.key)
    external_stream = ro_scan.get_writer_stream("shared_stream")

    rw_scan.start()

    # local_stream and external_stream are two instances of the same stream,
    # both having write permission.

    external_stream.send(1)
    external_stream.send(2)
    external_stream.send(3)
    external_stream.join()

    # Sending to local_stream will fail due to index discrepancy, but because of
    # stream's internal buffer mechanism, exception won't be raised immediately.
    local_stream.send(42)
    with pytest.raises(redis.exceptions.ResponseError):
        local_stream.join()

    # local_stream can still be used to seal the stream
    assert local_stream.seal() == 3  # local_stream.seal succeeds, but with a warning
    assert (
        "Cannot publish into stream 'shared_stream' due to index discrepancy"
        in caplog.text
    )
    assert external_stream.seal() == 3

    assert local_stream.is_sealed()
    assert external_stream.is_sealed()
    numpy.testing.assert_array_equal(local_stream[:], [1, 2, 3])
    numpy.testing.assert_array_equal(external_stream[:], [1, 2, 3])


# def test_update_block(data_store, rw_scan):
#     pass
#
# def test_update_timeout(data_store, rw_scan):
#     pass
#
# def test_update_no_block(data_store, rw_scan):
#     pass
