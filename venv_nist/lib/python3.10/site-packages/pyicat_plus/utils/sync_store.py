import os
import json
import logging
import datetime
from tqdm import tqdm
from glob import glob
import concurrent.futures
from typing import Dict, Any, List, Tuple, Optional, Callable

from . import raw_data
from . import sync_types
from . import path_utils

logger = logging.getLogger("ICAT SYNC")


class ExperimentalSessionStore:
    def __init__(
        self,
        cache_dir: Optional[str] = None,
        save_dir: Optional[str] = None,
        raw_data_format: str = "esrfv3",
        invalidate_cache: bool = False,
    ) -> None:
        """
        :param cache_dir: one JSON file for each cached experimental session
        :param save_dir: one CSV+BASH file for each sync status
        :param raw_data_format: will be used as subdirectory name
        :param invalidate_cache: invalidating session when loaded from cache
        """
        self._save_dir = save_dir
        self._cache_dir = cache_dir
        self._raw_data_format = raw_data_format
        self._invalidate_cache = invalidate_cache

        self._save_subdir = None
        self._cache_subdir = None
        if save_dir is not None:
            self._save_subdir = os.path.join(save_dir, raw_data_format)
        if cache_dir is not None:
            self._cache_subdir = os.path.join(cache_dir, raw_data_format)

        self._exp_sessions: Dict[str, sync_types.ExperimentalSession] = dict()
        self._load_all_session_files()
        self._start_time = datetime.datetime.now()

    def __enter__(self) -> "ExperimentalSessionStore":
        self._start_time = datetime.datetime.now()
        return self

    def __exit__(self, *_) -> bool:
        self.close()
        return False

    def get_session(self, session_dir: str) -> Optional[sync_types.ExperimentalSession]:
        return self._exp_sessions.get(session_dir)

    def add_session(self, exp_session: sync_types.ExperimentalSession) -> None:
        """Add session to the in-memory cache and when enabled save it on disk."""
        self._exp_sessions[exp_session.session_dir] = exp_session
        if not self._cache_subdir:
            return
        filename = self._cache_filename(exp_session)
        data = exp_session.as_dict()
        os.makedirs(self._cache_subdir, mode=0o775, exist_ok=True)
        _json_save(data, filename)

    def remove_session(self, exp_session: sync_types.ExperimentalSession) -> None:
        """Remove session from the in-memory cache and when enabled from disk."""
        self._exp_sessions.pop(exp_session.session_dir, None)
        if not self._cache_subdir:
            return
        filename = self._cache_filename(exp_session)
        try:
            os.remove(filename)
        except FileNotFoundError:
            pass
        logger.info("Removed session cache %s", filename)

    def close(self) -> None:
        try:
            self._save_overview()
        finally:
            self._print_final()

    def _load_all_session_files(self) -> None:
        """Load all the session files when enabled and delete the ones for sessions which directory no longer exists."""
        if not self._cache_subdir:
            return
        logger.info("Loading cache from %s ...", self._cache_subdir)
        filenames = glob(os.path.join(self._cache_subdir, "*.json"))

        logger.info("Loading cache of %d sessions ...", len(filenames))
        data = _executor_map_with_progress(_json_load, filenames)

        if self._invalidate_cache:
            logger.info("Invalidate cache of %d sessions ...", len(data))

        if self._invalidate_cache:
            func = _parse_session_cache_with_invalidation
        else:
            func = _parse_session_cache
        exp_sessions = _executor_map_with_progress(func, filenames, data)

        for exp_session in exp_sessions:
            if exp_session is not None:
                self._exp_sessions[exp_session.session_dir] = exp_session

        logger.info("Loaded cache of %d sessions.", len(self._exp_sessions))

    def _cache_filename(self, exp_session: sync_types.ExperimentalSession) -> str:
        basename = (
            f"{exp_session.proposal}_{exp_session.beamline}_{exp_session.session}.json"
        )
        return os.path.join(self._cache_subdir, basename)

    def _save_overview(self) -> None:
        if not self._save_subdir:
            return
        logger.info(
            "Saving synchronization overview of %d sessions ...",
            len(self._exp_sessions),
        )
        os.makedirs(self._save_subdir, mode=0o775, exist_ok=True)

        save_columns = [
            "beamline",
            "proposal",
            "timeslot",
            "session",
            "url",
            "search_url",
            "path",
            *sync_types.ExperimentalSession.DATASET_STATUSES,
        ]

        save_category = [
            "todo",
            "ok",
            "empty",
            "has_invalid",
            "todo_ongoing",
            "not_in_icat",
        ]

        register_cmd_params = ""
        if self._save_dir:
            register_cmd_params += f"--save-dir {self._save_dir} "
        if self._cache_dir:
            register_cmd_params += f"--cache-dir {self._cache_dir} "
        register_cmd_params += '"$@"'

        with concurrent.futures.ThreadPoolExecutor() as executor:
            # Clear CSV files and add header
            stats_files = dict(
                (s, os.path.join(self._save_subdir, s + ".csv")) for s in save_category
            )

            def _init_csv(filename):
                _save_csv_line(filename, save_columns, clear=True)
                try:
                    os.chmod(filename, 0o0664)
                except PermissionError:
                    pass

            _ = list(executor.map(_init_csv, stats_files.values()))

            # Clear bash files and add header
            cmd_files = dict(
                (s, os.path.join(self._save_subdir, s + ".sh")) for s in save_category
            )

            def _init_cmd(filename):
                _save_file_line(filename, "#!/bin/bash", clear=True)
                _save_file_line(filename, "")
                _save_file_line(
                    filename,
                    "# Run a cached copy because this script can be modified during execution",
                )
                _save_file_line(filename, 'if [ -z "$SCRIPT_CACHED" ]; then')
                _save_file_line(filename, '    SCRIPT_CACHED=$(<"$0")')
                _save_file_line(filename, '    script_name=$(basename "$0")')
                _save_file_line(
                    filename, '    echo "$SCRIPT_CACHED" > /tmp/cached_$script_name'
                )
                _save_file_line(
                    filename,
                    '    SCRIPT_CACHED=1 /bin/bash /tmp/cached_$script_name "$@"',
                )
                _save_file_line(filename, "    exit $?")
                _save_file_line(filename, "fi")
                _save_file_line(filename, "")
                try:
                    os.chmod(filename, 0o0774)
                except PermissionError:
                    pass

            _ = list(executor.map(_init_cmd, cmd_files.values()))

            # Add sessions CSV and BASH files
            def _save_session_line(exp_session):
                icat_investigation = exp_session.icat_investigation
                if not icat_investigation:
                    save_category = "not_in_icat"
                else:
                    statuses = exp_session.dataset_statuses()
                    if statuses & {
                        "not_uploaded",
                        "unregistered",
                        "registered_without_files",
                    }:
                        if icat_investigation and icat_investigation.ongoing:
                            save_category = "todo_ongoing"
                        else:
                            save_category = "todo"
                    elif "invalid" in statuses:
                        save_category = "has_invalid"
                    elif "registered" in statuses:
                        save_category = "ok"
                    else:
                        save_category = "empty"

                tabular_data = exp_session.tabular_data()
                stats = [tabular_data[k] for k in save_columns]
                _save_csv_line(stats_files[save_category], stats)

                register_cmd = f"icat-sync-raw --beamline {exp_session.beamline} --proposal {exp_session.proposal} --session {exp_session.session} {register_cmd_params}"
                _save_file_line(cmd_files[save_category], register_cmd)

            _ = list(executor.map(_save_session_line, self._exp_sessions.values()))

        logger.info("Saving synchronization finished.")

    def _print_final(self):
        end_time = datetime.datetime.now()
        logger.info("")
        logger.info("Total synchronization time was %s", end_time - self._start_time)
        if self._cache_subdir:
            logger.info("Cache was used from %s", self._cache_subdir)
        if self._save_subdir:
            logger.info("Results can be found in %s", self._save_subdir)
            logger.info(
                "To fix issues run %s", os.path.join(self._save_subdir, "todo.sh")
            )


def _executor_map_with_progress(func: Callable, *args) -> List[Any]:
    results = []
    with concurrent.futures.ThreadPoolExecutor() as executor:
        with tqdm(total=len(args[0])) as progress:

            def update_progress(future):
                progress.update(1)
                results.append(future.result())

            futures = [executor.submit(func, *_args) for _args in zip(*args)]

            for future in futures:
                future.add_done_callback(update_progress)

            result = concurrent.futures.wait(futures)

            for future in result.done:
                if future.exception():
                    raise future.exception()

    return results


def _parse_session_cache(
    filename: str, data: dict
) -> Optional[sync_types.ExperimentalSession]:
    try:
        return sync_types.ExperimentalSession.from_dict(data)
    except Exception as e:
        logger.info("Invalidate %s: wrong cache format (%s)", filename, e)
        try:
            os.remove(filename)
        except FileNotFoundError:
            pass


def _parse_session_cache_with_invalidation(
    filename: str, data: dict
) -> Optional[sync_types.ExperimentalSession]:
    exp_session = _parse_session_cache(filename, data)
    if exp_session is None:
        return

    invalidation_reason = _session_invalidation_reason(exp_session)
    if invalidation_reason:
        logger.info("Invalidate %s: %s", exp_session.session_dir, invalidation_reason)
        try:
            os.remove(filename)
        except FileNotFoundError:
            pass
        return

    return exp_session


def _session_invalidation_reason(
    exp_session: sync_types.ExperimentalSession,
) -> Optional[str]:
    if not os.path.isdir(exp_session.session_dir):
        return "no longer exists"

    if not exp_session.icat_investigation:
        # Cached session does not contain any datasets when
        # no ICAT investigation was found
        return

    cached = {path_utils.markdir(dset.path) for dset in exp_session.iter_datasets()}
    dataset_filters = raw_data.get_dataset_filters(
        exp_session.raw_root_dir, raw_data_format=exp_session.raw_data_format
    )
    actual = {
        path_utils.markdir(path)
        for dataset_filter in dataset_filters
        for path in glob(path_utils.markdir(dataset_filter))
    }
    if cached != actual:
        nnew = len(actual - cached)
        nremoved = len(cached - actual)
        return f"has {nnew} new datasets and {nremoved} datasets were removed"


def _json_save(data: Dict[str, Any], filename: str) -> None:
    try:
        with open(filename, "w") as fp:
            json.dump(data, fp, default=_json_serializer, indent=2)
    except Exception as e:
        raise IOError(f"writing failed for file {filename}") from e


def _json_load(filename: str) -> Dict[str, Any]:
    try:
        with open(filename, "r") as fp:
            return json.load(fp, object_pairs_hook=_json_pair_deserializer)
    except Exception as e:
        raise IOError(f"reading failed for file {filename}") from e


def _json_serializer(obj: Any):
    """Serialize datetime and date objects to string."""
    if isinstance(obj, (datetime.datetime, datetime.date)):
        return obj.isoformat()
    else:
        raise TypeError(
            "Object of type '{}' is not JSON serializable".format(type(obj))
        )


def _json_pair_deserializer(items: List[Tuple[str, Any]]) -> Dict[str, Any]:
    """Deserialize string to datetime or date objects."""
    result = dict()
    for key, value in items:
        if "date" in key and value:
            try:
                value = datetime.date.fromisoformat(value)
            except ValueError:
                value = datetime.datetime.fromisoformat(value)
        result[key] = value
    return result


def _save_csv_line(filename: str, row: List[Any], clear: bool = False) -> None:
    line = ",".join([str(item).replace(",", " ").strip() for item in row])
    _save_file_line(filename, line, clear)


def _save_file_line(filename: str, line: str, clear: bool = False) -> None:
    mode = "w" if clear else "a"
    with open(filename, mode) as f:
        f.write(f"{line}\n")
