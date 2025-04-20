# This file is part of the Lima2 project
#
# Copyright (c) 2020-2024 Beamline Control Unit, ESRF
# Distributed under the MIT license. See LICENSE for more info.

import gevent


class Timer:
    def __init__(self, cbk, after, nb_repeat):
        self.__cbk = cbk
        self.__nb_repeat = nb_repeat
        self.__count = 0

        self.__timer = gevent.get_hub().loop.timer(0.0, after)
        self.__timer.start(self.__call__)

    def __call__(self):
        self.__cbk(self.__count)
        self.__count += 1

        if self.__count > self.__nb_repeat:
            self.__timer.stop()

    def stop(self):
        self.__timer.stop()
