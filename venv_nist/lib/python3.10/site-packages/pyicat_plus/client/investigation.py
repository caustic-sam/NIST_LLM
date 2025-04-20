import os
import logging
import datetime
from urllib.parse import urljoin
from typing import Optional, List, Union, Sequence

import requests
import numpy

from ..concurrency import QueryPool
from .interface import DatasetId, Dataset, DatasetMetadata
from ..utils.maxsizedict import MaxSizeDict
from ..utils.url import normalize_url
from . import defaults

logger = logging.getLogger(__name__)

_DEFAULT_START_TIME = datetime.time(hour=8)


class IcatInvestigationClient:
    """Client for the investigation part of the ICAT+ REST API.

    An "investigation" is a time slot assigned to a particular proposal
    at a particular beamline.

    REST API docs:
    https://icatplus.esrf.fr/api-docs/

    The ICAT+ server project:
    https://gitlab.esrf.fr/icat/icat-plus/-/blob/master/README.md
    """

    DEFAULT_SCHEME = "https"

    def __init__(
        self,
        url: str,
        api_key: Optional[str] = None,
        timeout: Optional[float] = None,
    ):
        if api_key is None:
            api_key = defaults.ELOGBOOK_TOKEN
        url = normalize_url(url, default_scheme=self.DEFAULT_SCHEME)

        path = f"dataacquisition/{api_key}/investigation"
        query = "?instrumentName={beamline}&investigationName={proposal}"
        self._investigation_url = urljoin(url, path + query)

        path = f"dataacquisition/{api_key}/dataset"
        query = "?investigationId={investigation_id}"
        self._dataset_url = urljoin(url, path + query)

        self.raise_error = False
        self.__query_pool = QueryPool(timeout=timeout, maxqueries=20)
        self.__investigation_info = MaxSizeDict(maxsize=20)

    @property
    def timeout(self):
        return self.__query_pool.timeout

    @timeout.setter
    def timeout(self, value: Optional[float] = None):
        self.__query_pool.timeout = value

    def _get_with_response_parsing(
        self, url: str, timeout: Optional[float] = None
    ) -> Optional[list]:
        """Return `None` means the information is not available at this moment.
        An empty list means that an error has occured on the server side or an
        actual empty list is returned.
        """
        try:
            response = self.__query_pool.execute(
                requests.get, args=(url,), timeout=timeout, default=None
            )
        except requests.exceptions.ReadTimeout:
            return None
        except Exception as e:
            if self.raise_error:
                raise
            logger.exception(e)
            return None
        if response is None:
            return None
        if self.raise_error:
            response.raise_for_status()
        elif not response.ok:
            logger.error("%s: %s", response, response.text)
        if response.ok:
            return response.json()
        else:
            return list()

    def investigation_info(
        self,
        beamline: str,
        proposal: str,
        date: Optional[Union[datetime.datetime, datetime.date]] = None,
        allow_open_ended: bool = True,
        timeout: Optional[float] = None,
    ) -> Optional[dict]:
        """An investigation is defined by a time slot. Find an investigation (if any)
        for a beamline, proposal and date ("now" when not provided). When there is
        more than one investigation, select the closest one started before or at the date.
        If there is no such investigation, get the closest investigation which starts after the date.
        """
        investigation_key = beamline, proposal, date
        ninfo = self.__investigation_info.get(investigation_key)
        if ninfo is not None:
            return ninfo

        # Get all investigations for this proposal and beamline
        url = self._investigation_url.format(beamline=beamline, proposal=proposal)
        investigations = self._get_with_response_parsing(url, timeout=timeout)
        if investigations is None:
            return None  # not available at the moment

        # Select investigation
        investigation = _select_investigation(
            investigations, date=date, allow_open_ended=allow_open_ended
        )
        if investigation is None:
            return dict()  # no valid investigation found

        # Normalize information
        for key in ["parameters", "visitId"]:
            investigation.pop(key, None)
        ninfo = dict()
        ninfo["proposal"] = investigation.pop("name", None)
        ninfo["beamline"] = investigation.pop("instrument", dict()).get("name", None)
        ninfo.update(investigation)
        ninfo["e-logbook"] = (
            f"https://data.esrf.fr/investigation/{investigation['id']}/events"
        )
        ninfo["data portal"] = (
            f"https://data.esrf.fr/investigation/{investigation['id']}/datasets"
        )

        self.__investigation_info[investigation_key] = ninfo
        return ninfo

    def _investigation_id(
        self,
        beamline: str,
        proposal: str,
        date: Optional[Union[datetime.datetime, datetime.date]] = None,
        allow_open_ended: bool = True,
        timeout: Optional[float] = None,
    ) -> Optional[int]:
        info = self.investigation_info(
            beamline=beamline,
            proposal=proposal,
            date=date,
            allow_open_ended=allow_open_ended,
            timeout=timeout,
        )
        if info is None:
            return None
        return info.get("id", None)

    def registered_dataset_ids(
        self,
        beamline: str,
        proposal: str,
        date: Optional[Union[datetime.datetime, datetime.date]] = None,
        allow_open_ended: bool = True,
        timeout: Optional[float] = None,
    ) -> Optional[List[DatasetId]]:
        investigation_id = self._investigation_id(
            beamline=beamline,
            proposal=proposal,
            date=date,
            allow_open_ended=allow_open_ended,
            timeout=timeout,
        )
        if investigation_id is None:
            return None
        url = self._dataset_url.format(investigation_id=investigation_id)
        datasets = self._get_with_response_parsing(url, timeout=timeout)
        if datasets is None:
            return None  # not available at the moment
        return [self._icat_dataset_to_datasetid(dataset) for dataset in datasets]

    def registered_datasets(
        self,
        beamline: str,
        proposal: str,
        date: Optional[Union[datetime.datetime, datetime.date]] = None,
        allow_open_ended: bool = True,
        timeout: Optional[float] = None,
    ) -> Optional[List[Dataset]]:
        investigation_id = self._investigation_id(
            beamline=beamline,
            proposal=proposal,
            date=date,
            allow_open_ended=allow_open_ended,
            timeout=timeout,
        )
        if investigation_id is None:
            return None
        url = self._dataset_url.format(investigation_id=investigation_id)
        datasets = self._get_with_response_parsing(url, timeout=timeout)
        if datasets is None:
            return None  # not available at the moment
        return [self._icat_dataset_to_dataset(dataset) for dataset in datasets]

    @staticmethod
    def _get_dataset_parameter_value_by_name(
        dataset: dict, parameter_name: str
    ) -> Optional[str]:
        if "parameters" in dataset:
            for p in dataset["parameters"]:
                if p["name"] == parameter_name:
                    return p["value"]

    @classmethod
    def _icat_dataset_to_datasetid(cls, dataset: dict) -> DatasetId:
        location = dataset["location"]
        location, name = os.path.split(location)
        while location and not name:
            location, name = os.path.split(location)
        return DatasetId(name=name, path=dataset["location"])

    @classmethod
    def _icat_dataset_to_dataset(cls, dataset: dict) -> Dataset:
        dataset_id = cls._icat_dataset_to_datasetid(dataset)
        file_count = cls._get_dataset_parameter_value_by_name(dataset, "__fileCount")
        dataset_metadata = DatasetMetadata(
            file_count=int(0 if file_count is None else file_count)
        )
        return Dataset(
            dataset_id=dataset_id,
            icat_dataset_id=dataset["id"],
            dataset_metadata=dataset_metadata,
        )


def _select_investigation(
    investigations: List[dict],
    date: Optional[Union[datetime.datetime, datetime.date]] = None,
    allow_open_ended: bool = True,
) -> Optional[dict]:
    """When `date` is not provided we take it to be "now".

    This method returns the last investigation that contains
    the date or has a start/end closest to the date. The
    investigations are ordered from first to last created.

    Optionally all open-ended investigations can be ignored.
    Open-ended investigations have a start date but no end date.
    These investigations are created by sending dataset or
    investigation messages with start dates 48h outside any
    official investigation.

    :param investigations: unsorted list of investigations
    :param date: possibly not fully deterministic date
    :param allow_open_ended: validation option
    :returns: matching investigation according to the selection rules
    """
    investigations = _filter_valid_investigations(
        investigations, allow_open_ended=allow_open_ended
    )
    if not investigations:
        return
    if len(investigations) == 1:
        return investigations[0]

    # Sort investigations by order of importance: database creation time
    investigations = sorted(
        investigations, key=lambda investigation: investigation["id"]
    )

    date = _deterministic_date(date, investigations)
    return _select_investigation_from_sorted_list(investigations, date)


def _filter_valid_investigations(
    investigations: List[dict], allow_open_ended: bool = True
) -> List[dict]:
    """Filter out investigations with invalid time slots.

    :param investigations: unsorted list of investigations
    :param allow_open_ended: validation option
    :returns: list of valid investigations (preserve order)
    """
    if allow_open_ended:
        return [
            investigation
            for investigation in investigations
            if investigation.get("startDate")
        ]
    return [
        investigation
        for investigation in investigations
        if investigation.get("startDate") and investigation.get("endDate")
    ]


def _deterministic_date(
    date: Optional[Union[datetime.datetime, datetime.date]], investigations: List[dict]
) -> datetime.datetime:
    """The date might

    * not exist: take now
    * have no time zone: add local timezone
    * be a day without time: select time from the investigations or the default 8 a.m.

    The resulting date is fully deterministic.

    :param date: possibly not fully deterministic date
    :param investigations: sorted by order of importance
    :returns: fully deterministic date
    """
    if date is None:
        return datetime.datetime.now().astimezone()

    if isinstance(date, datetime.datetime):
        return date.astimezone()

    # Get the time from the investigations that start on the same day
    start_dates = [
        _tz_aware_fromisoformat(investigation["startDate"])
        for investigation in investigations
    ]
    is_start_date = [dt.date() == date for dt in start_dates]
    last_is_start_date = _last_where_true(is_start_date)
    if last_is_start_date is None:
        # No investigation starts on the same day
        start_time = _DEFAULT_START_TIME
    else:
        # Last investigation that starts on the same day
        start_time = start_dates[last_is_start_date].time()

    date = datetime.datetime.combine(date, start_time)
    return date.astimezone()


def _select_investigation_from_sorted_list(
    investigations: List[dict], date: datetime.datetime
) -> dict:
    """
    :param investigations: sorted by order of importance
    :param date: point in time for which we want to find the corresponding investigation
    :returns: matching investigation according to the selection rules
    """
    # Seconds between date and start/end of each investigation
    n = len(investigations)
    startdiff = numpy.zeros(n)
    enddiff = numpy.full(n, numpy.inf)
    for i, investigation in enumerate(investigations):
        startdate = _tz_aware_fromisoformat(investigation["startDate"])
        startdiff[i] = (date - startdate).total_seconds()
        enddate = investigation.get("endDate")
        if enddate is not None:
            enddate = _tz_aware_fromisoformat(enddate)
            enddiff[i] = (enddate - date).total_seconds()

    # Close open-ended investigation when the next investigation starts (if any)
    closed_ended = numpy.isfinite(enddiff)
    starttime_order = numpy.argsort(-startdiff)
    for i in range(n - 1):
        idx = starttime_order[i]
        if not closed_ended[idx]:
            idx_next = starttime_order[i + 1]
            enddiff[idx] = -startdiff[idx_next]

    # Return the last closed investigation which contains the date (if any)
    contains_date = (startdiff >= 0) & (enddiff >= 0)
    contains_date_and_closed = contains_date & closed_ended
    idx = _last_where_true(contains_date_and_closed)
    if idx is not None:
        return investigations[idx]

    # Return the last investigation which contains the date (if any)
    idx = _last_where_true(contains_date)
    if idx is not None:
        return investigations[idx]

    # Return the last investigation with the closest start or end date
    startdiff = numpy.abs(startdiff)
    enddiff = numpy.abs(enddiff)
    istart = numpy.argmin(startdiff)
    iend = numpy.argmin(enddiff)
    min_startdiff = startdiff[istart]
    min_enddiff = enddiff[iend]
    if min_startdiff < min_enddiff:
        return investigations[istart]
    if min_startdiff > min_enddiff:
        return investigations[iend]
    return investigations[max(istart, iend)]


def _tz_aware_fromisoformat(date: str) -> datetime.datetime:
    return datetime.datetime.fromisoformat(date).astimezone()


def _last_where_true(bool_arr: Sequence[bool]) -> Optional[int]:
    indices = numpy.argwhere(bool_arr)
    if indices.size:
        return indices[-1][0]
