import os
import json
import logging
import datetime
from glob import glob
from contextlib import contextmanager
from typing import Optional, Dict, List, Generator


logger = logging.getLogger("STOMP SUBSCRIBER")


class IcatDb:
    def __init__(self, root_dir: Optional[str] = None):
        if root_dir is None:
            root_dir = "."
        if root_dir:
            os.makedirs(root_dir, exist_ok=True)
        self._root_dir = root_dir
        self._investigations = os.path.join(root_dir, "investigations.json")

    def start_investigation(self, investigation: dict) -> None:
        investigation["instrument"] = {"name": investigation["instrument"]}
        investigation["proposal"] = investigation["experiment"]
        _add_table(self._investigations, investigation)

    def store_dataset(self, dataset: dict) -> None:
        investigation = _find_data(self._investigations, dataset["startDate"])
        if investigation is None:
            logger.error(
                "Dataset not stored because no investigation found: %s", dataset
            )
            return
        filename = os.path.join(self._root_dir, f"datasets{investigation['id']}.json")
        dataset["id"] = self._get_next_dataset_id()
        _add_table(filename, dataset)

    @contextmanager
    def update_dataset(self, dataset_id: int) -> Generator[dict, None, None]:
        for filename in glob(os.path.join(self._root_dir, "datasets*.json")):
            data = _get_row(filename, dataset_id)
            if data is None:
                continue
            yield data
            _edit_table(filename, data)
            break
        else:
            yield

    def get_all_investigations(self) -> List[Dict]:
        return _read_table(self._investigations)

    def get_investigations(self, instrument: str, experiment: str) -> List[Dict]:
        return [
            investigation
            for investigation in _read_table(self._investigations)
            if experiment == investigation["experiment"]
            and instrument == investigation["instrument"]["name"]
        ]

    def get_datasets(self, investigation_id) -> List[Dict]:
        filename = os.path.join(self._root_dir, f"datasets{investigation_id}.json")
        return _read_table(filename)

    def _get_next_dataset_id(self) -> int:
        max_ids = [
            _get_max_id(filename)
            for filename in glob(os.path.join(self._root_dir, "datasets*.json"))
        ]
        if max_ids:
            return max(max_ids) + 1
        return 0


def _read_table(filename: str) -> List[dict]:
    if not os.path.isfile(filename):
        return list()
    with open(filename, "r") as f:
        return json.load(f)


def _write_table(filename: str, data: dict) -> Dict:
    with open(filename, "w") as f:
        json.dump(data, f)
    logger.info("Metadata in %s updated", filename)


def _add_table(filename: str, data: dict) -> None:
    table = _read_table(filename)
    if "id" not in data:
        if table:
            data_id = max(row["id"] for row in table) + 1
        else:
            data_id = 0
        data = dict(data)
        data["id"] = data_id
    table.append(data)
    logger.info("Add metadata to %s: %s", filename, data)
    _write_table(filename, table)


def _edit_table(filename: str, data: dict) -> None:
    table = _read_table(filename)
    for row in table:
        if row["id"] == data["id"]:
            logger.info("Edit metadata of %s: %s", filename, data)
            row.update(data)
            _write_table(filename, table)
            break
    else:
        _add_table(filename, data)


def _get_row(filename: str, dataset_id: int) -> Optional[dict]:
    table = _read_table(filename)
    for row in table:
        if row["id"] == dataset_id:
            return row


def _get_max_id(filename: str) -> int:
    table = _read_table(filename)
    if table:
        return max(row["id"] for row in table)
    return -1


def _find_data(filename: str, date: str) -> Optional[Dict]:
    date = datetime.datetime.fromisoformat(date).astimezone()
    for data in _read_table(filename):
        start_date = datetime.datetime.fromisoformat(data["startDate"]).astimezone()
        end_date = data.get("endDate")
        if end_date is None:
            # infinite timeslot
            inside_timeslot = date >= start_date
        else:
            # finite timeslot
            end_date = datetime.datetime.fromisoformat(end_date).astimezone()
            inside_timeslot = date >= start_date and date <= end_date
        if inside_timeslot:
            return data
