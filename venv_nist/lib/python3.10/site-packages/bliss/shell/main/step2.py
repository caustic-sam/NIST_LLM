# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

"""
Second step of the BLISS main.

Create and run the BlissRepl in the application context.
"""

import os
import sys
import logging
import appdirs
import configparser
import subprocess


def get_theme_mode() -> str:
    """
    Return the theme mode dark/light.

    It is based on a guess of the terminal background color.
    """
    if sys.platform in ["win32", "cygwin"]:
        # assuming dark
        return "dark"
    try:
        colorfgbg = subprocess.run(
            "bliss_get_term_bgcolor.sh", text=True, stderr=subprocess.PIPE
        ).stderr
        _, _, bg = colorfgbg.rpartition(";")  # could be xx;zz, or xx;yyyy;zz
        return "dark" if int(bg.strip()) < 8 else "light"
    except Exception:
        # something went wrong, let's say 'dark'
        return "dark"


def get_style(theme_mode: str, debug: bool) -> str:
    # Get "standard" config path (typically:'~/.config/ESRF')
    # Use appdirs to be multi-plateform safe.
    config_dir = appdirs.user_config_dir("ESRF")

    # Read config file.
    config = configparser.ConfigParser(
        {"light-theme": "default", "dark-theme": "material"}
    )
    config_file_path = os.path.join(config_dir, "bliss.ini")

    # If no config file found, ensure directory exists and create one.
    if not os.path.isdir(config_dir):
        if debug:
            print(f"Creating BLISS config directory: {config_dir}")
        os.makedirs(config_dir)
    if not config.read(config_file_path):
        with open(config_file_path, "w") as config_file:
            config.write(config_file)

    if debug:
        print(f"Using BLISS config file: {config_dir}/bliss.ini")

    code_style = config["DEFAULT"][f"{theme_mode}-theme"]
    return code_style


def main(argv=None):
    # Do not import any more stuffs here to avoid warnings on stdout
    # See bellow after early_logging_startup

    from .. import log_utils

    if argv is None:
        argv = sys.argv

    log_utils.early_logging_startup()

    session_name = argv[1]

    log_level = getattr(logging, argv[2].upper())

    expert_error_report = argv[3] == "1" if len(argv) > 3 else False

    with log_utils.filter_import_warnings(
        ignore_warnings=not expert_error_report
    ) as early_log_info:
        from bliss.shell.cli.repl import embed
        from bliss import current_session
        from bliss import global_map

    # initialize logging
    log_utils.logging_startup(log_level)

    theme_mode = get_theme_mode()

    style = get_style(theme_mode, expert_error_report)

    try:
        embed(
            session_name=session_name,
            use_tmux=True,
            style=style,
            theme_mode=theme_mode,
            expert_error_report=expert_error_report,
            early_log_info=early_log_info,
        )
    finally:
        try:
            current_session.close()
        except AttributeError:
            # no current session
            pass
        global_map.clear()


if __name__ == "__main__":
    main()
