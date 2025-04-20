from typing import List

ELOGBOOK_TOKEN: str = "elogbook-00000000-0000-0000-0000-000000000000"

METADATA_QUEUE: str = "icatIngest"
METADATA_BROKERS: List[str] = ["bcu-mq-01.esrf.fr:61613", "bcu-mq-02.esrf.fr:61613"]

ARCHIVE_QUEUE: str = "icatArchiveRestoreStatus"
ARCHIVE_BROKERS: List[str] = ["bcu-mq-01.esrf.fr:61613", "bcu-mq-02.esrf.fr:61613"]

UPDATE_METADATA_QUEUE: str = "icatUpdateDatasetMetadata"
UPDATE_METADATA_BROKERS: List[str] = [
    "bcu-mq-01.esrf.fr:61613",
    "bcu-mq-02.esrf.fr:61613",
]

ADD_FILES_QUEUE: str = "icatDataFiles"
ADD_FILES_BROKERS: List[str] = [
    "bcu-mq-01.esrf.fr:61613",
    "bcu-mq-02.esrf.fr:61613",
]

RESCHEDULE_INVESTIGATION_QUEUE: str = "rescheduleInvestigation"
RESCHEDULE_INVESTIGATION_BROKERS: List[str] = [
    "bcu-mq-01.esrf.fr:61613",
    "bcu-mq-02.esrf.fr:61613",
]
