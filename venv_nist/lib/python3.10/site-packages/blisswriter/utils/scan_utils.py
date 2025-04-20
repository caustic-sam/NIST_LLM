import os
from typing import Dict, Optional
from . import session_utils
from . import data_policy


@session_utils.with_scan_saving
def session_filename(scan_saving=None) -> Optional[str]:
    """Name of the file that contains the scan data of the current BLISS session"""
    return session_utils.scan_saving_get(
        "filename", default=None, scan_saving=scan_saving
    )


@session_utils.with_scan_saving
def session_master_filenames(scan_saving=None, config: bool = True) -> Dict[str, str]:
    """Names of the files that contain links to the scan data of the current BLISS session"""
    if not config:
        return dict()
    writer_object = scan_saving.writer_object
    if not writer_object.saving_enabled():
        return dict()
    eval_dict = dict()
    root_path = scan_saving.get_cached_property("root_path", eval_dict=eval_dict)
    if writer_object.separate_scan_files:
        dataset_template = scan_saving.data_filename
    else:
        dataset_template = None
    relative_templates = data_policy.masterfile_templates(
        dataset_template=dataset_template, data_policy=scan_saving.data_policy
    )
    return {
        name: scan_saving.eval_template(
            os.path.abspath(os.path.join(root_path, s)), eval_dict=eval_dict
        )
        for name, s in relative_templates.items()
    }


@session_utils.with_scan_saving
def session_filenames(scan_saving=None, config=True):
    """
    Names of the files that contain links to the scan data (raw or as links) of the current BLISS session

    :param bliss.scanning.scan.ScanSaving scan_saving:
    :pram bool config: writer parses the extra "nexuswriter" info
    :returns list(str):
    """
    filenames = dict()
    filename = session_filename(scan_saving=scan_saving)
    if config:
        filenames.update(
            session_master_filenames(scan_saving=scan_saving, config=config)
        )
    if filename:
        filenames["dataset"] = filename
    return filenames
