from bliss.scanning.scan import ScanPreset


class RestoreFilterPosition(ScanPreset):
    """Reset the filter position at the end of the scan"""

    def __init__(self, auto_filter):
        self.auto_filter = auto_filter
        super().__init__()

        def user_status():
            with self.auto_filter.filterset._user_status():
                yield

        self._user_status = iter(user_status())
        next(self._user_status)

    def stop(self, scan):
        try:
            if self.auto_filter.always_back:
                self.auto_filter.filterset.set_back_filter()
        finally:
            next(self._user_status, None)


class SynchronizedFilterSet(ScanPreset):
    def __init__(self, auto_filter):
        self.auto_filter = auto_filter
        super().__init__()

    def prepare(self, scan):
        self.auto_filter.initialize_filterset()
        self.auto_filter.synchronize_filterset()
