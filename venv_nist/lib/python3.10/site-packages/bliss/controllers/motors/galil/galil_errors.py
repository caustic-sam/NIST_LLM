# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.


"""
This file contain:
* Error codes and descriptions for Galil controller.
* Stop codes and descriptions for Galil controller.

Commands:
  TC 0 : Return the numerical code only
  TC 1 : Return the numerical code and human-readable message

DEMO [12]: leg1.controller._galil_query("TC")
 Out [12]: '1'

DEMO [13]: leg1.controller._galil_query("TC 1")
 Out [13]: '1 Unrecognized command'

"""


def galil_get_error_str(err_nb):
    try:
        return galil_errors[err_nb]
    except KeyError:
        return f"Unknown error : {err_nb}"


def galil_get_stop_code_str(stop_code_nb):
    try:
        return galil_stop_codes[stop_code_nb]
    except KeyError:
        return f"Unknown stop code : {stop_code_nb}"


galil_errors = {
    1: "Unrecognized command",
    2: "Command only valid from program",
    3: "Command not valid in program",
    4: "Operand error",
    5: "Input buffer full",
    6: "Number out of range",
    7: "Command not valid while running",
    8: "Command not valid while not running",
    9: "Variable error",
    10: "Empty program line or undefined label",
    11: "Invalid label or line number",
    12: "Subroutine more than 16 deep",
    13: "JG only valid when running in jog mode",
    14: "EEPROM check sum error",
    15: "EEPROM write error",
    16: "IP incorrect sign during position move or IP given during forced deceleration",
    17: "ED, BN and DL not valid while program running",
    18: "Command not valid when contouring",
    19: "Application strand already executing",
    20: "Begin not valid with motor off",
    21: "Begin not valid while running",
    22: "Begin not possible due to Limit Switch",
    24: "Begin not valid because no sequence defined",
    28: "S operand not valid",
    29: "Not valid during coordinated move",
    30: "Sequence Segment Too Short",
    31: "Total move distance in a sequence > 2 billion",
    32: "Segment buffer full",
    33: "VP or CR commands cannot be mixed with LI commands",
    39: "No time specified",
    41: "Contouring record range error",
    42: "Contour data being sent too slowly",
    46: "Gear axis both master and follower",
    50: "Not enough fields",
    51: "Question mark not valid",
    52: "Missing quote or string too long",
    53: "Error in {}",
    54: "Question mark part of string",
    55: "Missing [ or []",
    56: "Array index invalid or out of range",
    57: "Bad function or array",
    58: "Bad command response   i.e._GNX",
    59: "Mismatched parentheses",
    60: "Download error - line too long or too many lines",
    61: "Duplicate or bad label",
    62: "Too many labels",
    63: "IF statement without ENDIF",
    66: "Array space full",
    67: "Too many arrays or variables",
    80: "Record mode already running",
    81: "No array or source specified",
    82: "Undefined Array",
    83: "Not a valid number",
    84: "Too many elements",
    90: "Only A B C D valid operand",
    97: "Bad Binary Command Format",
    98: "Binary Commands not valid in application program",
    99: "Bad binary command number",
    100: "Not valid when running ECAM",
    101: "Improper index into ET",
    102: "No master axis defined for ECAM",
    103: "Master axis modulus greater than 256 EP value",
    104: "Not valid when axis performing ECAM",
    105: "EB1 command must be given first",
    106: "Privilege Violation",
    110: "No hall effect sensors detected",
    111: "Must be made brushless by BA command",
    112: "BZ command timeout",
    113: "No movement in BZ command",
    114: "BZ command runaway",
    118: "Controller has GL1600 not GL1800",
    119: "Not valid for axis configured as stepper",
    120: "Bad Ethernet transmit",
    121: "Bad Ethernet packet received",
    123: "TCP lost sync",
    124: "Ethernet handle already in use",
    125: "No ARP response from IP address",
    126: "Closed Ethernet handle",
    127: "Illegal Modbus function code",
    128: "IP address not valid",
    130: "Remote IO command error",
    131: "Serial Port Timeout",
    132: "Analog inputs not present",
    133: "Command not valid when locked / Handle must be UDP",
    134: "All motors must be in MO for this command",
    135: "Motor must be in MO",
    136: "Invalid Password",
    137: "Invalid lock setting",
    138: "Passwords not identical",
    140: "Serial encoder error",
    141: "Feature not supported",
    143: "TM timed out",
    144: "Incompatible with encoder type",
    160: "BX failure",
    161: "Sine amp axis not initialized",
    163: "IA command not valid when DHCP mode enabled",
    164: "Exceeded maximum sequence length, BGS or BGT is required",
    165: "Cannot have both SINE and SSI feedback enabled at once",
    166: "Unable to set analog output",
}

galil_stop_codes = {
    0: "Motors are running, independent mode",
    1: "Motors decelerating or stopped at commanded independent position",
    2: "Decelerating or stopped by FWD limit switch or soft limit FL",
    3: "Decelerating or stopped by REV limit switch or soft limit BL",
    4: "Decelerating or stopped by Stop Command (ST)",
    6: "Stopped by Abort input",
    7: "Stopped by Abort command (AB)",
    8: "Decelerating or stopped by Off on Error (OE1)",
    9: "Stopped after finding edge (FE)",
    10: "Stopped after homing (HM) or Find Index (FI)",
    11: "Stopped by selective abort input",
    12: "Decelerating or stopped by encoder failure (OA1) (DMC-40x0/18x6)",
    15: "Amplifier Fault (DMC-40x0)",
    16: "Stepper position maintainance error",
    30: "Running in PVT mode",
    31: "PVT mode completed normally",
    32: "PVT mode exited because buffer is empty",
    50: "Contour Running",
    51: "Contour Stop",
    99: "MC timeout",
    100: "Motors are running, Vector Sequence",
    101: "Motors stopped at commanded vector",
}
