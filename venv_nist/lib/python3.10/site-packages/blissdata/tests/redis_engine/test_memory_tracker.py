import re
import time
import redis
import pytest
import numpy as np
from contextlib import contextmanager
from subprocess import Popen, PIPE, STDOUT
from blissdata.redis_engine.exceptions import IndexNoMoreThereError, ScanNotFoundError
from blissdata.streams.base import Stream

from .utils import redis_config_ctx


@contextmanager
def memory_tracker_ctx(redis_url, tracker_opts={}, redis_config={}):
    """Start a memory_tracker process and apply config to redis DB (like 'maxmemory').
    On exit, process is killed and redis DB config is restored.
    """
    with redis_config_ctx(redis_url, redis_config):
        flat_args = [arg for key_value in tracker_opts.items() for arg in key_value]
        with Popen(
            ["memory_tracker"] + flat_args,
            text=True,
            stdin=PIPE,
            stdout=PIPE,
            stderr=STDOUT,
        ) as proc:
            try:
                yield proc
            finally:
                proc.kill()


def test_usage(redis_url):
    with memory_tracker_ctx(redis_url) as proc:
        output, _ = proc.communicate(timeout=5)
        expected = """\
usage: memory_tracker [-h] --redis-url REDIS_URL
                      [--cleaning-threshold CLEANING_THRESHOLD]
                      [--protected-history PROTECTED_HISTORY]
                      [--monitoring-period MONITORING_PERIOD]
                      [--inactive-scan-deletion INACTIVE_SCAN_DELETION]
                      [--closed-scan-deletion CLOSED_SCAN_DELETION]
                      [--cleaning-time-slice CLEANING_TIME_SLICE]
                      [--init-db]
memory_tracker: error: the following arguments are required: --redis-url
"""
        # normalize spaces and returns to not depend on terminal width
        output = " ".join(output.split())
        expected = " ".join(expected.split())

        assert expected in output
        assert proc.returncode is not None  # it is terminated
        assert proc.returncode != 0  # it exited with an error


def test_no_maxmemory_set(redis_url):
    with memory_tracker_ctx(redis_url, tracker_opts={"--redis-url": redis_url}) as proc:
        output, _ = proc.communicate(timeout=5)
        assert (
            "ERROR: Redis database has no 'maxmemory' configured, please update Redis configuration."
            in output
        )
        assert proc.returncode is not None  # it is terminated
        assert proc.returncode != 0  # it exited with an error


@pytest.mark.timeout(10)
def test_startup(redis_url):
    with memory_tracker_ctx(
        redis_url,
        tracker_opts={
            "--redis-url": redis_url,
            "--cleaning-threshold": "72",
            "--protected-history": "321",
            "--monitoring-period": "23",
            "--inactive-scan-deletion": "80000",
            "--closed-scan-deletion": "200000",
        },
        redis_config={"maxmemory": "23MB", "maxmemory-policy": "noeviction"},
    ) as proc:
        while True:
            # skip possible warnings
            if "Redis max. memory: 23MB" in proc.stdout.readline():
                break
        assert "Mem. usage to trigger cleanup: 72%" in proc.stdout.readline()
        assert "Protected history length: 321 seconds" in proc.stdout.readline()


@pytest.mark.timeout(10)
def test_tracking_scan(redis_url, data_store, dummy_id):
    with memory_tracker_ctx(
        redis_url,
        tracker_opts={
            "--redis-url": redis_url,
            "--monitoring-period": "1",
            "--closed-scan-deletion": "100000",
        },
        redis_config={"maxmemory": "16MB", "maxmemory-policy": "noeviction"},
    ) as proc:

        # wait for memory tracker to start
        while True:
            line = proc.stdout.readline()
            print(line, end="")
            if "Memory usage" in line:
                break

        scan = data_store.create_scan(dummy_id)

        # wait for tracker to find the scan
        while True:
            line = proc.stdout.readline()
            print(line, end="")
            if f"Start tracking {scan.key}" in line:
                break

        # run the scan
        stream_definition_a = Stream.make_definition("abcd", "int")
        stream_definition_b = Stream.make_definition("efgh", "int")
        stream1 = scan.create_stream(stream_definition_a)
        stream2 = scan.create_stream(stream_definition_b)
        scan.prepare()
        scan.start()
        stream1.send(12345)
        stream2.send(54321)
        scan.stop()
        scan.close()

        # wait for tracker to see scan termination
        while True:
            line = proc.stdout.readline()
            print(line, end="")
            if (
                f"Scan {scan.key} is terminated, expiration set to 1 day, 3:46:40 seconds"
                in line
            ):
                break

        # Check expiration is applied to the scan and all of its streams
        assert 0 < data_store._redis.ttl(scan.key) <= 100000
        assert 0 < data_store._redis.ttl(scan._model.state_stream.key()) <= 100000
        for stream in scan.streams.values():
            assert 0 < data_store._redis.ttl(stream.key) <= 100000


@pytest.mark.timeout(30)
def test_tracking_multiple_scans(redis_url, data_store, dummy_id):
    def run_dummy_scan():
        scan = data_store.create_scan(dummy_id)
        stream_definition_a = Stream.make_definition("abcd", "int")
        stream_definition_b = Stream.make_definition("efgh", "int")
        stream1 = scan.create_stream(stream_definition_a)
        stream2 = scan.create_stream(stream_definition_b)
        scan.prepare()
        scan.start()
        for i in range(100):
            stream1.send(i)
            stream2.send(-i)
        scan.stop()
        scan.close()
        return scan

    with memory_tracker_ctx(
        redis_url,
        tracker_opts={
            "--redis-url": redis_url,
            "--monitoring-period": "1",
            "--closed-scan-deletion": "100000",
        },
        redis_config={"maxmemory": "32MB", "maxmemory-policy": "noeviction"},
    ) as proc:
        while "Memory usage" not in proc.stdout.readline():
            pass

        scans = [run_dummy_scan() for _ in range(100)]

        key_regex = re.compile(r"\besrf\:scan\:\w+")
        scan_keys = {scan.key for scan in scans}
        tracked_keys = set()
        expired_keys = set()
        while scan_keys != expired_keys:
            line = proc.stdout.readline()
            print(line, end="")
            if "Start tracking" in line:
                key = key_regex.findall(line)[0]
                assert key in scan_keys
                tracked_keys.add(key)
            elif "is terminated" in line:
                key = key_regex.findall(line)[0]
                assert key in tracked_keys
                expired_keys.add(key_regex.findall(line)[0])

                # Check expiration is applied to the scan and all of its streams
                scan = data_store.load_scan(key)
                assert 0 < data_store._redis.ttl(key) <= 100000
                assert (
                    0 < data_store._redis.ttl(scan._model.state_stream.key()) <= 100000
                )
                for stream in scan.streams.values():
                    assert 0 < data_store._redis.ttl(stream.key) <= 100000


@pytest.mark.timeout(10)
def test_delete_inactive_scan(redis_url, data_store, dummy_id):
    with memory_tracker_ctx(
        redis_url,
        tracker_opts={
            "--redis-url": redis_url,
            "--monitoring-period": "1",
            "--inactive-scan-deletion": "2",
        },
        redis_config={"maxmemory": "16MB", "maxmemory-policy": "noeviction"},
    ) as proc:

        # wait for memory tracker to start
        while True:
            line = proc.stdout.readline()
            print(line, end="")
            if "Memory usage" in line:
                break

        inactive_scan = data_store.create_scan(dummy_id)
        active_scan = data_store.create_scan(dummy_id)

        stream_definition = Stream.make_definition("mystream", "int")
        stream = active_scan.create_stream(stream_definition)

        active_scan.prepare()
        active_scan.start()

        # wait for tracker to find the scan
        nb_scans_to_track = 2
        while nb_scans_to_track:
            stream.send(1)
            line = proc.stdout.readline()
            print(line, end="")
            if "Start tracking" in line:
                nb_scans_to_track -= 1

        while True:
            stream.send(2)
            line = proc.stdout.readline()
            print(line, end="")
            if f"{inactive_scan.key} was inactive" in line:
                break

        for _ in range(4):
            stream.send(3)
            line = proc.stdout.readline()
            print(line, end="")
            assert f"{active_scan.key} was inactive" not in line
        data_store.load_scan(active_scan.key)

        # inactive scan is no longer accessible
        with pytest.raises(ScanNotFoundError):
            data_store.load_scan(inactive_scan.key)

        # Check deletion (which is a 10 seconds expiration) is applied to inactive_scan and all of its streams
        assert 0 < data_store._redis.ttl(inactive_scan.key) <= 10
        assert 0 < data_store._redis.ttl(inactive_scan._model.state_stream.key()) <= 10
        for stream in inactive_scan.streams.values():
            assert 0 < data_store._redis.ttl(stream.key) <= 10


@pytest.mark.timeout(60)
def test_manual_scan_deletion(redis_url, data_store, dummy_id):
    """Manually deleted scans should not disrupt memory tracking"""
    with memory_tracker_ctx(
        redis_url,
        tracker_opts={
            "--redis-url": redis_url,
            "--monitoring-period": "1",
        },
        redis_config={"maxmemory": "16MB", "maxmemory-policy": "noeviction"},
    ) as proc:

        # wait for memory tracker to start
        while True:
            line = proc.stdout.readline()
            print(line, end="")
            if "Memory usage" in line:
                break

        while True:
            scan = data_store.create_scan(dummy_id)

            # wait for tracker to find the scan
            while True:
                line = proc.stdout.readline()
                print(line, end="")
                if f"Start tracking {scan.key}" in line:
                    break

            data_store.delete_scan(scan.key, force=True)

            while True:
                line = proc.stdout.readline()
                print(line, end="")
                if f"{scan.key} has been deleted" in line:
                    print("Success, tracker stopped tracking of the deleted scan.")
                    return
                if f"{scan.key} is terminated, expiration set to" in line:
                    print(
                        "Tracker expired the scan before its manual deletion, retry..."
                    )
                    break


@pytest.mark.timeout(60)
def test_trimming(redis_url, data_store, dummy_id):
    @contextmanager
    def four_scans():
        try:
            yield [data_store.create_scan(dummy_id) for _ in range(4)]
        finally:
            for scan in scans:
                scan.close()

    with memory_tracker_ctx(
        redis_url,
        tracker_opts={
            "--redis-url": redis_url,
            "--cleaning-threshold": "50",
            "--protected-history": "1",
            "--monitoring-period": "1",
        },
        redis_config={"maxmemory": "100MB", "maxmemory-policy": "noeviction"},
    ) as proc:
        # wait for memory tracker to start
        while True:
            line = proc.stdout.readline()
            print(line, end="")
            if "Memory usage" in line:
                break

        with four_scans() as scans:
            streams = []
            for scan in scans:
                for i in range(4):
                    # 8kB datapoints
                    stream_definition = Stream.make_definition(
                        f"stream_{i}", "float", (32, 32)
                    )
                    streams.append(scan.create_stream(stream_definition))
            for scan in scans:
                scan.prepare()
                scan.start()

            group_1 = streams[:8]
            group_2 = streams[
                8:-1
            ]  # let one empty stream to ensure it does not disturb the tracker

            # send data in group_1 with a break in the middle to
            # let tracker take multiple memory snapshots
            for i in range(1024):
                if i == 512:
                    time.sleep(1)
                for stream in group_1:
                    stream.send(np.empty((32, 32)))
            for stream in group_1:
                stream.join()

            # seal one of them for testing
            group_1[-1].seal()

            # wait to go back under 50MB
            while data_store._redis.info()["used_memory_dataset"] > 50 * 2**20:
                time.sleep(0.05)

            # perform sanity checks on group_1 streams
            for stream in group_1:
                with pytest.raises(IndexError) as exc_info:
                    stream[0]
                assert isinstance(exc_info.value, IndexNoMoreThereError)
                assert len(stream) == 1024
                stream[1000]  # last elements are still there

            # write to group_2 streams to force full trimming on group_1
            for i in range(1024):
                if i == 512:
                    time.sleep(1)
                for stream in group_2:
                    stream.send(np.empty((32, 32)))
            for stream in group_2:
                stream.join()

            # wait to go back under 50MB
            while data_store._redis.info()["used_memory_dataset"] > 50 * 2**20:
                time.sleep(0.05)

            # perform sanity checks on group_1 AND group_2 streams
            for stream in group_1[:-1]:
                with pytest.raises(IndexError) as exc_info:
                    stream[1022]
                stream[1023]  # stream is not sealed, so last item hold the length
                assert isinstance(exc_info.value, IndexNoMoreThereError)
                assert len(stream) == 1024

            # sealed one has no data anymore but still hold the length
            with pytest.raises(IndexError) as exc_info:
                group_1[-1][1023]
            assert isinstance(exc_info.value, IndexNoMoreThereError)
            assert len(group_1[-1]) == 1024

            for stream in group_2:
                with pytest.raises(IndexError) as exc_info:
                    stream[0]
                assert isinstance(exc_info.value, IndexNoMoreThereError)
                assert len(stream) == 1024
                stream[1000]


@pytest.mark.timeout(20)
def test_protected_history(redis_url, data_store, dummy_id):
    with memory_tracker_ctx(
        redis_url,
        tracker_opts={
            "--redis-url": redis_url,
            "--cleaning-threshold": "10",
            "--protected-history": "3",
            "--monitoring-period": "1",
        },
        redis_config={"maxmemory": "100MB", "maxmemory-policy": "noeviction"},
    ) as proc:
        scan = data_store.create_scan(dummy_id)
        try:
            streams = []
            for i in range(4):
                # 8kB datapoints
                stream_definition = Stream.make_definition(
                    f"stream_{i}", "float", (32, 32)
                )
                streams.append(scan.create_stream(stream_definition))
            scan.prepare()
            scan.start()
            for i in range(1024):
                for stream in streams:
                    stream.send(np.empty((32, 32)))
            print(data_store._redis.info()["used_memory_dataset"])

            while True:
                line = proc.stdout.readline()
                print(line, end="")
                if (
                    "CRITICAL: Unable to free memory ! Remaining data is too recent"
                    in line
                ):
                    break

            # wait to go back under 10MB
            while data_store._redis.info()["used_memory_dataset"] > 10 * 2**20:
                time.sleep(0.05)

        finally:
            scan.close()


# def test_cleaning_routine_overflow(data_store, dummy_id):
#     # force multiple pass of the cleaning routine
#     assert False


@pytest.mark.timeout(20)
def test_out_of_memory_handling(redis_url, data_store, dummy_id):
    with memory_tracker_ctx(
        redis_url,
        tracker_opts={
            "--redis-url": redis_url,
            "--monitoring-period": "1",
            "--protected-history": "5",
        },
        redis_config={"maxmemory": "20MB", "maxmemory-policy": "noeviction"},
    ) as proc:
        while True:
            try:
                scan = data_store.create_scan(dummy_id)
                stream_definition = Stream.make_definition("mystream", "float", (64,))
                stream = scan.create_stream(stream_definition)
                scan.prepare()
                scan.start()
                for _ in range(10):
                    stream.send(
                        np.empty(
                            (
                                100,
                                64,
                            )
                        )
                    )
                stream.join()
                scan.close()
            except redis.exceptions.ResponseError:
                try:
                    stream.event_stream._sink.stop()
                except Exception:
                    pass

            line = proc.stdout.readline()
            print(line, end="")
            if (
                "CRITICAL: Redis is out of memory, monitoring routine can't run, retry..."
                in line
            ):
                break

        # wait to go back under 16MB
        while data_store._redis.info()["used_memory_dataset"] > 16 * 2**20:
            time.sleep(0.05)
