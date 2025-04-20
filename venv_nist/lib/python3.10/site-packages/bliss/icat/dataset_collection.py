# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.
from bliss.icat.policy import DataPolicyObject
from bliss.icat.proposal import Proposal
from bliss.common.utils import autocomplete_property
from bliss.icat.json_policy import RedisJsonNode


class DatasetCollection(DataPolicyObject):
    def __init__(self, node: RedisJsonNode):
        super().__init__(node)
        self._proposal = None
        self._expected_field = {"Sample_name", "Sample_description"}

    @property
    def proposal(self):
        if self._proposal is None:
            if self._node.parent is not None:
                self._proposal = Proposal(self._node.parent)
        return self._proposal

    @property
    def parent(self):
        return self.proposal

    @property
    def children(self):
        from bliss.icat.dataset import Dataset

        for node in self._node.children:
            yield Dataset(node)

    @autocomplete_property
    def sample_name(self):
        return self.get_metadata_field("Sample_name")

    @sample_name.setter
    def sample_name(self, value):
        self.write_metadata_field("Sample_name", value)

    @autocomplete_property
    def sample_description(self):
        # TODO: use Dataset_description when it gets introduced
        return self.get_metadata_field("Sample_description")

    @sample_description.setter
    def sample_description(self, value):
        # TODO: use Dataset_description when it gets introduced
        self["Sample_description"] = value
