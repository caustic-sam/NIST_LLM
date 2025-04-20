# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

"""
Meerstetter LTR1200 case acessible via Ethernet using TEC-Family controllers:
    TEC-1089, TEC-1090, TEC-1091, TEC-1122, TEC-1123.

yml configuration example:
- class: Ltr1200    #Or Ltr1200SampleEnv  for Sample environment pool controller with external interlocks
  module: regulation.temperature.meerstetter.ltr1200
  plugin: regulation
  #host: id10mer1
  host: 192.168.0.1

  outputs:
    - name: ltr1200output1
      unit: A
      channel: 1
    - name: ltr1200output2
      unit: A
      channel: 2

  inputs:
    - name: ltr1200input1
      unit: Celsius
      channel: 1
    - name: ltr1200inputsafety1
      unit: Celsius
      channel: 1
      type: Sink
    - name: ltr1200input2
      unit: Celsius
      channel: 2
    - name: ltr1200inputsafety2
      unit: Celsius
      channel: 2
      type: Sink

  ctrl_loops:
    - name: ltr1200loop1
      channel: 1
      input: $ltr1200input1
      output: $ltr1200output1
    - name: ltr1200loop2
      channel: 2
      input: $ltr1200input2
      output: $ltr1200output2
"""

from bliss import global_map
from bliss.comm import tcp
from bliss.common.logtools import log_debug
from bliss.controllers.regulator import Controller

from . import mecom


class ltr1200:
    """
    Low-level class which takes care of all the communication
    with the hardware with the help of other classes which
    implement MeCom protocol
    """

    def __init__(self, host=None, dev_addr=1, timeout=10):
        self.host = host
        self.dev_addr = dev_addr
        self.timeout = timeout

        # Port is always 50000 for Meerstetter TEC controller
        self._sock = tcp.Socket(self.host, 50000, self.timeout)

        self._tec = mecom.TECFamilyProtocol(self._sock, self.dev_addr)

        global_map.register(self, children_list=[self._sock, self._tec])

        log_debug(self, "__init__: %s %s %d" % (host, self._sock, dev_addr))

    def exit(self):
        self._sock.close()

    def init(self):
        log_debug(self, "init()")
        self.model = self._tec.get_model()
        log_debug(self, "init(): Model = %s" % (self.model))
        # TODO: see what else could add here i.e. which other
        #       operations/actions would be suitable.

    def get_model(self):
        log_debug(self, "get_model()")
        self.model = self._tec.get_model()
        log_debug(self, "get_model: %s" % (self.model))
        return self.model

    def get_object_temperature(self, channel):
        log_debug(self, "get_object_temperature(): channel = %d" % (channel))
        answer = self._tec._get_parameter(1000, 8, "float", channel)
        log_debug(self, "get_object_temperature: temp = %s" % answer)
        return answer

    def get_sink_temperature(self, channel):
        log_debug(self, "get_sink_temperature(): channel = %d" % (channel))
        answer = self._tec._get_parameter(1001, 8, "float", channel)
        log_debug(self, "get_sink_temperature: temp = %s" % answer)
        return answer

    def get_target_temperature(self, channel):
        log_debug(self, "get_target_temperature(): channel = %d" % (channel))
        answer = self._tec._get_parameter(1010, 8, "float", channel)
        log_debug(self, "get_target_temperature: temp = %s" % answer)
        return answer

    def set_target_temperature(self, value, channel):
        log_debug(
            self, "set_target_temperature(): channel = %d, value = %f", channel, value
        )
        answer = self._tec._set_parameter(3000, value, "float", channel)
        log_debug(self, "set_target_temperature: %s" % answer)  # should be ACK
        return answer

    def get_output_current(self, channel):
        log_debug(self, "get_output_current(): channel = %d", channel)
        answer = self._tec._get_parameter(1020, 8, "float", channel)
        log_debug(self, "get_output_current: current = %s" % answer)
        return answer

    def get_output_voltage(self, channel):
        log_debug(self, "get_output_voltage(): channel = %d", channel)
        answer = self._tec._get_parameter(1021, 8, "float", channel)
        log_debug(self, "get_output_voltage: voltage = %s" % answer)
        return answer

    def get_driver_status(self, channel=1):
        log_debug(self, "get_driver_status(): channel = %d", channel)
        answer = self._tec._get_parameter(1080, 8, "int", channel)
        description = [
            "Init",
            "Ready",
            "Run",
            "Error",
            "Bootloader",
            "Device will Reset within 200ms",
        ]

        if answer is not None:
            answer = description[int(answer)]
        log_debug(self, "get_driver_status: status = %s", answer)
        return answer

    def get_output_status(self, channel):
        log_debug(self, "get_output_status(): channel = %d", channel)
        answer = self._tec._get_parameter(2010, 8, "int", channel)
        description = [
            "Static OFF",
            "Static ON",
            "Live OFF/ON",
            "HW Enable",
        ]
        if answer is not None:
            answer = description[int(answer)]
        log_debug(self, "get_output_status: status = %s", answer)
        return answer

    def set_output_status(self, value, channel):
        log_debug(self, "set_output_status(): channel = %d, value = %d", channel, value)
        answer = self._tec._set_parameter(2010, value, "int", channel)
        log_debug(self, "set_output_status: %s" % answer)  # should be ACK
        return answer

    def get_output_input_selection(self, channel):
        log_debug(self, "get_output_input_selection(): channel = %d", channel)
        answer = int(self._tec._get_parameter(2000, 8, "int", channel))
        description = [
            "Static Current/Voltage",
            "Live Current/Voltage",
            "Temperature Controller",
        ]
        if answer is not None:
            answer = description[int(answer)]
        log_debug(self, "get_output_input_selection: %s" % answer)  # should be ACK
        return answer

    def set_output_input_selection(self, value, channel):
        log_debug(
            self,
            "set_output_input_selection(): channel = %d, value = %d",
            channel,
            value,
        )
        answer = self._tec._set_parameter(2000, value, "int", channel)
        log_debug(self, "set_output_input_selection: %s" % answer)  # should be ACK
        return answer

    def reset_device(self):
        log_debug(self, "reset_device()")
        self._tec.putget("RS")

    def emergency_stop(self):
        log_debug(self, "emergency_stop()")
        self._tec.putget("ES")

    def get_kp(self, channel):
        log_debug(self, "get_kp(): channel = %d" % (channel))
        answer = self._tec._get_parameter(3010, 8, "float", channel)
        log_debug(self, "get_kp = %s" % answer)
        return answer

    def set_kp(self, value, channel):
        log_debug(self, "set_kp(): value = %f channel = %d" % (value, channel))
        answer = self._tec._set_parameter(3010, value, "float", channel)
        log_debug(self, "set_kp = %s" % answer)
        return answer

    def get_ki(self, channel):
        log_debug(self, "get_ki(): channel = %d" % (channel))
        answer = self._tec._get_parameter(3011, 8, "float", channel)
        log_debug(self, "get_ki = %s" % answer)
        return answer

    def set_ki(self, value, channel):
        log_debug(self, "set_ki(): value = %f channel = %d" % (value, channel))
        answer = self._tec._set_parameter(3011, value, "float", channel)
        log_debug(self, "set_ki = %s" % answer)
        return answer

    def get_kd(self, channel):
        log_debug(self, "get_kd(): channel = %s" % (channel))
        answer = self._tec._get_parameter(3012, 8, "float", channel)
        log_debug(self, "get_kd = %s" % answer)
        return answer

    def set_kd(self, value, channel):
        log_debug(self, "set_kd(): value = %f channel = %d" % (value, channel))
        answer = self._tec._set_parameter(3012, value, "float", channel)
        log_debug(self, "set_kd = %s" % answer)
        return answer

    def get_ramprate(self, channel):
        log_debug(self, "get_ramp_rate(): channel = %s" % (channel))
        answer = self._tec._get_parameter(3003, 8, "float", channel)
        answer = answer * 60
        answer = round(answer, 2)
        log_debug(self, "get_ramp_rate = %s" % answer)
        return answer

    def set_ramprate(self, value, channel):
        log_debug(self, "set_ramp_rate(): value = %f channel = %d" % (value, channel))
        value = value / 60
        answer = self._tec._set_parameter(3003, value, "float", channel)
        log_debug(self, "set_ramp_rate = %s" % answer)
        return answer


class Ltr1200(Controller):
    def __init__(self, config):
        super().__init__(config)

        if "host" not in config:
            raise RuntimeError("Should have host with name or IP address in config")
        host = config["host"]

        if "dev_addr" not in config:
            dev_addr = 1
        else:
            dev_addr = config["dev_addr"]

        self._Ltr1200 = ltr1200(host, dev_addr)

        Controller.__init__(self, config)

        global_map.register(self, children_list=[self._Ltr1200])

        log_debug(self, "__init__: %s %d", host, dev_addr)

    # ------ init methods ------------------------

    def initialize_controller(self):
        """
        Initializes the controller (including hardware).
        """
        self._Ltr1200.init()

    def initialize_input(self, tinput):
        """
        Initializes an Input class type object

        Args:
           tinput: Input class type object
        """
        pass

    def initialize_output(self, toutput):
        """
        Initializes an Output class type object

        Args:
           toutput: Output class type object
        """
        # 2 set to Temperature Controller
        self._Ltr1200.set_output_input_selection(2, toutput.channel)

    def initialize_loop(self, tloop):
        """
        Initializes a Loop class type object

        Args:
           tloop: Loop class type object
        """
        pass

    # ------ get methods ------------------------

    def read_input(self, tinput):
        """
        Reads an Input class type object
        Raises NotImplementedError if not defined by inheriting class

        Args:
           tinput: Input class type object

        Returns:
           read value (in input unit)
        """
        input_type = tinput.config.get("type")
        log_debug(
            self, "Controller:read_input: %s:input_type %s" % (tinput, input_type)
        )

        if input_type is None:
            return self._Ltr1200.get_object_temperature(tinput.channel)
        elif input_type == "Sink":
            return self._Ltr1200.get_sink_temperature(tinput.channel)
        else:
            raise RuntimeError("unknown input_channel: %s." % (tinput.channel))

    def read_output(self, toutput):
        """
        Reads an Output class type object
        Raises NotImplementedError if not defined by inheriting class

        Args:
           toutput: Output class type object

        Returns:
           read value (in output unit)
        """
        log_debug(self, "Controller:read_output: %s" % (toutput))
        output_current = self._Ltr1200.get_output_current(toutput.channel)
        # output_voltage = self._Ltr1200.get_output_voltage(toutput.channel)
        # result = f"{output_current:.3f} A,{output_voltage:.3f} V"
        # result2 = "{}A, {}V".format(output_current,output_voltage)
        # return str(result)
        return output_current  # could also send output_voltage

    def state_input(self, tinput):
        """
        Return a string representing state of an Input object.
        Raises NotImplementedError if not defined by inheriting class

        Args:
           tinput:  Input class type object

        Returns:
           object state string. This is one of READY/RUNNING/ALARM/FAULT
        """
        log_debug(self, "Controller:state_input: %s" % (tinput))
        raise NotImplementedError

    def state_output(self, toutput):
        """
        Return a string representing state of an Output object.
        Raises NotImplementedError if not defined by inheriting class

        Args:
           toutput:  Output class type object

        Returns:
           object state string. This is one of READY/RUNNING/ALARM/FAULT
        """
        log_debug(self, "Controller:state_output: %s" % (toutput))
        return self._Ltr1200.get_output_status(toutput.channel)

    # ------ PID methods ------------------------

    def set_kp(self, tloop, kp):
        """
        Set the PID P value
        Raises NotImplementedError if not defined by inheriting class

        Args:
           tloop: Loop class type object
           kp: the kp value
        """
        log_debug(self, "Controller:set_kp: %s %s" % (tloop, kp))
        self._Ltr1200.set_kp(kp, tloop.channel)

    def get_kp(self, tloop):
        """
        Get the PID P value
        Raises NotImplementedError if not defined by inheriting class

        Args:
           tloop: Loop class type object

        Returns:
           kp value
        """
        log_debug(self, "Controller:get_kp: %s" % (tloop))
        return self._Ltr1200.get_kp(tloop.channel)

    def set_ki(self, tloop, ki):
        """
        Set the PID I value
        Raises NotImplementedError if not defined by inheriting class

        Args:
           tloop: Loop class type object
           ki: the ki value
        """
        log_debug(self, "Controller:set_ki: %s %s" % (tloop, ki))
        self._Ltr1200.set_ki(ki, tloop.channel)

    def get_ki(self, tloop):
        """
        Get the PID I value
        Raises NotImplementedError if not defined by inheriting class

        Args:
           tloop: Loop class type object

        Returns:
           ki value
        """
        log_debug(self, "Controller:get_ki: %s" % (tloop))
        return self._Ltr1200.get_ki(tloop.channel)

    def set_kd(self, tloop, kd):
        """
        Set the PID D value
        Raises NotImplementedError if not defined by inheriting class

        Args:
           tloop: Loop class type object
           kd: the kd value
        """
        log_debug(self, "Controller:set_kd: %s %s" % (tloop, kd))
        self._Ltr1200.set_kd(kd, tloop.channel)

    def get_kd(self, tloop):
        """
        Reads the PID D value
        Raises NotImplementedError if not defined by inheriting class

        Args:
           tloop: Output class type object

        Returns:
           kd value
        """
        log_debug(self, "Controller:get_kd: %s" % (tloop))
        return self._Ltr1200.get_kd(tloop.channel)

    def start_regulation(self, tloop):
        """
        Starts the regulation process.
        It must NOT start the ramp, use 'start_ramp' to do so.
        Raises NotImplementedError if not defined by inheriting class

        Args:
           tloop:  Loop class type object
        """
        log_debug(self, "Controller:start_regulation: %s" % (tloop))
        # 3 set to HW enable
        self._Ltr1200.set_output_status(3, tloop.output.config["channel"])

    def stop_regulation(self, tloop):
        """
        Stops the regulation process.
        It must NOT stop the ramp, use 'stop_ramp' to do so.
        Raises NotImplementedError if not defined by inheriting class

        Args:
           tloop:  Loop class type object
        """
        log_debug(self, "Controller:stop_regulation: %s:" % (tloop))
        raise NotImplementedError

    # ------ setpoint methods ------------------------

    def set_setpoint(self, tloop, sp, **kwargs):
        """
        Set the current setpoint (target value).
        It must NOT start the PID process, use 'start_regulation' to do so.
        Raises NotImplementedError if not defined by inheriting class

        Args:
           tloop:  Loop class type object
           sp:     setpoint (in tloop.input unit)
           **kwargs: auxilliary arguments
        """
        log_debug(self, "Controller:set_setpoint: %s %s" % (tloop, sp))
        self._Ltr1200.set_target_temperature(sp, tloop.channel)

    def get_setpoint(self, tloop):
        """
        Get the current setpoint (target value)
        Raises NotImplementedError if not defined by inheriting class

        Args:
           tloop:  Loop class type object

        Returns:
           (float) setpoint value (in tloop.input unit).
        """
        log_debug(self, "Controller:get_setpoint: %s" % (tloop))
        answer = self._Ltr1200.get_target_temperature(tloop.channel)
        answer = "%.3f" % answer
        answer = float(answer)
        return answer

    def get_working_setpoint(self, tloop):
        """
        Get the current working setpoint (setpoint along ramping)
        Raises NotImplementedError if not defined by inheriting class

        Args:
           tloop:  Loop class type object

        Returns:
           (float) working setpoint value (in tloop.input unit).
        """
        log_debug(self, "Controller:get_working_setpoint: %s" % (tloop))
        return self._Ltr1200.get_object_temperature(tloop.channel)

    # ------ setpoint ramping methods (optional) ------------------------

    def start_ramp(self, tloop, sp, **kwargs):
        """
        Start ramping to a setpoint
        It must NOT start the PID process, use 'start_regulation' to do so.
        Raises NotImplementedError if not defined by inheriting class

        Replace 'Raises NotImplementedError' by 'pass' if the controller has ramping but doesn't have a method to explicitly starts the ramping.
        Else if this function returns 'NotImplementedError', then the Loop 'tloop' will use a SoftRamp instead.

        Args:
           tloop:  Loop class type object
           sp:       setpoint (in tloop.input unit)
           **kwargs: auxilliary arguments
        """
        # loop_channel = tloop.channel
        # log_debug(self, "Controller:start_ramp: %s %s:channel: %s" % (tloop, sp, loop_channel))
        log_debug(self, "Controller:start_ramp: %s %s" % (tloop, sp))
        # self.set_setpoint(tloop, sp, channel=loop_channel)
        self.set_setpoint(tloop, sp)

    def stop_ramp(self, tloop):
        """
        Stop the current ramping to a setpoint
        It must NOT stop the PID process, use 'stop_regulation' to do so.
        Raises NotImplementedError if not defined by inheriting class

        Args:
           tloop:  Loop class type object
        """
        log_debug(self, "Controller:stop_ramp: %s" % (tloop))
        raise NotImplementedError

    def is_ramping(self, tloop):
        """
        Get the ramping status.
        Raises NotImplementedError if not defined by inheriting class

        Args:
           tloop:  Loop class type object

        Returns:
           (bool) True if ramping, else False.
        """
        log_debug(self, "Controller:is_ramping: %s" % (tloop))
        pass
        # raise NotImplementedError

    def set_ramprate(self, tloop, rate):
        """
        Set the ramp rate
        Raises NotImplementedError if not defined by inheriting class

        Args:
           tloop:  Loop class type object
           rate:   ramp rate (in input unit per second)
        """
        log_debug(self, "Controller:set_ramprate: %s %s" % (tloop, rate))
        self._Ltr1200.set_ramprate(rate, tloop.channel)

    def get_ramprate(self, tloop):
        """
        Get the ramp rate
        Raises NotImplementedError if not defined by inheriting class

        Args:
           tloop:  Loop class type object

        Returns:
           ramp rate (in input unit per second)
        """
        log_debug(self, "Controller:get_ramprate: %s" % (tloop))
        return self._Ltr1200.get_ramprate(tloop.channel)

    # # --- controller methods to handle the ramping on the Output (optional) -----------

    def start_output_ramp(self, toutput, value, **kwargs):
        """
        Start ramping on the output
        Raises NotImplementedError if not defined by inheriting class

        Replace 'Raises NotImplementedError' by 'pass' if the controller has
        output ramping but doesn't have a method to explicitly starts the output
        ramping.  Else if this function returns 'NotImplementedError', then the
        output 'toutput' will use a SoftRamp instead.

        Args:
            toutput: Output class type object
            value:   Target value for the output (in output unit)
            **kwargs: auxilliary arguments
        """
        log_debug(self, "Controller:start_output_ramp: %s %s" % (toutput, value))


class Ltr1200SampleEnv(Ltr1200):
    """
    Derivated class for meerstetter temperature controller that does not break
    safety interlocks on sample environment pool equipment.
    """

    def start_regulation(self, tloop):
        """
        Starts the regulation process in a safe way for the sample env
        controller (with external interlocks).
        It must NOT start the ramp, use 'start_ramp' to do so.
        Raises NotImplementedError if not defined by inheriting class

        Args:
           tloop:  Loop class type object
        """
        log_debug(self, f"Controller:start_regulation: {tloop}")
        # 3 set to 'HW Enable'  the output
        self._Ltr1200.set_output_status(3, tloop.output.config["channel"])
