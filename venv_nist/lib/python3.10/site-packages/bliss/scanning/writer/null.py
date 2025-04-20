from .base import FileWriter


class NullWriter(FileWriter, name="null"):
    """Don't write scan data."""

    _FILE_EXTENSION = ""

    def _prepare_scan(self, scan) -> None:
        pass

    def get_last_scan_number(self) -> int:
        """Scans start numbering from 1 so 0 indicates
        no scan exists in the file.
        """
        return 0

    @property
    def separate_scan_files(self) -> bool:
        return False

    def finalize(self, scan) -> None:
        """Called at the end of the scan"""
        pass
