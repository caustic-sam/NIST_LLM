from tabulate import tabulate

from bliss import global_map
from bliss.comm.util import get_comm

# from bliss.common.counter import Counter
# from bliss.controllers.counter import CounterController, SamplingCounterController
from bliss.controllers.counter import SamplingCounterController

# from bliss.common.counter import SamplingCounter, SamplingMode
from bliss.common.counter import SamplingCounter
from bliss.common.logtools import log_debug


"""
Preciamolen indicateur i5 - weighing machine

- class: I5
  module: preciamolen_i5
  name: i5
  #id: 1
  #unit: kg
  serial:                          
      url: rfc2217://lid322:28038
  counters:
  - counter_name: net
    weight: net
  - counter_name: raw
    weight: raw
  - counter_name: tare
    weight: tare


            trame configuree
            typical answer: '\x01\x020200000.0kg \r'
            or:             '\x01\x020100056.0kg \x020300056.0kg \r'
                              SOH STX             STX
                                     raw 56 kg       net 56 kg                         


        cmd = f"{I5ASCII.SOH}{id}{I5ASCII.ENQ}{cmd_blk[0]}L{I5ASCII.ENQ}{cmd_blk[1]}L{cs}\r\n"
        cmd = f"{I5ASCII.SOH}{id}{I5ASCII.ENQ}{cmd_blk[0]}L{cs}\r\n"
        cmd = f"{I5ASCII.SOH}{id}                          {cs}\r\n"

        reply = ['{cmd_blk[0]}value1'],['{cmd_blk[1]}value2']
        reply = ['0100056.0kg ','0300056.0kg ']
"""


INFORMATIONS = {
    "raw weight": "01",
    "tare weight": "02",
    "net weight": "03",
    "status": "04",
    "measuring ranges status": "05",
    "selected channel": "08",
    "date": "80",
    "time": "81",
    "DSD num id": "98",
    "DSD registration number": "99",
}

COMMANDS = {"mise a zero": "01", "tarage": "04"}


class I5ASCII:
    SOH = "\001"  # Start of Heading
    STX = "\002"  # Start of Text
    ETX = "\003"  # End of Text
    EOT = "\004"  # End of Transmission
    ENQ = "\005"  # Enquiry
    ACK = "\006"  # Acknowledge
    BEL = "\007"  # Bell
    BS = "\008"  # Backspace
    HT = "\009"  # Horizontal Tabulation
    LF = "\00A"  # Line Feed
    VT = "\00B"  # Vertical Tabulation
    FF = "\00C"  # Form Feed
    CR = "\00D"  # Carriage Return
    DLE = "\010"  # Data Link Escape


class I5Counter(SamplingCounter):
    def __init__(self, name, measure, controller, **kwargs):
        super().__init__(name, controller, **kwargs)
        self.cmd = measure
        log_debug(self, "Counter {0} created for reading {1}".format(name, measure))


class I5(SamplingCounterController):
    def __init__(self, name, config):

        super().__init__(name)
        # High frequency acquisition loop
        self.max_sampling_frequency = None

        self.comm = get_comm(config, eol="\n")

        self.id = config.get("id", None)
        self.unit = config.get("unit", "kg")

        if self.id and self.id not in range(1, 100):
            raise ValueError("I5: {self.id} not a valid id number")

        if self.unit not in ["g", "kg"]:
            raise ValueError('I5: {self.unit} not valid. only "g" or "kg" accepted')

        if config.get("checksum", None) is not None:
            raise NotImplementedError(
                "I5: checksum not implemented, please configure the device communication accordingly"
            )

        global_map.register(self, children_list=[self.comm], tag=name)

        for cnt in config.get("counters", list()):
            if "weight" in cnt.keys():
                if cnt["weight"].casefold() not in ["raw", "tare", "net"]:
                    print(
                        "WARNING: {0} weight unknown, {1} counter channel will be ignored".format(
                            cnt["weight"], cnt["counter_name"]
                        )
                    )
                    continue

            self.create_counter(I5Counter, cnt["counter_name"], cnt["weight"])

    def __del__(self):
        self.comm.close()

    def __info__(self):
        info_str = f"Preciamolen I5 weighing machine - {self.id}\n\n"
        info_str += tabulate(self._lecture_trame_configuree().items())

        return info_str

    def _lecture_trame_configuree(self):
        reply = self._io()
        ret = dict()

        for item in reply:
            for txt, cmd in INFORMATIONS.items():
                if item[:2] == cmd:
                    ret[txt] = item[2:]

        return ret

    def weight(self, type="net"):
        """
        typical answer: '\x01\x020300056.0kg \r'
        """
        cmd_blk = INFORMATIONS[f"{type} weight"]
        reply = self._io(cmd_blk)[0]
        assert reply.startswith(cmd_blk)
        return self._weight(reply[2:])

    def weights(self):
        reply = self._io(["01", "02", "03"])
        results = dict(
            zip(map(lambda x: x[:2], reply), map(lambda x: self._weight(x[2:]), reply))
        )

        return results

    def _weight(self, text):

        pds = float(text[:-3])
        unit = text[-3:-1]

        if unit == self.unit:
            return pds
        elif unit == "kg":
            return pds * 1000
        elif unit == " g":
            return pds / 1000
        else:
            raise RuntimeError("I5: wrong unit: {unit}")

    def taring(self):
        self._io(exec_blk="04")
        # status de la commande :self._io(exec_blk='04', value='?')

    def zero(self):
        # self._io(exec_blk='01')
        pass

    def _io(self, cmd_blk=None, value=None, exec_blk=None):
        id = f"{I5ASCII.HT}{self.id}" if self.id is not None else ""
        cs = ""
        cmd = f"{I5ASCII.SOH}{id}"  # {cs}\r\n"
        if cmd_blk is not None:
            if value is None:
                if isinstance(cmd_blk, str):
                    cmd += f"{I5ASCII.ENQ}{cmd_blk}L"
                elif isinstance(cmd_blk, list):
                    for _cmd in cmd_blk:
                        cmd += f"{I5ASCII.ENQ}{_cmd}L"
            else:
                if value != "?":
                    if value > 999999:
                        raise ValueError
                    value = f"{value:06d}.{'' if self.unit == 'kg' else ''}{self.unit} "
                cmd += f"{I5ASCII.STX}{cmd_blk}{value}"
                raise NotImplementedError("to be tested carrefully first")
        elif exec_blk is not None:
            raise NotImplementedError("to be tested carrefully first")
            if value is None:
                cmd += f"{I5ASCII.DLE}{exec_blk}M"
            else:
                cmd += f"{I5ASCII.DLE}{exec_blk}?"
                """
                answer : c=en cours, t=terminee, r=refusee
                """

        cmd += f"{cs}\r\n"
        reply = (
            self.comm.write_readline(cmd.encode())
            .decode()
            .strip("\r")
            .split(I5ASCII.STX)
        )
        assert reply.pop(0) == I5ASCII.SOH

        return reply

    # SamplingCounterController methods

    def read_all(self, *counters):
        """return the values of the given counters as a list.
        TODO: If possible this method should optimize the reading of all counters at once.
        """
        values = list()
        cmd_blk = list()

        for cnt in counters:
            cmd_blk.append(INFORMATIONS[f"{cnt.cmd} weight"])

        reply = self._io(cmd_blk)

        results = dict(
            zip(map(lambda x: x[:2], reply), map(lambda x: self._weight(x[2:]), reply))
        )

        for cnt in counters:
            values.append(results.pop(INFORMATIONS[f"{cnt.cmd} weight"]))

        return values
