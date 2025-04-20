# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.
import os
import datetime
from typing import Union
from collections.abc import Iterable
import numpy
from bliss.icat.policy import DataPolicyObject
from bliss.icat.client import DatasetId


class Proposal(DataPolicyObject):
    @property
    def unconfirmed_dataset_ids(self) -> list[DatasetId]:
        return [
            DatasetId(name=os.path.basename(dataset.path), path=dataset.path)
            for dataset in self._iter_unconfirmed_datasets()
        ]

    def get_dataset(self, dataset_name_or_id: Union[DatasetId, str]):
        for collection in self.children:
            for dataset in collection.children:
                if isinstance(dataset_name_or_id, str):
                    found = dataset_name_or_id == dataset.name
                else:
                    found = (
                        DatasetId(
                            name=os.path.basename(dataset.path), path=dataset.path
                        )
                        == dataset_name_or_id
                    )
                if found:
                    return dataset

    def _iter_unconfirmed_datasets(self):
        """An "unconfirmed" dataset is marked in Redis as "closed" and "unregistered"."""
        for collection in self.children:
            for dataset in collection.children:
                if not dataset.is_registered and dataset.is_closed:
                    yield dataset

    def unconfirmed_dataset_info_string(self) -> str:
        rows = list(self._iter_unconfirmed_dataset_info())
        if not rows:
            return ""
        lengths = numpy.array([[len(s) for s in row] for row in rows])
        fmt = "   ".join(["{{:<{}}}".format(n) for n in lengths.max(axis=0)])
        infostr = "Unconfirmed datasets:\n "
        infostr += fmt.format("Name", "Time since end", "Path")
        infostr += "\n "
        infostr += "\n ".join([fmt.format(*row) for row in rows])
        return infostr

    def _iter_unconfirmed_dataset_info(self) -> Iterable[tuple[str, str, str]]:
        now = datetime.datetime.now()
        for dataset in self._iter_unconfirmed_datasets():
            end_date = dataset.end_date
            if end_date is None:
                time_since_end = "NaN"
            else:
                time_since_end = str(now - end_date)
            yield dataset.name, time_since_end, dataset.path

    @property
    def children(self):
        from bliss.icat.dataset import DatasetCollection

        for node in self._node.children:
            yield DatasetCollection(node)

    @property
    def parent(self):
        return None
