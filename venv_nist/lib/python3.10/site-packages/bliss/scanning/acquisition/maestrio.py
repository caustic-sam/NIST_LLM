import gevent
import time
import numpy

from bliss.scanning.chain import AcquisitionMaster
from bliss.scanning.channel import AcquisitionChannel


class MaestrioDefaultAcquisitionMaster(AcquisitionMaster):
    def __init__(
        self, controller, maestrio, count_time=None, npoints=1, ctrl_params=None
    ):
        super().__init__(
            controller,
            ctrl_params=ctrl_params,
            prepare_once=False,  # True,
            start_once=False,
        )  # True)
        self.count_time = count_time
        self.maestrio = maestrio
        self._sources = {}

    def prepare(self):
        self.maestrio.putget("ACQTRIG SOFT")
        self.maestrio.putget("ACQHWTRIG DI1")  # if not set can run program
        self._sources = {
            c: c.channel for c in sorted(self._counters, key=lambda c: c.channel)
        }
        self.maestrio.putget("ACQSRC TIMER %s" % " ".join(self._sources.values()))
        self.maestrio.putget("ACQLENGTH %fS" % self.count_time)
        self.maestrio.putget("ACQNSAMPL %d" % 1)  # self.npoints)
        self.maestrio.load_program("#ACQ")

    def start(self):
        self.maestrio.run_program("#ACQ")

    def trigger(self):
        self.trigger_slaves()
        self.maestrio.putget("#PRGEVENT #ACQ")
        # Hack for now
        # Maestrio can only do an equivalent of **runc_ct**
        # to be removed in future
        self.wait_ready()
        data = self.maestrio.get_data(
            nb_counters=len(self._sources) + 1
        )  # + 1 TIMER is always return
        data_line = data[0]
        self.channels.update({c.name: d for c, d in zip(self._sources, data_line[1:])})

    def trigger_ready(self):
        status = self.maestrio.get_program_state("#ACQ")
        return status != self.maestrio.PROGRAM_STATE

    def wait_ready(self):
        while True:
            status = self.maestrio.get_program_state("#ACQ")
            if status != self.maestrio.PROGRAM_STATE.RUN:
                break
            gevent.sleep(20e-3)

    def stop(self):
        self.maestrio.stop_program("#ACQ")

    def reading(self):
        npoints = self.npoints
        while npoints:
            status = self.maestrio.get_program_state("#ACQ")
            if status != self.maestrio.PROGRAM_STATE.RUN:
                break
            npoints -= self.try_publish()
            gevent.sleep(0)
        self.try_publish()

    def try_publish(self):
        try:
            data = self.maestrio.get_data(nb_counters=len(self._sources))
        except RuntimeError:
            pass
        else:
            self.channels.update({c.name: d for c, d in zip(self._sources, data)})
            return len(data)
        return 0


class MaestrioAcquisitionMaster(AcquisitionMaster):
    def __init__(
        self,
        maestrio_device,
        program_data,
        program_name,
        program_abort_name=None,
        variables=None,
        macrodefs=None,
        channel_names=None,
        prepare_once=False,
        start_once=False,
        **keys,
    ):
        AcquisitionMaster.__init__(
            self,
            maestrio_device,
            name=maestrio_device.name,
            trigger_type=AcquisitionMaster.HARDWARE,
            prepare_once=prepare_once,
            start_once=start_once,
            **keys,
        )

        if start_once is True:
            raise NotImplementedError(
                "MaestrioAcquisitionMaster with start_once=True not yet implemented"
            )

        self.maestrio = maestrio_device
        self.program_data = program_data
        self.program_name = program_name
        self.program_abort_name = program_abort_name
        self.variables = variables or dict()
        self.macrodefs = macrodefs or dict()
        self.channames = channel_names or list()

        self._next_vars = None
        self._iter_index = 0
        self._start_epoch = None
        self._load_done = False

        m_name = self.maestrio.name
        for name in self.channames:
            self.channels.append(
                AcquisitionChannel(f"{m_name}:{name}", numpy.int32, ())
            )
        self._nb_counters = len(self.channames)

    def __iter__(self):
        if isinstance(self.variables, (list, tuple)):
            # iterate as many times as variable list length
            vars_iter = iter(self.variables)
            while True:
                self._next_vars = next(vars_iter)
                yield self
                self._iter_index += 1
        else:
            # iterate indefinitely unless no parent
            self._next_vars = self.variables
            while True:
                yield self
                self._iter_index += 1
                if not self.parent:
                    break

    def prepare(self):
        if self._iter_index > 0 and self.prepare_once:
            return

        if self._iter_index == 0:
            # upload program on maestrio
            self.maestrio.upload_program(self.program_name, self.program_data)

            # set macro defs
            self.maestrio.set_program_mac_values(self.program_name, **self.macrodefs)

            # load prog on sequencer
            self.maestrio.load_program(self.program_name)

        # write program variables
        self.maestrio.set_program_var_values(self.program_name, **self._next_vars)
        self._load_done = True

    def start(self):
        self.maestrio.run_program(self.program_name)
        self._start_epoch = time.time()
        self.spawn_reading_task()

    @property
    def start_epoch(self):
        return self._start_epoch

    def stop(self):
        if self.maestrio.is_program_running(self.program_name):
            self.maestrio.stop_program(self.program_name)
            self.wait_reading()
            if self.program_abort_name:
                self.maestrio.run_program(self.program_abort_name)
                self.wait_ready()

    def trigger_ready(self):
        return not self.maestrio.is_program_running(self.program_name)

    def wait_ready(self):
        if self._load_done:
            while self.maestrio.is_program_running(self.program_name):
                gevent.sleep(0.01)

    def reading(self):
        self._last_read = 0
        while self.maestrio.is_program_running(self.program_name):
            self.read_and_publish()
            gevent.sleep(0.01)

        # attempt a last read
        self.read_and_publish()

    def read_and_publish(self):
        data = self.maestrio.get_data(self._nb_counters)
        if data is not None:
            data = data.astype(numpy.int32)
            self.channels.update_from_array(data)
            self._last_read += data.shape[0]
            self.emit_progress_signal({"recv_data_len": self._last_read})
