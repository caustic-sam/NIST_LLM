# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

import flask
import gevent

from bliss.config import static


def domain_name(request):
    """Return the fully qualified domain name"""
    try:
        host, _ = request.server
        return host
    except TypeError:
        return request.host.split(":")[0]


def create_app(log_viewer_port):
    app = flask.Flask(__name__)

    @app.route("/")
    def index():
        with gevent.Timeout(30, TimeoutError):
            app.logger.info("Loading beacon configuration ...")
            cfg = static.get_config()
            app.logger.info("Beacon configuration loaded")

        node = cfg.root
        institute = node.get("institute", node.get("synchrotron"))
        laboratory = node.get("laboratory", node.get("beamline"))
        full_name = " - ".join(filter(None, (institute, laboratory)))
        return flask.render_template(
            "index.html",
            name=full_name,
            beamline=node.get("beamline", "ESRF"),
            institute=institute,
            laboratory=laboratory,
        )

    @app.route("/multivisor/")
    @app.route("/status/")
    def multivisor():
        return flask.redirect(f"http://{domain_name(flask.request)}:22000")

    @app.route("/supervisor/")
    def supervisor():
        return flask.redirect(f"http://{domain_name(flask.request)}:9001")

    @app.route("/log/")
    @app.route("/logs/")
    def log_viewer():
        return flask.redirect(f"http://{domain_name(flask.request)}:{log_viewer_port}")

    @app.route("/favicon.ico")
    def favicon():
        return flask.redirect(flask.url_for("static", filename="favicon.ico"), 301)

    return app
