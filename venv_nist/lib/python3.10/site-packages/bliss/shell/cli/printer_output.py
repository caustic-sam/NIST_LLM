from __future__ import annotations

import os
import tempfile
import subprocess
import shutil
from bliss.common import event
from .bliss_output import BlissOutput
from bliss import _get_current_session

try:
    from fpdf import FPDF
except ImportError:
    FPDF = None


class PrintPdfOutput:
    DEFAULT_FONT_SIZE = 9

    def __init__(self, output: BlissOutput, printer_name: str | None = None):
        self._buffer: list[str] = []
        self._output: BlissOutput = output
        self._printer_name = printer_name
        self._have_a2ps = shutil.which("a2ps") is not None

    def connect(self):
        """Connect to the output events"""
        event.connect(self._output, "output", self._log_stdout_append)
        event.connect(self._output, "flush", self._log_stdout_flush)

    def disconnect(self):
        """Disconnect the object from the output events"""
        event.disconnect(self._output, "output", self._log_stdout_append)
        event.disconnect(self._output, "flush", self._log_stdout_flush)

    def _log_stdout_append(self, data: str):
        self._buffer.append(data)

    def _log_stdout_flush(self):
        pass

    def flush(self):
        if len(self._buffer) == 0:
            return
        buffer = self._buffer
        self._buffer = []

        if self._have_a2ps:
            output_str = "".join(buffer)
            try:
                session = _get_current_session()
                with tempfile.NamedTemporaryFile(mode="wt", delete=False) as tmpfile:
                    tmpfile_name = tmpfile.name
                    tmpfile.write(output_str)
                printer_string = (
                    f"a2ps {tmpfile_name} --center-title={session.name} -o - | lp -oraw"
                )
                # printer_string = f"a2ps {tmpfile_name} --center-title={session.name} -o foo.ps"
                os.system(printer_string)
            finally:
                os.unlink(tmpfile_name)
        elif FPDF is not None:
            output_str = "".join(buffer)
            with tempfile.TemporaryDirectory() as tmpdir:
                # Portrait, points size ref, Letter-size paper
                pdf = FPDF("P", "pt", "A4")
                # add a blank page to start
                pdf.add_page()
                # "Courier" is required as a font with constant width
                pdf.set_font("Courier", size=self.DEFAULT_FONT_SIZE)
                # Here "h" is height of line
                pdf.write(h=self.DEFAULT_FONT_SIZE + 2, txt=output_str)
                pdf_filename = tmpdir + "/pon-poff.pdf"
                pdf.output(pdf_filename)
                printer_name = f"-P {self._printer_name}" if self._printer_name else ""
                printer_string = f"lpr {printer_name} -o media=A4 {pdf_filename}"
                os.system(printer_string)
        else:
            lpr = subprocess.Popen("/usr/bin/lpr", stdin=subprocess.PIPE)
            for b in buffer:
                lpr.stdin.write(b.encode("utf-8"))
            lpr.kill()
        print("Printer finished")
