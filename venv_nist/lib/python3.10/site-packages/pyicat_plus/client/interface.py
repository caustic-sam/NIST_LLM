import datetime
import dataclasses
from typing import Optional, List, Tuple, Sequence, Union, Dict, Any
from .archive import StatusLevel, StatusType


@dataclasses.dataclass(frozen=True)
class DatasetId:
    name: str
    path: str


@dataclasses.dataclass(frozen=True)
class DatasetMetadata:
    file_count: int


@dataclasses.dataclass(frozen=True)
class Dataset:
    dataset_id: DatasetId
    icat_dataset_id: int
    dataset_metadata: DatasetMetadata

    def as_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Dataset":
        """Factory method to create a Dataset instance from a dictionary."""
        data = data.copy()
        data["dataset_id"] = DatasetId(**data["dataset_id"])
        data["dataset_metadata"] = DatasetMetadata(**data["dataset_metadata"])
        return cls(**data)


class IcatClientInterface:
    def send_message(
        self,
        msg: str,
        msg_type: Optional[str] = None,
        beamline: Optional[str] = None,
        proposal: Optional[str] = None,
        dataset: Optional[str] = None,
        beamline_only: Optional[bool] = None,
        **payload,
    ):
        """
        Send a message to the proposal or beamline e-logbook.

        :param msg: The message content.
        :param msg_type: {'comment', 'debug', 'info', 'error', 'commandLine'}, optional.
        :param beamline: The beamline name of the proposal or beamline e-logbook.
        :param proposal: The proposal name of the e-logbook. Ignored if `beamline_only` is True.
        :param dataset: The specific dataset name to link the message to.
        :param beamline_only: if `True`, the message will be stored in the beamline e-logbook.
        :param editable: Used with the `formatted` field, to determine the category of message. Annotation characterizes editable and unformatted messages, while Notification charaterizes non-editable and formatted messages.
        :param formatted: Used with the `editable` field, to determine the category of message. Annotation characterizes editable and unformatted messages, while Notification charaterizes non-editable and formatted messages.
        :param mimetype: {'text/plain', 'text/html'}, optional.
        :param payload: Additional payload for the message. It can contain tags (list of strings or list of dictionaries), the machine, the software.
        """
        raise NotImplementedError

    def disconnect(self):
        pass

    def send_binary_data(
        self,
        data: bytes,
        mimetype: Optional[str] = None,
        beamline: Optional[str] = None,
        proposal: Optional[str] = None,
        beamline_only: Optional[bool] = None,
        **payload,
    ):
        """
        Send an image in base64 format to the proposal or beamline e-logbook.

        :param data: The binary message content.
        :param mimetype: {'text/plain', 'text/html'}, optional.
        :param beamline: The beamline name of the proposal or beamline e-logbook.
        :param proposal: The proposal name of the e-logbook. Ignored if `beamline_only` is True.
        :param beamline_only: if `True`, the message will be stored in the beamline e-logbook.
        :param payload: Additional payload for the e-logbook message. It can contain tags (list of strings or list of dictionaries), the machine, the software.
        """
        raise NotImplementedError

    def send_text_file(
        self,
        filename: str,
        beamline: Optional[str] = None,
        proposal: Optional[str] = None,
        dataset: Optional[str] = None,
        beamline_only: Optional[bool] = None,
        **payload,
    ):
        """
        Send the content of a text file as a message to the proposal or beamline e-logbook.

        :param filename: The filename containing the message to be sent.
        :param beamline: The beamline name of the proposal or beamline e-logbook.
        :param proposal: The proposal name of the e-logbook. Ignored if `beamline_only` is True.
        :param beamline_only: if `True`, the message will be stored in the beamline e-logbook.
        :param payload: Additional payload for the e-logbook message. It can contain tags (list of strings or list of dictionaries), the machine, the software.
        """
        raise NotImplementedError

    def send_binary_file(
        self,
        filename: str,
        beamline: Optional[str] = None,
        proposal: Optional[str] = None,
        beamline_only: Optional[bool] = None,
        **payload,
    ):
        """
        Send the content of a file as a binary image to the proposal or beamline e-logbook.

        :param filename: The filename of the image to be sent.
        :param beamline: The beamline name of the proposal or beamline e-logbook.
        :param proposal: The proposal name of the e-logbook. Ignored if `beamline_only` is True.
        :param beamline_only: if `True`, the message will be stored in the beamline e-logbook.
        :param payload: Additional payload for the e-logbook message. It can contain tags (list of strings or list of dictionaries), the machine, the software.
        """
        raise NotImplementedError

    def start_investigation(
        self,
        beamline: Optional[str] = None,
        proposal: Optional[str] = None,
        start_datetime=None,
        end_datetime=None,
    ):
        """
        Send a message to ActiveMQ to either synchronize the experiment session from the User Portal in ICAT or to create a test experiment session in ICAT.

        :param beamline: The beamline name of the proposal.
        :param proposal: The proposal name.
        :param start_datetime: The start date of the experiment session, timezone local time. Current date time by default.
        :param end_datetime: The end date of the experiment session, timezone local time.
        """
        raise NotImplementedError

    def store_dataset(
        self,
        beamline: Optional[str] = None,
        proposal: Optional[str] = None,
        dataset: Optional[str] = None,
        path: Optional[str] = None,
        metadata: dict = None,
        store_filename: Optional[str] = None,
    ):
        """
        Request icat to store raw dataset.

        :param beamline str: beamline name like id01, id15a, bm18...
        :param proposal str: proposal name like in1169, blc14795, ihma429...
        :param str dataset: dataset name.
        :param str path: path to the raw dataset to store. Must be a folder.
        :param dict metadata: metadata to associate to the dataset. Must contains keys defined by the appropriate application definition from https://gitlab.esrf.fr/icat/hdf5-master-config/-/blob/88a975039694d5dba60e240b7bf46c22d34065a0/hdf5_cfg.xml.
        :param str store_filename: xml file with metadata to be stored.
        """

        raise NotImplementedError

    def store_processed_data(
        self,
        beamline: Optional[str] = None,
        proposal: Optional[str] = None,
        dataset: Optional[str] = None,
        path: Optional[str] = None,
        metadata: dict = None,
        raw: Sequence = tuple(),
        store_filename: Optional[str] = None,
    ):
        """
        Request icat to store a processed dataset.

        :param beamline str: beamline name like id01, id15a, bm18...
        :param proposal str: proposal name like in1169, blc14795, ihma429...
        :param str dataset: dataset name like sample_XYZ.
        :param str path: path to the processed dataset to store. Can be a file or a folder.
        :param dict metadata: metadata to associate to the dataset. Must contains keys defined by the appropriate application definition from https://gitlab.esrf.fr/icat/hdf5-master-config/-/blob/88a975039694d5dba60e240b7bf46c22d34065a0/hdf5_cfg.xml.
        :param tuple raw: Path to the raw dataset(s). Expects to be path to 'bliss dataset' folder(s). See https://confluence.esrf.fr/display/BM02KW/File+structure for
                          If processing rely on more than one dataset then all dataset folders must be provided.
        :param str store_filename: xml file with metadata to be stored.
        """
        raise NotImplementedError

    def store_dataset_from_file(self, store_filename: Optional[str] = None):
        """
        Send a message to ActiveMQ to store a dataset and the associated metadata from a xml file stored on the disk.

        :param store_filename: The XML filename containing all dataset metadata.
        """
        raise NotImplementedError

    def investigation_info(
        self,
        beamline: str,
        proposal: str,
        date: Optional[Union[datetime.datetime, datetime.date]] = None,
        allow_open_ended: bool = True,
        timeout: Optional[float] = None,
    ) -> Optional[dict]:
        """
        Return the information of the experiment session corresponding to a beamline, proposal and date.

        :param beamline: The beamline name.
        :param proposal: The proposal name.
        :param date: The date of the proposal, current date by default.
        :param allow_open_ended: If `True`, enable to select an unofficial experiment session.
        :param timeout: Set a timeout for the ICAT request.
        :returns: If found, return the proposal, beamline, e-logbbok url and data portal url of the experiment session.
        """
        raise NotImplementedError

    def registered_dataset_ids(
        self,
        beamline: str,
        proposal: str,
        date: Optional[Union[datetime.datetime, datetime.date]] = None,
        allow_open_ended: bool = True,
        timeout: Optional[float] = None,
    ) -> Optional[List[DatasetId]]:
        """
        Return the dataset list of an experiment session.

        :param beamline: The beamline name of the proposal.
        :param proposal: The proposal name.
        :param date: The date of the proposal, current date by default.
        :param allow_open_ended: If `True`, enable to select an unofficial experiment session.
        :param timeout: Set a timeout for the ICAT request.
        :returns: The list of datasets (name and path).
        """
        raise NotImplementedError

    def registered_datasets(
        self,
        beamline: str,
        proposal: str,
        date: Optional[Union[datetime.datetime, datetime.date]] = None,
        allow_open_ended: bool = True,
        timeout: Optional[float] = None,
    ) -> Optional[List[Dataset]]:
        """
        Return the dataset information list of an experiment session.

        :param beamline: The beamline name of the proposal.
        :param proposal: The proposal name.
        :param date: The date of the proposal, current date by default.
        :param allow_open_ended: If `True`, enable to select an unofficial experiment session.
        :param timeout: Set a timeout for the ICAT request.
        :returns: The list of datasets (name, path, ICAT identifier, and :class:`.DatasetMetadata`).
        """
        raise NotImplementedError

    def investigation_info_string(
        self,
        beamline: str,
        proposal: str,
        date: Optional[Union[datetime.datetime, datetime.date]] = None,
        allow_open_ended: bool = True,
        timeout: Optional[float] = None,
    ) -> str:
        """
        Return the experiment session information as a string.

        :param beamline: The beamline name.
        :param proposal: The proposal name.
        :param date: The date of the proposal, current date by default.
        :param allow_open_ended: If `True`, enable to select an unofficial experiment session.
        :param timeout: Set a timeout for the ICAT request.
        :returns: If found, return the experiment session information from the metadata catalog as a string.
        """
        raise NotImplementedError

    def investigation_summary(
        self,
        beamline: str,
        proposal: str,
        date: Optional[Union[datetime.datetime, datetime.date]] = None,
        allow_open_ended: bool = True,
        timeout: Optional[float] = None,
    ) -> List[Tuple]:
        """
        Return the experiment session information as a `Tuple`.

        :param beamline: The beamline name.
        :param proposal: The proposal name.
        :param date: The date of the proposal, current date by default.
        :param allow_open_ended: If `True`, enable to select an unofficial experiment session.
        :param timeout: Set a timeout for the ICAT request.
        :returns: If found, return the experiment session information from the metadata catalog as a `Tuple`.
        """
        raise NotImplementedError

    def update_archive_restore_status(
        self,
        dataset_id: int = None,
        type: StatusType = None,
        level: StatusLevel = StatusLevel.INFO,
        message: Optional[str] = None,
    ):
        """
        Update the archiving or restore status of a dataset.

        :param dataset_id: The ICAT dataset identifier.
        :param type: The type of the status, possible options are {'archiving', 'restoration'}.
        :param level: The level of the status message; possible options are {'info', 'warning', 'error'}.
        :param message: The optional status' message.
        """
        raise NotImplementedError

    def update_metadata(
        self,
        proposal: str = None,
        beamline: str = None,
        dataset_paths: str = None,
        metadata_name: str = None,
        metadata_value: str = None,
    ):
        """
        Update or create datasets metadata.

        :param proposal: The proposal name.
        :param beamline: The beamline name of the proposal.
        :param dataset_paths: Comma-separated list of the dataset locations.
        :param metadata_name: The name of the metadata to update.
        :param metadata_value: The new value of the metadata.
        """
        raise NotImplementedError

    def add_files(
        self,
        dataset_id: int = None,
    ):
        """
        Add missing files to a dataset already ingested.

        :param dataset_id: The ICAT dataset identifier.
        """
        raise NotImplementedError

    def reschedule_investigation(self, investigation_id: str):
        """
        Reschedule an investigation defined by its id.

        :param investigation_id:
        """
        raise NotImplementedError

    def do_log_in(self, password: str) -> dict:
        """
        Login to access the restricted part of the API.

        :param password:
        :returns: authentication info
        """
        raise NotImplementedError

    def get_investigations_by(
        self,
        filter: Optional[str] = None,
        instrument_name: Optional[str] = None,
        start_date: Optional[datetime.datetime] = None,
        end_date: Optional[datetime.datetime] = None,
        ids: Optional[str] = None,
    ) -> List[dict]:
        """
        Returns a list of investigations matching the provided criteria.

        :param filter:
        :param instrument_name:
        :param start_date:
        :param end_date:
        :param ids:
        :returns: list of investigations
        """
        raise NotImplementedError

    def get_samples_by(
        self,
        investigationId: Optional[str] = None,
        sampleIds: Optional[str] = None,
    ) -> List[dict]:
        """
        Returns a list of samples matching the provided criteria.

        :param investigationId: an investigationId
        :param sampleIds: comma separated list of sampleIds
        """
        return NotImplementedError

    def get_parcels_by(self, investigation_id: str) -> List[dict]:
        """
        Returns the list of parcels associated to an investigation.

        :param investigation_id:
        :returns: list of parcel information
        """
        raise NotImplementedError

    def get_session_information(self) -> List[dict]:
        """
        Returns the information about a session.

        :returns: session information
        """
        raise NotImplementedError

    @property
    def expire_datasets_on_close(self) -> bool:
        """
        A flag indicating whether the dataset expires when it is closed or if it is synchronized with the metadata catalog.
        """
        raise NotImplementedError

    @property
    def reason_for_missing_information(self) -> str:
        """
        A string explaining why some information is missing in the metadata catalog.
        """
        raise NotImplementedError
