#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Examples of calibrations for test and demo purposes.
"""

import logging
import os
from xcalibu import Xcalibu

XCALIBU_DIRBASE = os.path.dirname(os.path.realpath(__file__))

log = logging.getLogger("DEMO_XCALIBU")


# Some data for demo
demo_calib_string = """
# TEST calibration
# Type TABLE
# Comments are starting with '#'
# Spaces are mainly ignored.

CALIB_NAME = B52
CALIB_TYPE = TABLE
CALIB_TIME = 1400081171.300155
CALIB_DESC = 'test table : example of matching lines'
#CALIB_TITI = 14

B52[0.8e-2] = -0.83e-2
B52[1]=1
B52[3]= 2
B52[5]=-12
B52 [6]=  -3
B52 [7]=   2
B52[10]=   4.5
 B52[13]=7.5
   B52[15]=18.5
B52[18]=0.5e2
B52[23]=	42
B52[23.1]=0.61e2
B52[27.401] = 0.72e2
B52[32]=  62
B52[0.5e2] = +0.53e2
"""


def demo_xcalibu_err(do_plot):
    """
    string calibration
    """
    log = logging.getLogger("XCALIBU")

    # log.info("===== use: demo_calib_string str ; POLYFIT ; fit_order = 2 ===================\n")
    myCalibString = Xcalibu(
        calib_string=demo_calib_string, fit_order=2, reconstruction_method="POLYFIT"
    )
    log.info("TEST -         demo_calib_string(%f) = %f" % (5, myCalibString.get_y(5)))
    log.info("TEST - inverse_demo_calib_string(%f) = %f" % (4, myCalibString.get_x(4)))

    myCalibString.print_info()

    print(f"myCalibString.get_y(1)={myCalibString.get_y(1)}")

    print(f"myCalibString.get_y(0)={myCalibString.get_y(0)}")

    if do_plot:
        myCalibString.plot()


'''
def demo_xcalibu_(do_plot):
    """
    ... calibration
    """
    log = logging.getLogger("XCALIBU")


    .print_info()

    if do_plot:
        .plot()
'''


def main():
    from optparse import OptionParser

    parser = OptionParser("demo_xcalibu.py")
    parser.add_option(
        "-d",
        "--debug",
        type="string",
        help="Available levels are :\n CRITICAL(50)\n \
                      ERROR(40)\n WARNING(30)\n INFO(20)\n DEBUG(10)",
        default="INFO",
    )

    parser.add_option(
        "-p",
        "--plot",
        action="store_true",
        dest="plot",
        default=False,
        help="Calibration plotting",
    )

    # Gather options and arguments.
    (options, args) = parser.parse_args()
    # print(options)
    # print(args)

    try:
        loglevel = getattr(logging, options.debug.upper())
    except AttributeError:
        # print "AttributeError  o.d=",options.debug
        loglevel = {
            50: logging.CRITICAL,
            40: logging.ERROR,
            30: logging.WARNING,
            20: logging.INFO,
            10: logging.DEBUG,
        }[int(options.debug)]

    print(
        "[xcalibu] - log level = %s (%d)" % (logging.getLevelName(loglevel), loglevel)
    )

    LOG_FORMAT = "%(name)s - %(levelname)s - %(message)s"
    logging.basicConfig(format=LOG_FORMAT, level=loglevel)

    print("-------------- args ------------------------")
    print(options)
    print(" plot=", options.plot)
    print("--------------------------------------")

    demo_xcalibu_err(options.plot)

if __name__ == "__main__":
    main()
