#!/usr/bin/env python

"""
Script to test xcalibu Tango device server
usage:
   device_test.py <ds_url>
"""

import sys
import PyTango

DS_NAME = sys.argv[1]

print(f"DS name ={ DS_NAME}")

DEV_PROXY = PyTango.DeviceProxy(DS_NAME)

print(f"DS State is {DEV_PROXY.state()}")
print(f"Calib name = {DEV_PROXY.calib_name}")
print(f" Xmin={DEV_PROXY.Xmin}")
print(f" Xmax={DEV_PROXY.Xmax}")

print(f" Ymin={DEV_PROXY.Ymin}")
print(f" Ymax={DEV_PROXY.Ymax}")

if DEV_PROXY.calib_type == "TABLE":
    print(f" fit order = {DEV_PROXY.fit_order}")

if DEV_PROXY.calib_type == "POLY":
    print(f"calib order = {DEV_PROXY.calib_order}")

print(f" f(3)={DEV_PROXY.get_y(3)}")
print(f" f(1)={DEV_PROXY.get_y(1)}")


try:
    print(f" f(666)={DEV_PROXY.get_y(666)}")
except Exception:
    print(" 666 is out of range")

# must return : -0.444837391376
