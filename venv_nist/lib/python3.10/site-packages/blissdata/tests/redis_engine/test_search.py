import re
import time
import pytest
from threading import Thread, Semaphore
from blissdata.redis_engine.exceptions import NoScanAvailable


def test_search_empty_db(data_store):
    ts, scans = data_store.search_existing_scans()
    assert ts == "0-0"
    assert scans == []

    ts, scans = data_store.search_existing_scans(name="abc*")
    assert ts == "0-0"
    assert scans == []


def test_search_all_scans(data_store):
    scan = data_store.create_scan(
        identity={
            "name": "scan",
            "number": 1,
            "data_policy": "esrf",
            "session": "a",
            "proposal": "b",
            "collection": "c",
            "dataset": "d",
        }
    )
    assert re.match("^esrf:scan:[A-Z0-9]{26}$", scan.key)

    ts, scans_found = data_store.search_existing_scans()
    millis, seq = [int(item) for item in ts.split("-")]
    assert millis > 0
    assert seq == 0
    assert len(scans_found) == 1
    assert scan.key == scans_found[0]

    # ensure next ts won't be on the same millisecond
    time.sleep(0.001)

    scans = [
        data_store.create_scan(
            identity={
                "name": "scan",
                "number": i,
                "data_policy": "esrf",
                "session": "a",
                "proposal": "b",
                "collection": "c",
                "dataset": "d",
            }
        )
        for i in range(1, 4)
    ]
    ts, scans_found = data_store.search_existing_scans()
    next_millis, _ = [int(item) for item in ts.split("-")]
    assert next_millis > millis
    assert len(scans_found) == 4
    for scan in scans:
        assert re.match("^esrf:scan:[A-Z0-9]{26}$", scan.key)
        assert scan.key in scans_found


def test_search_specific_scans(data_store):
    for i in range(0, 20):
        data_store.create_scan(
            identity={
                "name": "scan",
                "number": i,
                "data_policy": "esrf",
                "session": f"session{i % 2}",
                "proposal": f"proposal{i}",
                "collection": f"collection{i}",
                "dataset": f"dataset{i}",
            }
        )

    ts, scans_found = data_store.search_existing_scans()
    assert len(scans_found) == 20

    ts, scans_found = data_store.search_existing_scans(dataset="dataset")
    assert len(scans_found) == 0
    ts, scans_found = data_store.search_existing_scans(dataset="dataset1")
    assert len(scans_found) == 1
    ts, scans_found = data_store.search_existing_scans(dataset="dataset1*")
    assert len(scans_found) == 11  # scan1, scan10, scan11, ... scan19

    ts, scans_found = data_store.search_existing_scans(number=1, session="session0")
    assert len(scans_found) == 0
    ts, scans_found = data_store.search_existing_scans(number=0, session="session0")
    assert len(scans_found) == 1
    ts, scans_found = data_store.search_existing_scans(
        dataset="dataset1*", session="session0"
    )
    assert len(scans_found) == 5  # scan10, scan12, ... scan18
    ts, scans_found = data_store.search_existing_scans(
        dataset="dataset*", session="session*"
    )
    assert len(scans_found) == 20

    # FAIL
    # ts, scans_found = data_store.search_existing_scans(name="*")
    # ts, scans_found = data_store.search_existing_scans(name="sca*n1")

    # play with MAXPREFIXEPANSIONS ? should be reset correctly after test


def test_get_next_scan_empty_db(data_store):
    with pytest.raises(NoScanAvailable):
        data_store.get_next_scan(block=False)

    with pytest.raises(NoScanAvailable):
        data_store.get_next_scan(timeout=0.1)


def test_get_next_scan_missed(data_store, dummy_id):
    _ = data_store.create_scan(dummy_id)
    with pytest.raises(NoScanAvailable):
        data_store.get_next_scan(block=False)


def test_get_next_scan_deleted(data_store, dummy_id):
    scan = data_store.create_scan(dummy_id)
    data_store.delete_scan(scan.key, force=True)
    with pytest.raises(NoScanAvailable):
        data_store.get_next_scan(block=False, since=0)


def test_get_next_scan_blocking(data_store, dummy_id):
    sem = Semaphore(0)

    def monitor_redis():
        """monitor redis db to see when get_next_scan() send blocking call to redis"""
        with data_store._redis.monitor() as mo:
            sem.release()
            for cmd in mo.listen():
                if cmd["command"].startswith("XREAD"):
                    sem.release()
                    return

    retval = []

    def wait_for_scan():
        ts, key = data_store.get_next_scan()
        retval.append(key)

    monitoring_thread = Thread(target=monitor_redis)
    monitoring_thread.start()
    sem.acquire()  # monitoring of redis is ready

    # start a thread waiting for scan
    scan_waiting_thread = Thread(target=wait_for_scan)
    scan_waiting_thread.start()

    sem.acquire()  # monitor saw get_next_scan() querying redis
    monitoring_thread.join()

    # Create a scan to unlock get_next_scan()
    scan = data_store.create_scan(dummy_id)

    scan_waiting_thread.join(timeout=5)
    assert retval[0] == scan.key
    assert not scan_waiting_thread.is_alive()


def test_get_next_scan_skip_deleted(data_store, dummy_id):
    scans = [data_store.create_scan(dummy_id) for _ in range(5)]
    data_store.delete_scan(scans[0].key, force=True)
    data_store.delete_scan(scans[2].key, force=True)

    ts, key = data_store.get_next_scan(since="0-0")
    assert key == scans[1].key
    ts, key = data_store.get_next_scan(since=ts)
    assert key == scans[3].key
    ts, key = data_store.get_next_scan(since=ts)
    assert key == scans[4].key

    ts, key = data_store.get_next_scan(timeout=1, since="0-0")
    assert key == scans[1].key
    ts, key = data_store.get_next_scan(timeout=1, since=ts)
    assert key == scans[3].key
    ts, key = data_store.get_next_scan(timeout=1, since=ts)
    assert key == scans[4].key

    ts, key = data_store.get_next_scan(block=False, since="0-0")
    assert key == scans[1].key
    ts, key = data_store.get_next_scan(block=False, since=ts)
    assert key == scans[3].key
    ts, key = data_store.get_next_scan(block=False, since=ts)
    assert key == scans[4].key


def test_get_last_scan(data_store, dummy_id):
    scans = [data_store.create_scan(dummy_id) for _ in range(5)]
    ts, key = data_store.get_last_scan()
    assert key == scans[-1].key


def test_get_last_scan_empty_db(data_store):
    with pytest.raises(NoScanAvailable):
        data_store.get_last_scan()


def test_get_last_scan_deleted(data_store, dummy_id):
    scan = data_store.create_scan(dummy_id)
    data_store.delete_scan(scan.key, force=True)
    with pytest.raises(NoScanAvailable):
        data_store.get_last_scan()


def test_get_last_scan_skip_deleted(data_store, dummy_id):
    scans = [data_store.create_scan(dummy_id) for _ in range(5)]
    data_store.delete_scan(scans[-1].key, force=True)
    ts, key = data_store.get_last_scan()
    assert key == scans[-2].key
