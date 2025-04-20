import datetime
import warnings
from typing import List, Mapping, Optional, Sequence, Tuple, Union

import numpy

from . import defaults
from .archive import IcatArchiveStatusClient, StatusLevel, StatusType
from .elogbook import IcatElogbookClient
from .interface import DatasetId, Dataset, IcatClientInterface
from .investigation import IcatInvestigationClient
from .metadata import IcatMetadataClient
from .update_metadata import IcatUpdateMetadataClient
from .add_files import IcatAddFilesClient
from .icatplus_restricted import IcatPlusRestrictedClient


class IcatClient(IcatClientInterface):
    """Client object that provides access to these services:

    - ActiveMQ message broker for creating datasets in ICAT
    - ActiveMQ message broker for updating dataset metadata in ICAT
    - ActiveMQ message broker for updating dataset file count in ICAT
    - ActiveMQ message broker for updating dataset archiving status in ICAT
    - RESTful interface for sending electronic logbook messages/images and get information about investigations

    The RESTful interface is referred to as ICAT+ and the ActiveMQ message brokers are consumed by the "ingesters".
    """

    def __init__(
        self,
        metadata_urls: Optional[List[str]] = None,
        elogbook_url: Optional[str] = None,
        elogbook_token: Optional[str] = None,
        metadata_queue: Optional[str] = None,
        metadata_queue_monitor_port: Optional[int] = None,
        elogbook_timeout: Optional[float] = None,
        feedback_timeout: Optional[float] = None,
        queue_timeout: Optional[float] = None,
        beamline: Optional[str] = None,
        proposal: Optional[str] = None,
        elogbook_metadata: Optional[Mapping] = None,
        archive_urls: Optional[List[str]] = None,
        archive_queue: Optional[str] = None,
        archive_queue_monitor_port: Optional[int] = None,
        update_metadata_urls: Optional[List[str]] = None,
        update_metadata_queue: Optional[str] = None,
        update_metadata_queue_monitor_port: Optional[int] = None,
        add_files_urls: Optional[List[str]] = None,
        add_files_queue: Optional[str] = None,
        add_files_queue_monitor_port: Optional[int] = None,
        reschedule_investigation_urls: Optional[List[str]] = None,
        reschedule_investigation_queue: Optional[str] = None,
        reschedule_investigation_queue_monitor_port: Optional[int] = None,
        icatplus_restricted_url: Optional[str] = None,
        icatplus_password: Optional[str] = None,
        icat_session_id: Optional[str] = None,
        catalogue_queues: Optional[List[str]] = None,  # DEPRECATED
        catalogue_url: Optional[str] = None,  # DEPRECATED
        tracking_url: Optional[str] = None,  # DEPRECATED
    ):
        """
        :param metadata_urls: URLs of the ActiveMQ message brokers to be used for creating ICAT datasets from a directory with metadata.
        :param elogbook_url: URL of the ICAT+ REST server to be used for sending text or images to the electronic logbook and get information about investigations.
        :param elogbook_token: Access token for restricted requests to `elogbook_url`.
        :param metadata_queue: Queue to be used when sending a message to `metadata_urls`.
        :param metadata_queue_monitor_port: REST server port to be used for monitor the `metadata_urls` ActiveMQ message brokers (same host as the message broker).
        :param elogbook_timeout: POST timeout for `elogbook_url`.
        :param feedback_timeout: GET timeout for `elogbook_url`.
        :param queue_timeout: Connection timeout for the ActiveMQ message brokers.
        :param beamline: Default beamline to be used as metadata when sending messages to `metadata_urls` or `elogbook_url`.
        :param proposal: Default proposal to be used as metadata when sending messages to `metadata_urls` or `elogbook_url`.
        :param elogbook_metadata: Default electronic logbook metadata to be used when sending messages to  `elogbook_url`.
        :param archive_urls: URLs of the ActiveMQ message brokers to be used for updating the archival status of ICAT datasets.
        :param archive_queue: Queue to be used when sending a message to `archive_urls`.
        :param archive_queue_monitor_port: REST server port to be used for monitor the `archive_urls` ActiveMQ message brokers (same host as the message broker).
        :param update_metadata_urls: URLs of the ActiveMQ message brokers to be used for update metadata of ICAT datasets.
        :param update_metadata_queue: Queue to be used when sending a message to `update_metadata_urls`.
        :param update_metadata_queue_monitor_port: REST server port to be used for monitor the `update_metadata_urls` ActiveMQ message brokers (same host as the message broker).
        :param add_files_urls: URLs of the ActiveMQ message brokers to be used for updating the file count of ICAT datasets.
        :param add_files_queue: Queue to be used when sending a message to `add_files_urls`.
        :param add_files_queue_monitor_port: REST server port to be used for monitor the `add_files_urls` ActiveMQ message brokers (same host as the message broker).
        :param reschedule_investigation_urls: URLs of the ActiveMQ message brokers to be used for rescheduling investigations.
        :param reschedule_investigation_queue: Queue to be used when sending a message to `reschedule_investigation`.
        :param reschedule_investigation_queue_monitor_port: REST server port to be used for monitor the `reschedule_investigation` ActiveMQ message brokers (same host as the message broker).
        :param icatplus_restricted_url: URL of the ICAT+ REST server to be used for restricted access (requires `icatplus_password` or `do_log_in`).
        :param icatplus_password: Password to provide access to `icatplus_restricted_url`.
        :param icat_session_id: The ICAT's session id that is used for authentication/authorization in ICAT.
        :param catalogue_queues: URLs of the ActiveMQ message brokers to be used for the catalogue (DEPRECATED).
        :param catalogue_url: URL of the ICAT+ REST server to be used for accessing the catalogue (DEPRECATED).
        :param tracking_url: URL of the ICAT+ REST server to be used for accessing the tracking (DEPRECATED).
        """

        # Defaults to be used in client methods
        self.current_proposal = proposal
        self.current_beamline = beamline
        self.current_dataset = None
        self.current_path = None
        self.current_dataset_metadata = None

        # Deprecated constructor arguments
        if catalogue_queues:
            reschedule_investigation_urls = catalogue_queues
            warnings.warn(
                "'catalogue_queues' is deprecated and will be renamed to 'reschedule_investigation_urls'.",
                DeprecationWarning,
            )
        if catalogue_url:
            warnings.warn(
                "'catalogue_url' is deprecated and will be renamed to 'icatplus_restricted_url'.",
                DeprecationWarning,
            )
            if not icatplus_restricted_url:
                icatplus_restricted_url = catalogue_url
        if tracking_url:
            warnings.warn(
                "'tracking_url' is deprecated and will be renamed to 'icatplus_restricted_url'.",
                DeprecationWarning,
            )
            if not icatplus_restricted_url:
                icatplus_restricted_url = tracking_url

        # Initialize clients to message brokers or REST URL's.
        # Clients with missing parameters are not initialized
        # add the associated client methods will raise an error.
        self._init_metadata_client(
            brokers=metadata_urls,
            queue=metadata_queue,
            timeout=queue_timeout,
            monitor_port=metadata_queue_monitor_port,
        )
        self._init_archive_client(
            brokers=archive_urls,
            queue=archive_queue,
            timeout=queue_timeout,
            monitor_port=archive_queue_monitor_port,
        )
        self._init_update_metadata_client(
            brokers=update_metadata_urls,
            queue=update_metadata_queue,
            timeout=queue_timeout,
            monitor_port=update_metadata_queue_monitor_port,
        )
        self._init_elogbook_client(
            url=elogbook_url,
            api_key=elogbook_token,
            timeout=elogbook_timeout,
            metadata=elogbook_metadata,
        )
        self._init_investigation_client(
            url=elogbook_url,
            api_key=elogbook_token,
            timeout=feedback_timeout,
        )
        self._init_add_files_client(
            brokers=add_files_urls,
            queue=add_files_queue,
            timeout=queue_timeout,
            monitor_port=add_files_queue_monitor_port,
        )
        self._init_reschedule_investigation_client(
            brokers=reschedule_investigation_urls,
            queue=reschedule_investigation_queue,
            timeout=queue_timeout,
            monitor_port=reschedule_investigation_queue_monitor_port,
        )
        self._init_icatplus_restricted_client(
            url=icatplus_restricted_url,
            password=icatplus_password,
            session_id=icat_session_id,
        )

    def disconnect(self):
        if self.__metadata_client is not None:
            self.__metadata_client.disconnect()
        if self.__update_metadata_client is not None:
            self.__update_metadata_client.disconnect()
        if self.__add_files_client is not None:
            self.__add_files_client.disconnect()
        if self.__archive_client is not None:
            self.__archive_client.disconnect()
        if self.__reschedule_investigation_client is not None:
            self.__reschedule_investigation_client.disconnect()

    @property
    def metadata_client(self):
        warnings.warn(
            "Will be removed in the next release.", DeprecationWarning, stacklevel=2
        )
        return self._metadata_client

    @property
    def _metadata_client(self):
        if self.__metadata_client is None:
            raise RuntimeError("The message queue URL's are missing")
        return self.__metadata_client

    def _init_metadata_client(
        self,
        brokers: Optional[List[str]] = None,
        queue: Optional[str] = None,
        timeout: Optional[float] = None,
        monitor_port: Optional[int] = None,
    ):
        if brokers:
            self.__metadata_client = IcatMetadataClient(
                queue_urls=brokers,
                queue_name=queue,
                monitor_port=monitor_port,
                timeout=timeout,
            )
        else:
            self.__metadata_client = None

    @property
    def elogbook_client(self):
        warnings.warn(
            "Will be removed in the next release.", DeprecationWarning, stacklevel=2
        )
        return self._elogbook_client

    @property
    def _elogbook_client(self):
        if self.__elogbook_client is None:
            raise RuntimeError("The ICAT+ URL and/or token are missing")
        return self.__elogbook_client

    def _init_elogbook_client(
        self,
        url: Optional[str] = None,
        api_key: Optional[str] = None,
        timeout: Optional[float] = None,
        metadata: Optional[dict] = None,
    ):
        if url and api_key:
            if metadata is None:
                metadata = dict()
            self.__elogbook_client = IcatElogbookClient(
                url=url,
                api_key=api_key,
                timeout=timeout,
                **metadata,
            )
        else:
            self.__elogbook_client = None

    @property
    def investigation_client(self):
        warnings.warn(
            "Will be removed in the next release.", DeprecationWarning, stacklevel=2
        )
        return self._investigation_client

    @property
    def _investigation_client(self):
        if self.__investigation_client is None:
            raise RuntimeError("The ICAT+ URL and/or token are missing")
        return self.__investigation_client

    def _init_investigation_client(
        self,
        url: Optional[str] = None,
        api_key: Optional[str] = None,
        timeout: Optional[float] = None,
    ):
        if url and api_key:
            self.__investigation_client = IcatInvestigationClient(
                url=url, api_key=api_key, timeout=timeout
            )
        else:
            self.__investigation_client = None

    @property
    def archive_client(self):
        warnings.warn(
            "Will be removed in the next release.", DeprecationWarning, stacklevel=2
        )
        return self._archive_client

    @property
    def _archive_client(self):
        if self.__archive_client is None:
            raise RuntimeError("The message queue URL's are missing")
        return self.__archive_client

    def _init_archive_client(
        self,
        brokers: Optional[List[str]] = None,
        queue: Optional[str] = None,
        timeout: Optional[float] = None,
        monitor_port: Optional[int] = None,
    ):
        if brokers:
            self.__archive_client = IcatArchiveStatusClient(
                queue_urls=brokers,
                queue_name=queue,
                monitor_port=monitor_port,
                timeout=timeout,
            )
        else:
            self.__archive_client = None

    @property
    def update_metadata_client(self):
        warnings.warn(
            "Will be removed in the next release.", DeprecationWarning, stacklevel=2
        )

    @property
    def _update_metadata_client(self):
        if self.__update_metadata_client is None:
            raise RuntimeError("The message queue URL's are missing")
        return self.__update_metadata_client

    def _init_update_metadata_client(
        self,
        brokers: Optional[List[str]] = None,
        queue: Optional[str] = None,
        timeout: Optional[float] = None,
        monitor_port: Optional[int] = None,
    ):
        if brokers:
            self.__update_metadata_client = IcatUpdateMetadataClient(
                queue_urls=brokers,
                queue_name=queue,
                monitor_port=monitor_port,
                timeout=timeout,
            )
        else:
            self.__update_metadata_client = None

    @property
    def add_files_client(self):
        warnings.warn(
            "Will be removed in the next release.", DeprecationWarning, stacklevel=2
        )
        return self._add_files_client

    @property
    def _add_files_client(self):
        if self.__add_files_client is None:
            raise RuntimeError("The message queue URL's are missing")
        return self.__add_files_client

    def _init_add_files_client(
        self,
        brokers: Optional[List[str]] = None,
        queue: Optional[str] = None,
        timeout: Optional[float] = None,
        monitor_port: Optional[int] = None,
    ):
        if brokers:
            self.__add_files_client = IcatAddFilesClient(
                queue_urls=brokers,
                queue_name=queue,
                monitor_port=monitor_port,
                timeout=timeout,
            )
        else:
            self.__add_files_client = None

    @property
    def catalogue_client(self):
        warnings.warn(
            "Will be removed in the next release.", DeprecationWarning, stacklevel=2
        )
        return self

    @property
    def tracking_client(self):
        warnings.warn(
            "Will be removed in the next release.", DeprecationWarning, stacklevel=2
        )
        return self

    @property
    def _icatplus_authentication_client(self):
        if self.__icatplus_authentication_client is None:
            raise RuntimeError("The ICAT+ URL and/or token are missing")
        return self.__icatplus_authentication_client

    @property
    def _icatplus_restricted_client(self):
        if self.__icatplus_restricted_client is None:
            raise RuntimeError("The ICAT+ URL is missing and/or login")
        return self.__icatplus_restricted_client

    def _init_icatplus_restricted_client(
        self,
        url: Optional[str] = None,
        password: Optional[str] = None,
        session_id: Optional[str] = None,
    ):
        if url:
            self.__icatplus_restricted_client = IcatPlusRestrictedClient(
                url=url, password=password, session_id=session_id
            )
        else:
            self.__icatplus_restricted_client = None

    @property
    def _reschedule_investigation_client(self):
        if self.__reschedule_investigation_client is None:
            raise RuntimeError("The message queue URL's are missing")
        return self.__reschedule_investigation_client

    def _init_reschedule_investigation_client(
        self,
        brokers: Optional[List[str]] = None,
        queue: Optional[str] = None,
        timeout: Optional[float] = None,
        monitor_port: Optional[int] = None,
    ):
        if brokers:
            self.__reschedule_investigation_client = IcatMetadataClient(
                queue_urls=brokers or defaults.RESCHEDULE_INVESTIGATION_BROKERS,
                queue_name=queue or defaults.RESCHEDULE_INVESTIGATION_QUEUE,
                monitor_port=monitor_port,
                timeout=timeout,
            )
        else:
            self.__reschedule_investigation_client = None

    @property
    def current_proposal(self):
        return self.__current_proposal

    @current_proposal.setter
    def current_proposal(self, value: Optional[str]):
        self.__current_proposal = value

    @property
    def current_beamline(self):
        return self.__current_beamline

    @current_beamline.setter
    def current_beamline(self, value: Optional[str]):
        self.__current_beamline = value

    @property
    def current_dataset(self):
        return self.__current_dataset

    @current_dataset.setter
    def current_dataset(self, value: Optional[str]):
        self.__current_dataset = value

    @property
    def current_dataset_metadata(self):
        return self.__current_dataset_metadata

    @current_dataset_metadata.setter
    def current_dataset_metadata(self, value: Optional[dict]):
        self.__current_dataset_metadata = value

    @property
    def current_path(self):
        return self.__current_path

    @current_path.setter
    def current_path(self, value: Optional[str]):
        self.__current_path = value

    def send_message(
        self,
        msg: str,
        msg_type: Optional[str] = None,
        beamline: Optional[str] = None,
        proposal: Optional[str] = None,
        dataset: Optional[str] = None,
        beamline_only: Optional[bool] = None,
        editable: Optional[bool] = None,
        formatted: Optional[bool] = None,
        mimetype: Optional[str] = None,
        **payload,
    ):
        if beamline_only:
            proposal = None
        elif proposal is None:
            proposal = self.current_proposal
        if beamline is None:
            beamline = self.current_beamline
        if beamline_only:
            dataset = None
        elif dataset is None:
            dataset = self.current_dataset
        self._elogbook_client.send_message(
            message=msg,
            message_type=msg_type,
            beamline=beamline,
            proposal=proposal,
            dataset=dataset,
            editable=editable,
            formatted=formatted,
            mimetype=mimetype,
            **payload,
        )

    def send_binary_data(
        self,
        data: bytes,
        mimetype: Optional[str] = None,
        beamline: Optional[str] = None,
        proposal: Optional[str] = None,
        beamline_only: Optional[bool] = None,
        **payload,
    ):
        if beamline_only:
            proposal = None
        elif proposal is None:
            proposal = self.current_proposal
        if beamline is None:
            beamline = self.current_beamline
        self._elogbook_client.send_binary_data(
            data, mimetype=mimetype, beamline=beamline, proposal=proposal, **payload
        )

    def send_text_file(
        self,
        filename: str,
        beamline: Optional[str] = None,
        proposal: Optional[str] = None,
        dataset: Optional[str] = None,
        beamline_only: Optional[bool] = None,
        **payload,
    ):
        if beamline_only:
            proposal = None
        elif proposal is None:
            proposal = self.current_proposal
        if beamline is None:
            beamline = self.current_beamline
        if beamline_only:
            dataset = None
        elif dataset is None:
            dataset = self.current_dataset
        self._elogbook_client.send_text_file(
            filename, beamline=beamline, proposal=proposal, dataset=dataset, **payload
        )

    def send_binary_file(
        self,
        filename: str,
        beamline: Optional[str] = None,
        proposal: Optional[str] = None,
        beamline_only: Optional[bool] = None,
        **payload,
    ):
        if beamline_only:
            proposal = None
        elif proposal is None:
            proposal = self.current_proposal
        if beamline is None:
            beamline = self.current_beamline
        self._elogbook_client.send_binary_file(
            filename, beamline=beamline, proposal=proposal, **payload
        )

    def start_investigation(
        self,
        beamline: Optional[str] = None,
        proposal: Optional[str] = None,
        start_datetime=None,
        end_datetime=None,
    ):
        if proposal is None:
            proposal = self.current_proposal
        else:
            self.current_proposal = proposal
        if beamline is None:
            beamline = self.current_beamline
        else:
            self.current_beamline = beamline
        self._metadata_client.start_investigation(
            beamline=beamline,
            proposal=proposal,
            start_datetime=start_datetime,
            end_datetime=end_datetime,
        )

    def store_dataset(
        self,
        beamline: Optional[str] = None,
        proposal: Optional[str] = None,
        dataset: Optional[str] = None,
        path: Optional[str] = None,
        metadata: dict = None,
        store_filename: Optional[str] = None,
    ):
        if proposal is None:
            proposal = self.current_proposal
        if beamline is None:
            beamline = self.current_beamline
        if dataset is None:
            dataset = self.current_dataset
        if path is None:
            path = self.current_path
        if metadata is None:
            metadata = self.current_dataset_metadata
            if metadata is None:
                metadata = dict()
        if store_filename:
            self._metadata_client.store_metadata(
                store_filename,
                beamline=beamline,
                proposal=proposal,
                dataset=dataset,
                path=path,
                metadata=metadata,
            )
        else:
            self._metadata_client.send_metadata(
                beamline=beamline,
                proposal=proposal,
                dataset=dataset,
                path=path,
                metadata=metadata,
            )

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
        """The 'raw' argument is shorthand for `metadata = {'input_datasets': ...}`."""
        if metadata is None:
            metadata = self.current_dataset_metadata
            if metadata is None:
                metadata = dict()
        if raw:
            if isinstance(raw, str):
                metadata["input_datasets"] = [raw]
            elif isinstance(raw, Sequence):
                metadata["input_datasets"] = list(raw)
            else:
                metadata["input_datasets"] = [raw]
        if not metadata.get("input_datasets"):
            raise ValueError("Provide 'raw' dataset directories")
        self.store_dataset(
            beamline=beamline,
            proposal=proposal,
            dataset=dataset,
            path=path,
            metadata=metadata,
            store_filename=store_filename,
        )

    def store_dataset_from_file(self, store_filename: Optional[str] = None):
        self._metadata_client.send_metadata_from_file(store_filename)

    def investigation_info(
        self,
        beamline: str,
        proposal: str,
        date: Optional[Union[datetime.datetime, datetime.date]] = None,
        allow_open_ended: bool = True,
        timeout: Optional[float] = None,
    ) -> Optional[dict]:
        return self._investigation_client.investigation_info(
            beamline=beamline,
            proposal=proposal,
            date=date,
            allow_open_ended=allow_open_ended,
            timeout=timeout,
        )

    def registered_dataset_ids(
        self,
        beamline: str,
        proposal: str,
        date: Optional[Union[datetime.datetime, datetime.date]] = None,
        allow_open_ended: bool = True,
        timeout: Optional[float] = None,
    ) -> Optional[List[DatasetId]]:
        return self._investigation_client.registered_dataset_ids(
            beamline=beamline,
            proposal=proposal,
            date=date,
            allow_open_ended=allow_open_ended,
            timeout=timeout,
        )

    def registered_datasets(
        self,
        beamline: str,
        proposal: str,
        date: Optional[Union[datetime.datetime, datetime.date]] = None,
        allow_open_ended: bool = True,
        timeout: Optional[float] = None,
    ) -> Optional[List[Dataset]]:
        return self._investigation_client.registered_datasets(
            beamline=beamline,
            proposal=proposal,
            date=date,
            allow_open_ended=allow_open_ended,
            timeout=timeout,
        )

    def investigation_info_string(
        self,
        beamline: str,
        proposal: str,
        date: Optional[Union[datetime.datetime, datetime.date]] = None,
        allow_open_ended: bool = True,
        timeout: Optional[float] = None,
    ) -> str:
        info = self.investigation_info(
            beamline=beamline,
            proposal=proposal,
            date=date,
            allow_open_ended=allow_open_ended,
            timeout=timeout,
        )
        if info:
            rows = [(str(k), str(v)) for k, v in info.items()]
            lengths = numpy.array([[len(s) for s in row] for row in rows])
            fmt = "   ".join(["{{:<{}}}".format(n) for n in lengths.max(axis=0)])
            infostr = "ICAT proposal time slot:\n "
            infostr += "\n ".join([fmt.format(*row) for row in rows])
        elif info is None:
            infostr = f"Proposal information currently not available ({self.reason_for_missing_information})"
        else:
            infostr = "Proposal NOT available in the data portal"
        return infostr

    def investigation_summary(
        self,
        beamline: str,
        proposal: str,
        date: Optional[Union[datetime.datetime, datetime.date]] = None,
        allow_open_ended: bool = True,
        timeout: Optional[float] = None,
    ) -> List[Tuple]:
        info = self.investigation_info(
            beamline=beamline,
            proposal=proposal,
            date=date,
            allow_open_ended=allow_open_ended,
            timeout=timeout,
        )
        keys = ["e-logbook", "data portal"]
        if info:
            rows = [(key, info[key]) for key in keys]
        elif info is None:
            rows = [
                (
                    key,
                    f"Proposal information currently not available ({self.reason_for_missing_information})",
                )
                for key in keys
            ]
        else:
            rows = [(key, "Proposal NOT available in the data portal") for key in keys]
        return rows

    def update_archive_restore_status(
        self,
        dataset_id: int = None,
        type: StatusType = None,
        level: StatusLevel = StatusLevel.INFO,
        message: Optional[str] = None,
    ):
        self._archive_client.send_archive_status(
            dataset_id=dataset_id, type=type, level=level, message=message
        )

    def update_metadata(
        self,
        proposal: str = None,
        beamline: str = None,
        dataset_paths: str = None,
        metadata_name: str = None,
        metadata_value: str = None,
    ):
        if proposal is None:
            proposal = self.current_proposal
        if beamline is None:
            beamline = self.current_beamline
        self.__update_metadata_client.send_update_metadata(
            proposal=proposal,
            beamline=beamline,
            dataset_paths=dataset_paths,
            metadata_name=metadata_name,
            metadata_value=metadata_value,
        )

    def add_files(
        self,
        dataset_id: int = None,
    ):
        self.__add_files_client.add_files(
            dataset_id=dataset_id,
        )

    def reschedule_investigation(self, investigation_id: str):
        self._reschedule_investigation_client.reschedule_investigation(investigation_id)

    def do_log_in(self, password: str) -> dict:
        return self._icatplus_restricted_client.login(password)

    def get_investigations_by(
        self,
        filter: Optional[str] = None,
        instrument_name: Optional[str] = None,
        start_date: Optional[datetime.datetime] = None,
        end_date: Optional[datetime.datetime] = None,
        ids: Optional[str] = None,
    ) -> List[dict]:
        return self._icatplus_restricted_client.get_investigations_by(
            filter=filter,
            instrument_name=instrument_name,
            start_date=start_date,
            end_date=end_date,
            ids=ids,
        )

    def get_samples_by(
        self,
        investigationId: Optional[str] = None,
        sampleIds: Optional[str] = None,
    ) -> List[dict]:
        return self._icatplus_restricted_client.get_samples_by(
            investigationId=investigationId, sampleIds=sampleIds
        )

    def get_parcels_by(self, investigation_id: str) -> List[dict]:
        return self._icatplus_restricted_client.get_parcels_by(investigation_id)

    def get_session_information(self) -> dict:
        return self._icatplus_restricted_client.get_session_information()

    @property
    def expire_datasets_on_close(self) -> bool:
        return False

    @property
    def reason_for_missing_information(self) -> str:
        return "ICAT communication timeout"
