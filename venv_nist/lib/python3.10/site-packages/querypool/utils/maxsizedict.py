from typing import Optional
from collections import OrderedDict


class MaxSizeDict(OrderedDict):
    def __init__(self, *args, maxsize: Optional[int] = None, **kwargs):
        self.__maxsize = maxsize
        super().__init__(*args, **kwargs)
        self.__purge()

    def __setitem__(self, key, value):
        super().__setitem__(key, value)
        self.__purge()

    def __purge(self):
        if self.__maxsize is not None:
            while len(self) > self.__maxsize:
                self.popitem(last=False)
