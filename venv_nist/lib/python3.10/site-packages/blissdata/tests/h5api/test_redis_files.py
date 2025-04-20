import pytest
import threading
import time
import numpy as np

from blissdata.h5api.redis_hdf5 import File, Group
from blissdata.streams.base import Stream


def test_file_open():
    with pytest.raises(ValueError) as exc_info:
        _ = File("dummy_path")
    assert "No DataStore provided" in str(exc_info)
    assert "$BEACON_HOST is not specified" in str(exc_info)


@pytest.mark.timeout(30)
def test_file_iter(data_store, tmpdir):
    file_path = str(tmpdir / "data.h5")
    file = File(file_path, data_store)
    assert len(file) == 0

    # listen for three scans in a thread
    def iter_over_three_scans(keys: list):
        for key in file:
            keys.append(key)
            assert key == f"{len(keys)}.1"
            if len(keys) == 3:
                break

    keys = []
    t = threading.Thread(target=iter_over_three_scans, args=(keys,))
    t.start()

    # run three scans
    for i in range(3):
        scan = data_store.create_scan(
            identity={
                "name": "empty_scan",
                "number": i + 1,
                "data_policy": "None",
                "path": file_path,
            },
        )
        scan.info["mapping"] = {
            "type": "group",
            "items": {f"{i + 1}.1": {"type": "group", "items": {}}},
        }

        scan.close()

    # ensure the iterator got them all
    t.join()
    assert len(keys) == 3
    assert len(file) == 3


def test_file_getitem(data_store, tmpdir):
    file_path = str(tmpdir / "data.h5")
    file = File(file_path, data_store)

    # no such scan
    with pytest.raises(KeyError):
        _ = file["1.1"]

    scan = data_store.create_scan(
        identity={
            "name": "empty_scan",
            "number": 1,
            "data_policy": "None",
            "path": file_path,
        }
    )
    scan.info["mapping"] = {
        "type": "group",
        "items": {"1.1": {"type": "group", "items": {}}},
    }

    scan.prepare()

    # scan found
    group = file["1.1"]
    assert isinstance(group, Group)

    _ = data_store.create_scan(
        identity={
            "name": "empty_scan",
            "number": 2,
            "data_policy": "None",
            "path": str(tmpdir / "another_file.h5"),
        }
    )

    # Scan 2 not in that file
    with pytest.raises(KeyError):
        _ = file["2.1"]


def test_missing_scan_mapping(data_store, tmpdir):
    file_path = str(tmpdir / "data.h5")
    file = File(file_path, data_store)

    scan = data_store.create_scan(
        identity={
            "name": "empty_scan",
            "number": 1,
            "data_policy": "None",
            "path": file_path,
        }
    )
    scan.prepare()

    with pytest.raises(KeyError):
        _ = file["1.1"]


def test_scan_iter(data_store, tmpdir):
    file_path = str(tmpdir / "data.h5")
    file = File(file_path, data_store)

    scan = data_store.create_scan(
        identity={
            "name": "scan_with_mapping",
            "number": 1,
            "data_policy": "None",
            "path": file_path,
        }
    )
    scan.info["mapping"] = {
        "type": "group",
        "items": {
            "1.1": {
                "type": "group",
                "items": {
                    "abc": {"type": "group", "items": {}},
                    "def": {"type": "group", "items": {}},
                    "ghi": {"type": "group", "items": {}},
                },
            }
        },
    }

    scan.prepare()

    assert {key for key in file[f"{scan.number}.1"]} == {"abc", "def", "ghi"}


def test_scan_getitem(data_store, tmpdir):
    file_path = str(tmpdir / "data.h5")
    file = File(file_path, data_store)

    scan = data_store.create_scan(
        identity={
            "name": "scan_with_mapping",
            "number": 1,
            "data_policy": "None",
            "path": file_path,
        }
    )
    scan.info["mapping"] = {
        "type": "group",
        "items": {
            "1.1": {
                "type": "group",
                "items": {
                    "abc": {
                        "type": "group",
                        "items": {
                            "def": {
                                "type": "group",
                                "items": {"ghi": {"type": "group", "items": {}}},
                            }
                        },
                    },
                    "xyz": {"type": "group", "items": {}},
                },
            }
        },
    }

    scan.prepare()

    group = file[f"{scan.number}.1"]
    assert len(group) == 2

    test_path = f"/{scan.number}.1/abc/def/ghi"
    assert group["abc/def/ghi"].name == test_path
    assert group["abc"]["def"]["ghi"].name == test_path
    assert group[f"/{scan.number}.1/abc/def/ghi"].name == test_path
    assert file[f"/{scan.number}.1/abc/def/ghi"].name == test_path

    with pytest.raises(KeyError) as exc_info:
        group["abc/ERR/def/ghi"]
    # assert exc_info.value.args[0] == "No such path: /1.1/abc/ERR" # TODO
    assert exc_info.value.args[0] == "No such path: /1.1/abc/ERR/def/ghi"

    # edit mapping on scan closing
    scan.start()
    scan.stop()
    scan.info["mapping"]["items"]["1.1"]["items"]["xyz"]["items"]["uvw"] = {
        "type": "group",
        "items": {},
    }
    scan.close()

    # try to access new group until available
    start = time.perf_counter()
    while True:
        try:
            result = group[f"/{scan.number}.1/xyz/uvw"]
            print("found", result)
            break
        except KeyError as e:
            if time.perf_counter() < start + 5:
                print(e)
                time.sleep(0.05)
            else:
                raise


def test_get_dataset(data_store, tmpdir):
    file_path = str(tmpdir / "data.h5")
    file = File(file_path, data_store)

    scan = data_store.create_scan(
        identity={
            "name": "test_scan",
            "number": 1,
            "data_policy": "None",
            "path": file_path,
        }
    )

    stream_definition_a = Stream.make_definition("abcd", np.float64)
    stream_definition_b = Stream.make_definition("efgh", np.int32)
    stream1 = scan.create_stream(stream_definition_a)
    stream2 = scan.create_stream(stream_definition_b)

    scan.info["mapping"] = {
        "type": "group",
        "items": {
            "1.1": {
                "type": "group",
                "items": {
                    "dset1": {
                        "type": "dset",
                        "stream": "abcd",
                    },
                    "dset2": {
                        "type": "dset",
                        "stream": "efgh",
                    },
                    "dset3": {
                        "type": "dset",
                        "value": 42,
                    },
                    "dset4": {
                        "type": "dset",
                        "stream": "missing stream",
                    },
                },
            },
        },
    }

    scan.prepare()
    scan.start()
    data1 = np.array([1.0, 2.0, 3.0, 4.0, 5.0], dtype=np.float64)
    data2 = np.array([10, 20, 30, 40, 50], dtype=np.int32)
    stream1.send(data1)
    stream2.send(data2)
    scan.close()

    dset1 = file[f"/{scan.number}.1/dset1"]
    dset2 = file[f"/{scan.number}.1/dset2"]
    dset3 = file[f"/{scan.number}.1/dset3"]
    with pytest.raises(KeyError):
        _ = file[f"/{scan.number}.1/dset4"]

    assert np.array_equal(dset1[:], data1)
    assert np.array_equal(dset2[:], data2)
    assert np.array_equal(dset3[()], 42)
    assert dset1.dtype == np.dtype("float64")
    assert dset2.dtype == np.dtype("int32")
    # assert dset3.dtype == np.dtype("int64") # TODO
    assert dset1.shape == (5,)
    assert dset2.shape == (5,)
