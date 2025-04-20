# -*- coding: utf-8 -*-
#
# This file is part of the mechatronic project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.


"""
SpeedgoatUtils class: Collection of useful functions.

# Why not in BLISS ???

"""

from __future__ import annotations

import typing
from datetime import datetime
import numpy as np
import matplotlib.pyplot as plt
import gevent
from matplotlib.mlab import psd, csd, cohere
from scipy.io import savemat

if typing.TYPE_CHECKING:
    from .speedgoat_counter import SpeedgoatHdwCounter


class SpeedgoatUtils:
    """
    Collection of useful functions
    used in speedgoat_hardware.py
    """

    def __init__(self, speedgoat):
        self._speedgoat = speedgoat
        self._fastdaq = self._speedgoat.fastdaq.fastdaq

    @staticmethod
    def display_counters(counters: list[SpeedgoatHdwCounter], update_time: float = 0.1):
        """
        Display counters live until user interruption (ctrl-c).

        Arguments:
            counters: A list of hardware counters
            update_time: Interval between read and redisplay
        """
        from bliss.shell.standard import text_block

        values: dict[SpeedgoatHdwCounter, typing.Any] = {}

        def render():
            block = f"| {'Counter':^20} | {'Value':^20} | {'Unit':^10} |\n"
            block += f"|-{'-'*20}-+-{'-'*20}-+-{'-'*10}-|\n"
            for counter in counters:
                value = values.get(counter)
                if value is None:
                    value = np.nan
                unit = counter.unit or ""
                block += f"| {counter.name:<20} | {value:>20.8f} | {unit:^10} |\n"

            return len(counters) + 2, block

        with text_block(render=render):
            while True:
                for c in counters:
                    values[c] = c.value
                gevent.sleep(update_time)

    def time_display(self, counters, duration=10, directory=None, file_name=None):
        """Record and display Speedgoat counters."""

        # Configure FastDAQ
        self._fastdaq.prepare_time(duration, counters)

        # Start the FastDAQ
        self._fastdaq.start(silent=True, wait=True)

        # Get FastDAQ Data
        data = self._fastdaq.get_data()
        t = self._speedgoat._Ts * np.arange(0, np.size(data[counters[0].name]), 1)

        # Plot the identified transfer function
        fig = plt.figure(dpi=150)
        ax = fig.add_subplot(1, 1, 1)

        for counter in counters:
            ax.plot(
                t, data[counter.name], "-", label=f"{counter.name} [{counter.unit}]"
            )

        ax.set_xscale("linear")
        ax.set_yscale("linear")
        ax.grid(True, which="both", axis="both")
        ax.set_xlabel("Time [s]")
        ax.set_ylabel("Amplitude")
        ax.legend()

        if directory is not None and file_name is not None:
            now = datetime.now().strftime("%d-%m-%Y_%H-%M")
            savemat(f"{directory}/{now}_{file_name}.mat", data)

            fig.savefig(f"{directory}/{now}_{file_name}.pdf")
            fig.savefig(f"{directory}/{now}_{file_name}.png")

        plt.close(fig)

        return data, t

    def spectral_analysis(
        self, counters, duration=10, time_averaging=1, directory=None, file_name=None
    ):
        """
        Compute the Power Spectral Density of Speedgoat counters.
        """

        assert (
            duration > time_averaging
        ), "duration should be larger than time_averaging"

        # Configure FastDAQ
        self._fastdaq.prepare_time(duration, counters)

        # Start the FastDAQ
        self._fastdaq.start(silent=True, wait=True)

        # Get FastDAQ Data
        data = self._fastdaq.get_data()

        # Plot the identified transfer function
        win = np.hanning(int(time_averaging * self._speedgoat._Fs))
        fig = plt.figure(dpi=150)
        ax = fig.add_subplot(1, 1, 1)

        psd_data = {}

        for counter in counters:
            [Pxx, f] = psd(
                data[counter.name],
                window=win,
                NFFT=len(win),
                Fs=int(self._speedgoat._Fs),
                noverlap=int(len(win) / 2),
                detrend="mean",
            )
            psd_data[counter.name] = Pxx
            psd_data["f"] = f
            ax.plot(
                f,
                np.sqrt(np.abs(Pxx)),
                "-",
                label=f"{counter.name}: {np.std(data[counter.name]):.2e} {counter.unit}rms",
            )

        ax.set_xscale("log")
        ax.set_yscale("log")
        ax.grid(True, which="both", axis="both")
        ax.set_xlabel("Frequency [Hz]")
        ax.set_ylabel("Amplitude Spectral Density [unit/sqrt(Hz)")
        ax.set_xlim(1, 1e3)
        ax.legend()

        if directory is not None and file_name is not None:
            now = datetime.now().strftime("%d-%m-%Y_%H-%M")
            savemat(f"{directory}/{now}_{file_name}.mat", data)
            fig.savefig(f"{directory}/{now}_{file_name}.pdf")
            fig.savefig(f"{directory}/{now}_{file_name}.png")

        plt.close(fig)

        return psd_data

    def identify_plant(
        self,
        generator,
        counter_in,
        counters_out,
        directory=None,
        file_name=None,
        xlim=[1e0, 1e3],
        ylim=[1e1, 1e3],
    ):
        """Computes the transfer function from between counter_in and counters_out using given generator.
        Save Data and figure in the specified directory with given filename.
        The duration of the identification is equal to the duration of the generator."""

        # Configure FastDAQ
        self._fastdaq.prepare_time(generator.duration, [counter_in] + counters_out)

        # Start the FastDAQ
        self._fastdaq.start(silent=True, wait=False)

        # Start the test signal
        generator.start()

        # Get FastDAQ Data
        self._fastdaq.wait_finished()
        data = self._fastdaq.get_data()

        # Plot the identified transfer function
        win = np.hanning(self._speedgoat._Fs)
        fig, axs = plt.subplots(2, 1, dpi=150, sharex=True)

        for counter_out in counters_out:
            self._tfestimate(
                data[counter_in.name],
                data[counter_out.name],
                win=win,
                Fs=int(self._speedgoat._Fs),
                plot=True,
                axs=axs,
                legend=f"{counter_in.name} to {counter_out.name}",
            )

        axs[0].set_xlim(xlim)
        axs[0].set_ylim(ylim)
        axs[0].legend()
        axs[1].set_ylim(-180, 180)

        now = datetime.now().strftime("%d-%m-%Y_%H-%M")
        if directory is not None and file_name is not None:
            savemat(f"{directory}/{now}_{file_name}.mat", data)
            fig.savefig(f"{directory}/{now}_{file_name}.pdf")
            fig.savefig(f"{directory}/{now}_{file_name}.png")

            plt.close(fig)

        # Plot the Coherence
        fig = plt.figure(dpi=150)
        ax = fig.add_subplot(1, 1, 1)

        for counter_out in counters_out:
            self._mscohere(
                data[counter_in.name],
                data[counter_out.name],
                win=win,
                Fs=int(self._speedgoat._Fs),
                plot=True,
                ax=ax,
                legend=f"{counter_in.name} to {counter_out.name}",
            )

        ax.set_xlim(1, 1e3)
        ax.legend()

        if directory is not None and file_name is not None:
            fig.savefig(f"{directory}/{now}_{file_name}_cohere.pdf")
            fig.savefig(f"{directory}/{now}_{file_name}_cohere.png")

        plt.close(fig)

    def _tfestimate(self, x, y, win, Fs, plot=False, axs=None, legend=""):
        """Computes the transfer function from x to y.
        win is a windowing function.
        Fs is the sampling frequency in [Hz]"""
        nfft = len(win)

        [Pyx, f] = csd(
            x,
            y,
            window=win,
            NFFT=nfft,
            Fs=int(Fs),
            noverlap=int(nfft / 2),
            detrend="mean",
        )
        Pxx = psd(
            x, window=win, NFFT=nfft, Fs=int(Fs), noverlap=int(nfft / 2), detrend="mean"
        )[0]

        G = Pyx / Pxx

        if plot:
            if axs is None:
                axs = plt.subplots(2, 1, dpi=150, sharex=True)[1]
            axs[0].plot(f, np.abs(G), "-", label=legend)
            axs[0].set_yscale("log")
            axs[0].grid(True, which="both", axis="both")
            axs[0].set_ylabel("Amplitude")

            axs[1].plot(f, 180 / np.pi * np.angle(G), "-")
            axs[1].set_xscale("log")
            axs[1].set_ylim(-180, 180)
            axs[1].grid(True, which="both", axis="both")
            axs[1].set_yticks(np.arange(-180, 180.1, 45))
            axs[1].set_xlabel("Frequency [Hz]")
            axs[1].set_ylabel("Phase [deg]")

        return G, f

    def _mscohere(self, x, y, win, Fs, plot=False, ax=None, legend=""):
        """Computes the coherence from x to y.
        win is a windowing function.
        Fs is the sampling frequency in [Hz]"""
        nfft = len(win)

        [coh, f] = cohere(
            x,
            y,
            window=win,
            NFFT=nfft,
            Fs=int(Fs),
            noverlap=int(nfft / 2),
            detrend="mean",
        )

        if plot:
            if ax is None:
                fig = plt.figure(dpi=150)
                ax = fig.add_subplot(1, 1, 1)
            ax.plot(f, np.abs(coh), "-", label=legend)
            ax.set_yscale("linear")
            ax.set_xscale("log")
            ax.grid(True, which="both", axis="both")
            ax.set_ylim(0, 1)
            ax.set_xlabel("Frequency [Hz]")
            ax.set_ylabel("Coherence")

        return coh, f

    def _pwelch(self, x, win, Fs, ax=None, plot=False, label=""):
        nfft = len(win)
        [Pxx, f] = psd(
            x, window=win, NFFT=nfft, Fs=int(Fs), noverlap=int(nfft / 2), detrend="mean"
        )

        if plot and ax is not None:
            ax.plot(f, np.sqrt(np.abs(Pxx)), "-", label=label)

        return Pxx, f
