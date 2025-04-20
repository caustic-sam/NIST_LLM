from typing import List, Tuple
from .interface import IcatClientInterface


class IcatNullClient(IcatClientInterface):
    def __init__(self, expire_datasets_on_close=True) -> None:
        self.__expire_datasets_on_close = expire_datasets_on_close

    def disconnect(self):
        pass

    def send_message(self, *args, **kw):
        pass

    def send_binary_data(self, *args, **kw):
        pass

    def send_text_file(self, *args, **kw):
        pass

    def send_binary_file(self, *args, **kw):
        pass

    def start_investigation(self, *args, **kw):
        pass

    def store_dataset(self, *args, **kw):
        pass

    def store_processed_data(self, *args, **kw):
        pass

    def store_dataset_from_file(self, *args, **kw):
        pass

    def investigation_info(self, *args, **kwargs) -> None:
        pass

    def registered_dataset_ids(self, *args, **kwargs) -> None:
        pass

    def registered_datasets(self, *args, **kwargs) -> None:
        pass

    def investigation_info_string(self, *args, **kwargs) -> str:
        return f"Proposal information not available ({self.reason_for_missing_information})"

    def investigation_summary(self, *args, **kwargs) -> List[Tuple]:
        keys = ["e-logbook", "data portal"]
        return [
            (key, f"information not available ({self.reason_for_missing_information})")
            for key in keys
        ]

    def update_archive_restore_status(self, *args, **kwargs) -> None:
        pass

    def update_metadata(self, *args, **kwargs) -> None:
        pass

    def add_files(self, *args, **kwargs) -> None:
        pass

    def reschedule_investigation(self, *args, **kwargs) -> None:
        pass

    def do_log_in(self, *args, **kwargs) -> None:
        pass

    def get_investigations_by(self, *args, **kwargs) -> None:
        pass

    def get_parcels_by(self, *args, **kwargs) -> None:
        pass

    def get_session_information(self, *args, **kwargs) -> None:
        pass

    @property
    def expire_datasets_on_close(self) -> bool:
        return self.__expire_datasets_on_close

    @property
    def reason_for_missing_information(self) -> str:
        if self.__expire_datasets_on_close:
            return "ICAT is not configured"
        else:
            return "ICAT communication is disabled"
