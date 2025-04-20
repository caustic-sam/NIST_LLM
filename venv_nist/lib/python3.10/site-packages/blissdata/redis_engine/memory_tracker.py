# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.
import sys
import time
import math
import logging
import argparse
import redis
from datetime import timedelta
from dataclasses import dataclass

from .store import DataStore
from .scan import Scan
from .scan import ScanState
from .exceptions import NoScanAvailable, ScanNotFoundError, ScanLoadError


memory_tracking_stream = "_MEMORY_TRACKING_"
SEAL_ID = ("-".join([str(2**64 - 1)] * 2)).encode()


def format_bytes(nbytes):
    suffixes = ["B", "KB", "MB", "GB", "TB", "PB"]
    exp = int(math.log(nbytes, 1024))
    return f"{nbytes/1024**exp:.4g}{suffixes[exp]}"


class NoFreeableMemoryError(Exception):
    pass


@dataclass
class TrackedScan:
    scan: Scan
    last_activity: int

    def __eq__(self, other):
        return self.scan.key == other.scan.key

    def __hash__(self):
        return hash(self.scan.key)


def set_scan_expiration(data_store, scan_key, ttl):
    try:
        scan = data_store.load_scan(scan_key)
    except ScanNotFoundError:
        # scan already deleted from Redis by user, skip it
        return
    except ScanLoadError:
        logging.warning("Expiration of scan %r cannot be set", scan_key)

    if scan.state < ScanState.CLOSED:
        raise RuntimeError(f"Scan {scan_key} is not terminated.")

    def expire_scan_keys(pipe: redis.client.Pipeline) -> None:
        keys = [stream.key() for stream in scan._model.data_streams.values()]
        keys.append(scan._model.state_stream.key())
        keys.append(scan_key)
        for key in keys:
            pipe.expire(key, ttl, lt=True)

    data_store._redis.transaction(expire_scan_keys)


class MemoryTracker:
    def __init__(
        self,
        data_store,
        inactive_scan_deletion,
        closed_scan_deletion,
        cleaning_time_slice,
    ):
        self._data_store = data_store
        self.inactive_scan_deletion = inactive_scan_deletion
        self.closed_scan_deletion = closed_scan_deletion
        self.cleaning_time_slice = cleaning_time_slice

        try:
            self._since, scan_keys = self._data_store.search_existing_scans()
        except redis.exceptions.ResponseError:
            # search transaction can fail if Redis is out-of-memory
            self._since = "0-0"
            scan_keys = []

        self._tracked_scans = set()
        for scan_key in scan_keys:
            try:
                scan = self._data_store.load_scan(scan_key)
                raw = self._data_store._redis.xrevrange(
                    scan._model.state_stream.key(), count=1
                )
                last_activity = int(raw[0][0].split(b"-")[0]) / 1000
                self._tracked_scans.add(TrackedScan(scan, last_activity))
            except ScanNotFoundError:
                # scan already deleted from Redis by user, skip it
                pass
            except ScanLoadError:
                logging.warning("Cannot track scan %r", scan_key, exc_info=True)

    def update(self):
        # Discover new scans
        while True:
            try:
                since, scan_key = self._data_store.get_next_scan(
                    block=False, since=self._since
                )
            except NoScanAvailable:
                break
            else:
                try:
                    scan = self._data_store.load_scan(scan_key)
                    last_activity = int(since.split("-")[0]) / 1000
                    self._tracked_scans.add(TrackedScan(scan, last_activity))
                    self._since = since
                    logging.info(f"Start tracking {scan.key}")
                except ScanNotFoundError:
                    # scan already deleted from Redis by user, skip it
                    pass
                except ScanLoadError:
                    logging.warning("Cannot track scan %r", scan_key, exc_info=True)

        # Update scans state and save the current index of each data stream
        deleted_scans = set()
        all_data_streams_ids = {}
        for track in self._tracked_scans:
            try:
                track.scan.update(block=False)
            except ScanNotFoundError:
                logging.info(f"Scan {track.scan.key} has been deleted, stop tracking")
                deleted_scans.add(track)
                continue

            if track.scan.state >= ScanState.PREPARED:
                streams_ids = self._data_streams_ids(track.scan)
                if streams_ids:
                    track.last_activity, _ = self._data_store._redis.time()
                all_data_streams_ids.update(streams_ids)

        # Untrack deleted scans
        self._tracked_scans -= deleted_scans

        # Store the tracking info into a dedicated redis stream, so it can be retrieved by date
        if all_data_streams_ids:
            self._data_store._redis.xadd(memory_tracking_stream, all_data_streams_ids)

        deleted_scans = set()
        epoch, _ = self._data_store._redis.time()
        for track in self._tracked_scans:
            if track.scan.state == ScanState.CLOSED:
                set_scan_expiration(
                    self._data_store, track.scan.key, self.closed_scan_deletion
                )
                deleted_scans.add(track)
                logging.info(
                    f"Scan {track.scan.key} is terminated, expiration set to {timedelta(seconds=self.closed_scan_deletion)} seconds"
                )
            elif epoch - track.last_activity >= self.inactive_scan_deletion:
                self._data_store.delete_scan(track.scan.key, force=True)
                deleted_scans.add(track)
                logging.warning(
                    f"Open scan {track.scan.key} was inactive for {timedelta(seconds=self.inactive_scan_deletion)} seconds, deleting now"
                )
        self._tracked_scans -= deleted_scans

    def _data_streams_ids(self, scan):
        pipe = self._data_store._redis.pipeline(transaction=False)

        streams_keys = [stream.key for stream in scan.streams.values()]
        for key in streams_keys:
            pipe.xrevrange(key, count=1)
        raw = pipe.execute()

        last_ids = {}
        for key, stream_raw in zip(streams_keys, raw):
            if stream_raw:  # stream exists
                last_ids[key] = stream_raw[0][0]
        return last_ids

    def reclaim_memory(self, protected_history: int):
        """protected_history is the amount of history in seconds that reclaim_memory() cannot free."""
        sec, usec = self._data_store._redis.time()
        now_ms = sec * 1000 + usec // 1000

        raw = self._data_store._redis.xrange(memory_tracking_stream, count=1)
        if not raw:
            raise NoFreeableMemoryError
        tracking_time_origin = int(raw[0][0].decode().split("-")[0])

        # Forget the oldest cleaning_time_slice percent of all tracked data
        cleaning_horizon = (
            tracking_time_origin
            + (now_ms - tracking_time_origin) * self.cleaning_time_slice // 100
        )
        cleaning_horizon = min(cleaning_horizon, now_ms - protected_history * 1000)

        if cleaning_horizon <= tracking_time_origin:
            raise NoFreeableMemoryError

        raw = self._data_store._redis.xrange(
            memory_tracking_stream, max=cleaning_horizon
        )
        streams = {}
        for _, entry in raw:
            streams.update(entry)

        # TODO: can be optimized with 2 pipelines (xlens then xtrims), or even better with a server script
        for key, idx in streams.items():
            # stream may have been trimmed already (length 1) or even deleted (length 0)
            stream_len = self._data_store._redis.xlen(key)
            limit = stream_len - 1
            if limit > 0:
                if idx != SEAL_ID:
                    # faster trimming on the run
                    self._data_store._redis.xtrim(key, minid=idx, limit=limit)
                else:
                    # do not approximate for the last trimming to not leave data behind
                    self._data_store._redis.xtrim(key, minid=idx, approximate=False)

        # Trim memory_tracking_stream as well once the data has been removed
        self._data_store._redis.xtrim(
            memory_tracking_stream, minid=cleaning_horizon, approximate=False
        )
        logging.info(
            f"Trimmed off the oldest {self.cleaning_time_slice}% of stream history"
        )


def run(
    redis_url,
    cleaning_threshold,
    protected_history,
    monitoring_period,
    inactive_scan_deletion,
    closed_scan_deletion,
    cleaning_time_slice,
    init_db: bool = False,
):
    logging.basicConfig(
        format="%(asctime)s %(levelname)s: %(message)s",
        level=logging.INFO,
        stream=sys.stdout,
    )

    data_store = DataStore(redis_url, init_db=init_db)
    max_memory = int(data_store._redis.config_get("maxmemory")["maxmemory"])
    if max_memory == 0:
        logging.error(
            "Redis database has no 'maxmemory' configured, please update Redis configuration."
        )
        sys.exit(1)

    bytes_trigger = max_memory * cleaning_threshold // 100
    if not (0 <= cleaning_threshold <= 100):
        logging.error("Threshold should be comprise between 0 and 100")
        sys.exit(1)

    logging.info(f"INIT | Redis max. memory: {format_bytes(max_memory)}")
    logging.info(
        f"INIT | Mem. usage to trigger cleanup: {cleaning_threshold}% ({format_bytes(bytes_trigger)})"
    )

    logging.info(f"INIT | Protected history length: {protected_history} seconds")

    tracker = MemoryTracker(
        data_store, inactive_scan_deletion, closed_scan_deletion, cleaning_time_slice
    )
    while True:
        try:
            tracker.update()
        except redis.exceptions.OutOfMemoryError:
            # The tracker just ignore out-of-memory errors. If it can't journalize memory usage, then
            # nobody can neither add data to redis...
            logging.critical(
                "Redis is out of memory, monitoring routine can't run, retry..."
            )
            time.sleep(0.5)
            continue
        mem_usage = data_store._redis.info()["used_memory"]
        logging.info(f"Memory usage: {format_bytes(mem_usage)}")
        if mem_usage >= bytes_trigger:
            try:
                tracker.reclaim_memory(protected_history)
            except NoFreeableMemoryError:
                logging.critical(
                    f"Unable to free memory ! Remaining data is too recent to be freed (protected_history: {protected_history} seconds)"
                )
                time.sleep(1)
        else:
            time.sleep(monitoring_period)


def cli():
    parser = argparse.ArgumentParser(description="Redis memory cleaner for blissdata.")
    parser.add_argument("--redis-url", help="Redis server address", required=True)
    parser.add_argument(
        "--cleaning-threshold",
        help="Percentage of memory usage to trigger a cleaning routine.",
        required=False,
        default=80,
        type=int,
    )
    parser.add_argument(
        "--protected-history",
        help="Recent data protection in seconds. Cleaning routine can only erase data older than this.\
        Be careful, protecting too much of the history may prevent the cleaning routine to release enough space.",
        required=False,
        default=180,
        type=int,
    )
    parser.add_argument(
        "--monitoring-period",
        help="Tracker updates period in seconds",
        required=False,
        default=30,
        type=int,
    )
    parser.add_argument(
        "--inactive-scan-deletion",
        help="Time in seconds after which an inactive and non-terminated scan is completely deleted\
        (data streams may be trimmed earlier).",
        required=False,
        default=24 * 60 * 60,
        type=int,
    )
    parser.add_argument(
        "--closed-scan-deletion",
        help="Time in seconds after which a properly terminated scan is completely deleted\
        (data streams may be trimmed earlier).",
        required=False,
        default=7 * 24 * 60 * 60,
        type=int,
    )
    parser.add_argument(
        "--cleaning-time-slice",
        help="Size of the history slice which is released by a cleaning routine. It is a percentage\
        of the total time covered by history (i.e. the oldest 20 percent of all history).",
        required=False,
        default=20,
        type=int,
    )
    parser.add_argument(
        "--init-db",
        help="Initialize a fresh redis database",
        action="store_true",
        required=False,
    )
    args = vars(parser.parse_args())
    run(**args)


if __name__ == "__main__":
    cli()
