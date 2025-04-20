"""ESRF data policy unofficial (but still valid)
"""

import os


def masterfile_templates(data_policy=None, dataset_template=None):
    """
    Templates for HDF5 file names relative to the dataset directory

    :returns dict(str):
    """
    templates = dict()
    if dataset_template:
        templates["dataset"] = os.path.extsep.join((dataset_template, "h5"))
    if data_policy == "ESRF":
        templates["dataset_collection"] = os.path.join(
            "..", os.path.extsep.join(("{proposal_name}_{collection_name}", "h5"))
        )
        templates["proposal"] = os.path.join(
            "..", "..", os.path.extsep.join(("{proposal_name}_{beamline}", "h5"))
        )
    return templates
