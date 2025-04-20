import os
import logging
import datetime
import dataclasses
from typing import Optional, Dict, Any, List, Set, Generator

from . import path_utils
from ..client.interface import Dataset as IcatDataset


logger = logging.getLogger("ICAT SYNC")


@dataclasses.dataclass(eq=True)
class Dataset:
    path: str  # Path of the directory
    proposal: str
    beamline: str
    name: Optional[str]
    raw_root_dir: str
    status_reason: List[str]
    startdate: Optional[datetime.datetime] = None  # From HDF5
    enddate: Optional[datetime.datetime] = None  # From HDF5
    metadata: Optional[dict] = (
        None  # From HDF5 (times are clipped to the session time slot)
    )
    icat_dataset: Optional[IcatDataset] = None  # From ICAT

    def __post_init__(self) -> None:
        self.path = path_utils.markdir(self.path)

    def as_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Dataset":
        """Factory method to create a Dataset instance from a dictionary."""
        data = data.copy()
        icat_dataset = data.get("icat_dataset")
        if icat_dataset is not None:
            data["icat_dataset"] = IcatDataset.from_dict(icat_dataset)
        return cls(**data)

    def is_invalid(self) -> bool:
        return self.name is None or not os.path.isdir(self.path)

    def has_metadata(self) -> bool:
        return self.metadata is not None

    def to_be_uploaded(self) -> bool:
        return not self.is_registered() and os.path.exists(self.metadata_file)

    def is_registered(self) -> bool:
        return self.icat_dataset is not None

    def is_non_empty_dir(self) -> bool:
        if not os.path.isdir(self.path):
            return False
        return bool(os.listdir(self.path))

    def is_registered_without_files(self) -> bool:
        return (
            self.is_registered()
            and self.icat_dataset.dataset_metadata.file_count == 0
            and self.is_non_empty_dir()
        )

    def get_status(self) -> str:
        # Decreasing order of registration:
        if self.is_registered_without_files():
            return "registered_without_files"
        if self.is_registered():
            return "registered"
        if self.to_be_uploaded():
            return "not_uploaded"
        # Not registered of invalid
        if self.is_invalid() or not self.has_metadata():
            return "invalid"
        return "unregistered"

    @property
    def metadata_file(self) -> str:
        """File stored by Bliss when ending the proposal without confirmation of registration"""
        return os.path.join(
            self.raw_root_dir, "__icat__", f"{path_utils.basename(self.path)}.xml"
        )


@dataclasses.dataclass(eq=True)
class IcatInvestigation:
    id: str
    url: str
    search_url: str
    registered_datasets: List[IcatDataset]
    startdate: Optional[datetime.datetime] = None
    enddate: Optional[datetime.datetime] = None

    def as_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "IcatInvestigation":
        """Factory method to create an IcatInvestigation instance from a dictionary."""
        data = data.copy()
        data["registered_datasets"] = [
            IcatDataset.from_dict(dataset_data)
            for dataset_data in data["registered_datasets"]
        ]
        return cls(**data)

    @property
    def has_timeslot(self) -> bool:
        return self.startdate is not None

    @property
    def open_ended(self) -> bool:
        return self.enddate is None

    @property
    def started(self) -> bool:
        if not self.has_timeslot:
            return False
        now = datetime.datetime.now().astimezone()
        startdate = self.startdate - datetime.timedelta(days=2)
        return now >= startdate

    def _start_from_now(self) -> str:
        if not self.has_timeslot:
            return ""
        now = datetime.datetime.now().astimezone()
        if now >= self.startdate:
            return f"(Started {now - self.startdate} ago)"
        else:
            return f"(Will start in {self.startdate - now})"

    @property
    def ended(self) -> bool:
        if self.open_ended:
            return False
        now = datetime.datetime.now().astimezone()
        enddate = self.enddate + datetime.timedelta(days=2)
        return now >= enddate

    def _end_from_now(self) -> str:
        if self.open_ended:
            return ""
        now = datetime.datetime.now().astimezone()
        if now >= self.enddate:
            return f"(Ended {now - self.enddate} ago)"
        else:
            return f"(Will end in {self.enddate - now})"

    @property
    def ongoing(self) -> bool:
        return self.started and not self.ended

    @property
    def duration(self) -> Optional[datetime.timedelta]:
        if not self.open_ended:
            return self.enddate - self.startdate

    @property
    def timeslot(self) -> str:
        if self.open_ended:
            return f"{self.startdate} - ???"
        return f"{self.startdate} - {self.enddate} ({self.duration})"

    def pprint(self) -> None:
        logger.debug("Investigation ID: %s", self.id)
        logger.debug(" URL: %s", self.url)
        logger.debug(" Search URL: %s", self.search_url)

        if not self.has_timeslot:
            logger.debug("Invalid investigation (no start date)")
            return
        logger.debug(" Start time: %s %s", self.startdate, self._start_from_now())

        if self.open_ended:
            logger.debug(" Duration:", "open-ended (not official)")
            return
        logger.debug(" End time: %s %s", self.enddate, self._end_from_now())

        if self.ongoing:
            logger.debug(" Duration: %s (WARNING: ONGOING)", self.duration)
        else:
            logger.debug(" Duration: %s", self.duration)


@dataclasses.dataclass(eq=True)
class ExperimentalSession:
    session_dir: str
    raw_root_dir: str  # RAW_DATA directory
    raw_data_format: str  # e.g. "esrfv3"
    proposal: str
    beamline: str
    session: str
    startdate: datetime.date  # from the directory name
    search_url: str
    datasets: Dict[str, List[Dataset]]  # from browsing the root directory
    icat_investigation: Optional[IcatInvestigation] = None

    DATASET_STATUSES = (
        "unregistered",
        "not_uploaded",
        "registered",
        "invalid",
        "registered_without_files",
    )

    def __post_init__(self) -> None:
        self.datasets = {
            status: self.datasets.get(status, list())
            for status in self.DATASET_STATUSES
        }
        self.session_dir = path_utils.markdir(self.session_dir)
        self.raw_root_dir = path_utils.markdir(self.raw_root_dir)

    def as_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ExperimentalSession":
        data = data.copy()

        data["datasets"] = {
            key: [Dataset.from_dict(dataset_data) for dataset_data in dataset_list]
            for key, dataset_list in data["datasets"].items()
        }

        icat_investigation = data.get("icat_investigation")
        if icat_investigation is not None:
            data["icat_investigation"] = IcatInvestigation.from_dict(icat_investigation)

        return cls(**data)

    @classmethod
    def allow_unsupervised_upload(cls, dataset_status: str) -> bool:
        if dataset_status not in cls.DATASET_STATUSES:
            raise ValueError(
                f"'{dataset_status}' is not as valid dataset status. Valid statuses are {list(cls.DATASET_STATUSES)}"
            )
        return dataset_status == "registered_without_files"

    @property
    def in_icat_investigation(self) -> Optional[bool]:
        if self.icat_investigation is None:
            return
        if not self.icat_investigation.has_timeslot:
            return False
        inside_timeslot = self.startdate >= self.icat_investigation.startdate.date()
        if not self.icat_investigation.open_ended:
            inside_timeslot &= self.startdate <= self.icat_investigation.enddate.date()
        return inside_timeslot

    def add_dataset(self, dataset: Dataset) -> None:
        self.datasets[dataset.get_status()].append(dataset)

    def iter_datasets(self) -> Generator[Dataset, None, None]:
        for lst in self.datasets.values():
            yield from lst

    def dataset_statuses(self) -> Set[str]:
        return set(status for status, lst in self.datasets.items() if lst)

    def dataset_status_counts(self) -> Dict[str, int]:
        return {status: len(lst) for status, lst in self.datasets.items()}

    def tabular_data(self) -> Dict[str, str]:
        data = self._init_tabular_data()
        self._add_icat_tabular_data(data)
        return data

    def _init_tabular_data(self) -> Dict[str, str]:
        status_counts = {
            status: str(counts)
            for status, counts in self.dataset_status_counts().items()
        }
        return {
            "beamline": self.beamline,
            "proposal": self.proposal,
            "session": str(self.startdate),
            "url": "",
            "search_url": self.search_url,
            "path": self.raw_root_dir,
            **status_counts,
            "timeslot": "",
        }

    def _add_icat_tabular_data(self, data: Dict[str, str]) -> None:
        if self.icat_investigation is None:
            return
        data["url"] = self.icat_investigation.url
        data["search_url"] = self.icat_investigation.search_url

        if self.in_icat_investigation:
            data["session"] += " (NOT INSIDE TIMESLOT)"
        if not self.icat_investigation.started:
            data["timeslot"] = f"FUTURE: {self.icat_investigation.timeslot}"
        elif self.icat_investigation.ended:
            data["timeslot"] = f"FINISHED: {self.icat_investigation.timeslot}"
        elif self.icat_investigation.open_ended:
            data["timeslot"] = f"INDEFINITE: {self.icat_investigation.timeslot}"
        elif self.icat_investigation.ongoing:
            data["timeslot"] = f"ONGOING: {self.icat_investigation.timeslot}"
        else:
            raise RuntimeError("Unknown session time state")

        return data

    def pprint(self) -> None:
        separator = "--------------------------------"
        logger.debug("")
        logger.debug(separator)
        self._pprint_datasets()
        logger.debug("")
        self._pprint_raw_session()
        logger.debug("")
        self._pprint_icat_investigation()
        logger.debug("")
        logger.debug(separator)
        logger.debug("")

    def _pprint_raw_session(self) -> None:
        logger.debug("Directory: %s", self.raw_root_dir)
        if self.in_icat_investigation:
            logger.debug(" Start time: %s", self.startdate)
        else:
            logger.debug(
                " Start time: %s (WARNING: not inside time slot!)", self.startdate
            )

    def _pprint_icat_investigation(self) -> None:
        if self.icat_investigation is None:
            logger.debug("No corresponding investigation")
            logger.debug(" Search URL: %s", self.search_url)
        else:
            self.icat_investigation.pprint()

    def _pprint_datasets(self) -> None:
        _print_dataset_summary("Registered datasets", self.datasets["registered"])
        _print_dataset_summary("Invalid datasets", self.datasets["invalid"])
        _print_dataset_summary("Unregistered datasets", self.datasets["unregistered"])
        _print_dataset_summary("Datasets not uploaded", self.datasets["not_uploaded"])
        _print_dataset_summary(
            "Registered datasets without files",
            self.datasets["registered_without_files"],
        )

        logger.debug("")
        if (
            self.datasets["invalid"]
            or self.datasets["not_uploaded"]
            or self.datasets["unregistered"]
            or self.datasets["registered_without_files"]
        ):
            logger.debug("Datasets (TODO):")
        elif not self.datasets["registered"]:
            logger.debug("Datasets (EMPTY):")
        else:
            logger.debug("Datasets (OK):")
        logger.debug(" %s registered", len(self.datasets["registered"]))
        logger.debug(" %s invalid", len(self.datasets["invalid"]))
        logger.debug(" %s not uploaded", len(self.datasets["not_uploaded"]))
        logger.debug(" %s unregistered", len(self.datasets["unregistered"]))
        logger.debug(
            " %s registered without files",
            len(self.datasets["registered_without_files"]),
        )


def _print_dataset_summary(title: str, datasets: List[Dataset]):
    if not datasets:
        return
    logger.debug("")
    logger.debug("%s:", title)
    for dataset in datasets:
        logger.debug("  %s", dataset.path)
        for s in dataset.status_reason:
            logger.debug("   %s", s)
