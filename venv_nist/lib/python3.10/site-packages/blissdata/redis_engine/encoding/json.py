# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.
import json
import numpy as np
from . import EncodedBatch, StreamEncoder


class JsonStreamEncoder(StreamEncoder):
    @property
    def dtype(self):
        return np.dtype("object")

    @property
    def shape(self):
        return ()

    def info(self):
        return {"type": "json"}

    @classmethod
    def from_info(cls, info):
        assert info["type"] == "json"
        return cls()

    def encode(self, data):
        return EncodedBatch(json.dumps(data).encode())

    def decode(self, batches):
        if isinstance(batches, EncodedBatch):
            batches = (batches,)
        return [json.loads(batch.payload) for batch in batches]
