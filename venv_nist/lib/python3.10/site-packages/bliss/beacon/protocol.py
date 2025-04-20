# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

import struct

HEADER_SIZE = struct.calcsize("<ii")

UNKNOW_MESSAGE = -1

CONFIG = 1

# Locking related messages
LOCK = 20
UNLOCK = 21
LOCK_OK_REPLY = 22
LOCK_RETRY = 23
LOCK_STOLEN = 24
LOCK_STOLEN_OK_REPLY = 25

# Get Redis location
REDIS_QUERY = 30
REDIS_QUERY_ANSWER = 31

# Get Redis-data location
REDIS_DATA_SERVER_QUERY = 32
REDIS_DATA_SERVER_FAILED = 33
REDIS_DATA_SERVER_OK = 34

# Read file content fron config
CONFIG_GET_FILE = 50
CONFIG_GET_FILE_FAILED = 51
CONFIG_GET_FILE_OK = 52

# Get absolute config location
CONFIG_GET_DB_BASE_PATH = 60
CONFIG_GET_DB_BASE_PATH_OK = 89

# Read config subtree of file content related to BLISS configuration
CONFIG_GET_DB_FILES = 64
CONFIG_DB_FILE_RX = 61
CONFIG_DB_END = 62
CONFIG_DB_FAILED = 63

# Create/write file into the config
CONFIG_SET_DB_FILE = 70
CONFIG_SET_DB_FILE_FAILED = 71
CONFIG_SET_DB_FILE_OK = 72

# Remove file from the config
CONFIG_REMOVE_FILE = 80
CONFIG_REMOVE_FILE_FAILED = 81
CONFIG_REMOVE_FILE_OK = 82

# Move file/folder from the config
CONFIG_MOVE_PATH = 83
CONFIG_MOVE_PATH_FAILED = 84
CONFIG_MOVE_PATH_OK = 85

# Get folder structure of a subtree of the config
CONFIG_GET_DB_TREE = 86
CONFIG_GET_DB_TREE_FAILED = 87
CONFIG_GET_DB_TREE_OK = 88

# Unused
# Introduced by https://gitlab.esrf.fr/bliss/bliss/-/merge_requests/234
CONFIG_GET_PYTHON_MODULE = 90
CONFIG_GET_PYTHON_MODULE_FAILED = 91
CONFIG_GET_PYTHON_MODULE_RX = 92
CONFIG_GET_PYTHON_MODULE_END = 93

# Get the UDS location (.sock) to access to beacon, if reachable
UDS_QUERY = 100
UDS_OK = 101
UDS_FAILED = 102

# Get/set client name per client connection to beacon
CLIENT_SET_NAME = 110
CLIENT_GET_NAME = 111
CLIENT_NAME_OK = 112

# Retrieve devices locked by clients
WHO_LOCKED = 120
WHO_LOCKED_FAILED = 121
WHO_LOCKED_RX = 122
WHO_LOCKED_END = 123

# Get logging server location
LOG_SERVER_ADDRESS_QUERY = 130
LOG_SERVER_ADDRESS_OK = 131
LOG_SERVER_ADDRESS_FAIL = 132

# Get/set on beacon key/value storage
KEY_SET = 140
KEY_SET_OK = 141
KEY_SET_FAILED = 142
KEY_GET = 143
KEY_GET_OK = 144
KEY_GET_FAILED = 145
KEY_GET_UNDEFINED = 146


class IncompleteMessage(Exception):
    pass


def message(cmd, contents=b""):
    return b"%s%s" % (struct.pack("<ii", cmd, len(contents)), contents)


def unpack_header(header):
    return struct.unpack("<ii", header)


def unpack_message(s):
    if len(s) < HEADER_SIZE:
        raise IncompleteMessage
    messageType, messageLen = struct.unpack("<ii", s[:HEADER_SIZE])
    if len(s) < HEADER_SIZE + messageLen:
        raise IncompleteMessage
    message = s[HEADER_SIZE : HEADER_SIZE + messageLen]
    remaining = s[HEADER_SIZE + messageLen :]
    return messageType, message, remaining
