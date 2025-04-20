import os
import sys
import logging
import argparse
import datetime
from tqdm import tqdm
from time import sleep
from glob import glob
from typing import Optional, Tuple, Generator

from ..utils import raw_data
from ..utils import path_utils
from ..utils import sync_types
from ..client.main import IcatClient
from ..client.bliss import get_icat_client
from ..utils.log_utils import basic_config
from ..utils.sync_store import ExperimentalSessionStore


logger = logging.getLogger("ICAT SYNC")


def sync_raw(
    icat_client: IcatClient,
    beamline: Optional[str] = None,
    proposal: Optional[str] = None,
    session: Optional[str] = None,
    root_dir: Optional[str] = None,
    dry_run: bool = True,
    unsupervised: bool = False,
    raw_data_format: str = "esrfv3",
    save_dir: Optional[str] = None,
    cache_dir: Optional[str] = None,
    invalidate_cache: bool = False,
) -> None:
    with ExperimentalSessionStore(
        cache_dir=cache_dir,
        save_dir=save_dir,
        raw_data_format=raw_data_format,
        invalidate_cache=invalidate_cache,
    ) as exp_session_store:
        if beamline is None:
            beamline = "*"
        if proposal is None:
            proposal = "*"
        if session is None:
            session = "*"
        session_filter = raw_data.get_session_dir(
            proposal,
            beamline,
            session,
            root_dir=root_dir,
            raw_data_format=raw_data_format,
        )

        logger.info("Discovering sessions %s ...", session_filter)
        session_dirs = glob(path_utils.markdir(session_filter))
        logger.info("Discovering sessions finished.")

        logger.info("Parse %d session directories ...", len(session_dirs))

        if dry_run and logger.getEffectiveLevel() > logging.DEBUG:
            session_dirs = tqdm(session_dirs)

        for session_dir in session_dirs:
            (
                proposal_from_dir,
                beamline_from_dir,
                session_from_dir,
            ) = raw_data.parse_session_dir(session_dir, raw_data_format)
            if not proposal_from_dir:
                continue
            session = path_utils.basename(session_dir)
            try:
                _ = sync_session(
                    icat_client,
                    proposal_from_dir,
                    beamline_from_dir,
                    session_from_dir,
                    dry_run=dry_run,
                    unsupervised=unsupervised,
                    raw_data_format=raw_data_format,
                    exp_session_store=exp_session_store,
                )
            except Exception as e:
                raise RuntimeError(
                    f"Failed syncing beamline={beamline_from_dir}, proposal={proposal_from_dir}, session={session_from_dir}"
                ) from e
        logger.info("Session directories parsed.")


def sync_session(
    icat_client: IcatClient,
    proposal: str,
    beamline: str,
    session: str,
    root_dir: Optional[str] = None,
    dry_run: bool = True,
    unsupervised: bool = False,
    allow_open_ended: bool = True,
    raw_data_format: str = "esrfv3",
    exp_session_store: Optional[ExperimentalSessionStore] = None,
) -> Optional[sync_types.ExperimentalSession]:
    remove_from_cache_afterwards = False
    exp_session = None
    try:
        # Experimental session from cache
        if exp_session_store is not None:
            session_dir = raw_data.get_session_dir(
                proposal,
                beamline,
                session,
                root_dir=root_dir,
                raw_data_format=raw_data_format,
            )
            exp_session = exp_session_store.get_session(session_dir)

        # Experimental session from disk and ICAT
        if exp_session is None:
            exp_session = _discover_exp_session(
                icat_client,
                proposal,
                beamline,
                session,
                root_dir=root_dir,
                raw_data_format=raw_data_format,
            )

        if exp_session is None:
            # Not an experimental session
            return exp_session

        # Experimental session from ICAT
        if exp_session.icat_investigation is None:
            _discover_icat_investigation(
                icat_client,
                exp_session,
                allow_open_ended=allow_open_ended,
                raw_data_format=raw_data_format,
            )

        # Print summary of the experimental session
        exp_session.pprint()

        # Register datasets that failed to be registered
        for dataset in _iter_datasets_to_upload(
            exp_session, "not_uploaded", dry_run, unsupervised
        ):
            remove_from_cache_afterwards = True
            icat_client.store_dataset_from_file(dataset.metadata_file)
            sleep(0.1)  # do not overload ICAT

        # Register datasets that were not even attempted to be registered
        for dataset in _iter_datasets_to_upload(
            exp_session, "unregistered", dry_run, unsupervised
        ):
            remove_from_cache_afterwards = True
            icat_client.store_dataset(
                beamline=dataset.beamline,
                proposal=dataset.proposal,
                dataset=dataset.name,
                path=dataset.path,
                metadata=dataset.metadata,
            )
            sleep(0.1)  # do not overload ICAT

        # Add missing data files for registered datasets without data
        for dataset in _iter_datasets_to_upload(
            exp_session, "registered_without_files", dry_run, unsupervised
        ):
            remove_from_cache_afterwards = True
            icat_client.add_files(dataset.icat_dataset.icat_dataset_id)
            sleep(0.1)  # do not overload ICAT

    finally:
        if exp_session_store and exp_session:
            if remove_from_cache_afterwards:
                exp_session_store.remove_session(exp_session)
            else:
                exp_session_store.add_session(exp_session)

    return exp_session


def _iter_datasets_to_upload(
    exp_session: sync_types.ExperimentalSession,
    dataset_status: str,
    dry_run: bool,
    unsupervised: bool,
    verbose: bool = False,
) -> Generator[sync_types.Dataset, None, None]:
    datasets = exp_session.datasets[dataset_status]
    if not datasets:
        return

    action = f'Upload "{dataset_status}" dataset'

    if dry_run:
        do_yield = False
        do_print = verbose
    elif unsupervised:
        if exp_session.allow_unsupervised_upload(dataset_status):
            do_yield = True
            do_print = True
        else:
            do_yield = False
            do_print = verbose
    else:
        if _ask_execute_confirmation(exp_session, action + "s"):
            do_yield = True
            do_print = True
        else:
            do_yield = False
            do_print = verbose

    if not do_yield and not do_print:
        return

    for dataset in datasets:
        if do_print:
            _print_dataset_details(dataset, action)
        if do_yield:
            yield dataset


def _ask_execute_confirmation(
    exp_session: sync_types.ExperimentalSession, action: str
) -> bool:
    logger.info("Search URL: %s", exp_session.icat_investigation.search_url)
    result = input(f"{action}? (y/[n])")
    return result.lower() in ("y", "yes")


def _print_dataset_details(dataset: sync_types.Dataset, title: str):
    logger.debug("-> %s: %s", title, dataset.path)
    logger.debug("    Name: %s", dataset.name)
    if dataset.metadata:
        logger.debug("    Sample: %s", dataset.metadata["Sample_name"])
        dataset_startdate = dataset.startdate
        dataset_enddate = dataset.enddate
        logger.debug("    Start time: %s", dataset_startdate)
        logger.debug("    End time: %s", dataset_enddate)
        logger.debug("    Duration: %s", dataset_enddate - dataset_startdate)


def _discover_exp_session(
    icat_client: IcatClient,
    proposal: str,
    beamline: str,
    session: str,
    root_dir: Optional[str] = None,
    raw_data_format: str = "esrfv3",
) -> Optional[sync_types.ExperimentalSession]:
    """Discovery the experimental session on disk and in ICAT"""
    try:
        session_startdate_fromdir = datetime.datetime.strptime(session, "%Y%m%d").date()
    except ValueError:
        # Not a session directory
        return

    session_dir = raw_data.get_session_dir(
        proposal, beamline, session, root_dir=root_dir, raw_data_format=raw_data_format
    )

    raw_root_dir = raw_data.get_raw_data_dir(
        session_dir, raw_data_format=raw_data_format
    )

    return sync_types.ExperimentalSession(
        session_dir=session_dir,
        raw_root_dir=raw_root_dir,
        raw_data_format=raw_data_format,
        proposal=proposal,
        beamline=beamline,
        session=session,
        startdate=session_startdate_fromdir,
        search_url=_data_portal_search_url(proposal, beamline),
        datasets=dict(),
    )


def _discover_icat_investigation(
    icat_client: IcatClient,
    exp_session: sync_types.ExperimentalSession,
    allow_open_ended: bool = True,
    raw_data_format: str = "esrfv3",
) -> None:
    # Get the ICAT investigation related to the raw data directory
    investigation = icat_client.investigation_info(
        exp_session.beamline,
        exp_session.proposal,
        date=exp_session.startdate,
        allow_open_ended=allow_open_ended,
    )
    if not investigation:
        return
    if not investigation.get("startDate"):
        return

    icat_investigation = sync_types.IcatInvestigation(
        id=investigation["id"],
        url=investigation["data portal"],
        search_url=_data_portal_search_url(
            investigation["proposal"], investigation["beamline"]
        ),
        registered_datasets=list(),
    )

    icat_investigation.startdate = _as_datetime(investigation["startDate"])
    if investigation.get("endDate"):
        icat_investigation.enddate = _as_datetime(investigation["endDate"])

    # Add dataset information
    exp_session.icat_investigation = icat_investigation
    _discover_icat_datasets(icat_client, exp_session, allow_open_ended=allow_open_ended)
    _discover_raw_datasets(exp_session, raw_data_format=raw_data_format)


def _discover_icat_datasets(
    icat_client: IcatClient,
    exp_session: sync_types.ExperimentalSession,
    allow_open_ended: bool = True,
) -> None:
    logger.info("Extracting metadata from ICAT for %s ...", exp_session.raw_root_dir)
    exp_session.icat_investigation.registered_datasets = (
        icat_client.registered_datasets(
            exp_session.beamline,
            exp_session.proposal,
            date=exp_session.startdate,
            allow_open_ended=allow_open_ended,
        )
    )
    logger.info("Extracting metadata finished.")


def _discover_raw_datasets(
    exp_session: sync_types.ExperimentalSession,
    raw_data_format: str = "esrfv3",
) -> None:
    """Browse all datasets from the raw data directory and compare with
    the datasets registered with the ICAT investigation"""

    dataset_filters = raw_data.get_dataset_filters(
        exp_session.raw_root_dir, raw_data_format=raw_data_format
    )

    if len(dataset_filters) > 1:
        logger.info(
            "Extracting metadata from HDF5 for %d filters ...", len(dataset_filters)
        )
    else:
        logger.info("Extracting metadata from HDF5 for %s ...", dataset_filters[0])

    registered_datasets = {
        path_utils.markdir(dset.dataset_id.path): dset
        for dset in exp_session.icat_investigation.registered_datasets
    }

    dataset_dirs = []
    for pattern in dataset_filters:
        dataset_dirs.extend(glob(path_utils.markdir(pattern)))
    dataset_dirs.sort(key=_get_creation_time)

    for dataset_dir in dataset_dirs:
        name = raw_data.get_raw_dataset_name(
            dataset_dir, raw_data_format=raw_data_format
        )
        if not os.path.isdir(dataset_dir):
            status_reason = ["Dataset path is not a directory"]
        elif not name:
            status_reason = ["Dataset path has the wrong format"]
        else:
            status_reason = []

        dataset = sync_types.Dataset(
            path=dataset_dir,
            proposal=exp_session.proposal,
            beamline=exp_session.beamline,
            name=name,
            raw_root_dir=exp_session.raw_root_dir,
            status_reason=status_reason,
        )

        if not dataset.is_invalid():
            dataset.icat_dataset = registered_datasets.get(dataset.path)
            if not dataset.is_registered():
                _add_metadata_from_raw_data(
                    dataset, exp_session, raw_data_format=raw_data_format
                )

        exp_session.add_dataset(dataset)
    logger.info("Metadata from HDF5 extracted.")


def _get_creation_time(file_path: str) -> float:
    """
    Get the creation time of a file or directory.
    """
    if os.path.exists(file_path):
        return os.path.getctime(file_path)
    return 0


def _data_portal_search_url(
    proposal: str,
    beamline: str,
) -> str:
    return f"https://data.esrf.fr/beamline/{beamline}?search={proposal}"


def _add_metadata_from_raw_data(
    dataset: sync_types.Dataset,
    exp_session: sync_types.ExperimentalSession,
    raw_data_format: str = "esrfv3",
) -> None:
    try:
        dataset_metadata = raw_data.get_raw_dataset_metadata(
            dataset.path, raw_data_format=raw_data_format
        )
        err_msg = f"data format assumed to be '{raw_data_format}'"
    except Exception as e:
        dataset_metadata = dict()
        err_msg = str(e)

    if set(dataset_metadata) != {"Sample_name", "startDate", "endDate"}:
        dataset.status_reason.append(f"Cannot extract dataset metadata: {err_msg}")
        return

    dataset.startdate = _as_datetime(dataset_metadata["startDate"])
    dataset.enddate = _as_datetime(dataset_metadata["endDate"])

    dataset_startdate, msg = _force_date_within_range(
        exp_session.icat_investigation.startdate,
        exp_session.icat_investigation.enddate,
        dataset.startdate,
        end=False,
    )
    if msg:
        dataset.status_reason.append(msg)

    dataset_enddate, msg = _force_date_within_range(
        exp_session.icat_investigation.startdate,
        exp_session.icat_investigation.enddate,
        dataset.enddate,
        end=True,
    )
    if msg:
        dataset.status_reason.append(msg)

    dataset_metadata["startDate"] = dataset_startdate
    dataset_metadata["endDate"] = dataset_enddate
    dataset.metadata = dataset_metadata


def _force_date_within_range(
    session_startdate: datetime.datetime,
    session_enddate: Optional[datetime.datetime],
    dataset_date: datetime.datetime,
    end: bool = False,
) -> Tuple[datetime.datetime, Optional[str]]:
    if end:
        action = "ended"
        dstart_seconds = 2
        dend_seconds = 1
    else:
        action = "started"
        dstart_seconds = 1
        dend_seconds = 2
    msg = None
    if session_enddate is not None and dataset_date >= session_enddate:
        msg = f"{action} {dataset_date - session_enddate} after the end of the session"
        dataset_date = session_enddate - datetime.timedelta(seconds=dend_seconds)
    if dataset_date <= session_startdate:
        msg = f"{action} {session_startdate - dataset_date} before the start of the session"
        dataset_date = session_startdate + datetime.timedelta(seconds=dstart_seconds)
    return dataset_date, msg


def _as_datetime(isoformat: str) -> datetime.datetime:
    return datetime.datetime.fromisoformat(isoformat).astimezone()


def main(argv=None):
    if argv is None:
        argv = sys.argv

    parser = argparse.ArgumentParser(
        description="Register missing raw dataset with ICAT"
    )
    parser.add_argument(
        "--beamline", type=str.lower, required=False, help="Beamline name (e.g. id00)"
    )
    parser.add_argument(
        "--proposal",
        type=str.lower,
        required=False,
        help="Proposal name (e.g. ihch123)",
    )
    parser.add_argument(
        "--session", required=False, help="Session name (e.g. 20231028)"
    )
    parser.add_argument(
        "--register",
        action="store_true",
        required=False,
        help="Register datasets with ICAT when needed (prompts for confirmation)",
    )
    parser.add_argument(
        "--auto-register",
        action="store_true",
        required=False,
        help="Register datasets with ICAT when needed without confirmation (only when it is safe to do so)",
    )
    parser.add_argument(
        "--format",
        required=False,
        choices=["esrfv1", "esrfv2", "esrfv3", "id16bspec", "mx"],
        default="esrfv3",
        help="Raw data structure",
    )
    parser.add_argument(
        "--save-dir",
        type=str,
        default=None,
        required=False,
        help="Directory to save the sync statistics for humans",
    )
    parser.add_argument(
        "--cache-dir",
        type=str,
        default=None,
        required=False,
        help="Directory to cache the sync state for machines",
    )
    parser.add_argument(
        "--no-print", action="store_true", help="Do not print the session summaries"
    )
    parser.add_argument(
        "--invalidate-cache",
        action="store_true",
        help="Invalidate session when loading the cache",
    )
    args = parser.parse_args(argv[1:])

    basic_config(
        logger=logger,
        level=logging.INFO if args.no_print else logging.DEBUG,
        format="%(message)s",
    )

    icat_client = get_icat_client(timeout=600)

    dry_run = not args.register and not args.auto_register
    unsupervised = args.auto_register

    sync_raw(
        icat_client,
        beamline=args.beamline,
        proposal=args.proposal,
        session=args.session,
        dry_run=dry_run,
        unsupervised=unsupervised,
        raw_data_format=args.format,
        save_dir=args.save_dir,
        cache_dir=args.cache_dir,
        invalidate_cache=args.invalidate_cache,
    )

    icat_client.disconnect()


if __name__ == "__main__":
    sys.exit(main())
