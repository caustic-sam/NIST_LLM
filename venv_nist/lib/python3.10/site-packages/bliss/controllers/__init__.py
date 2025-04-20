"""Repository of controllers

Here you will find the complete catalog of available Bliss controllers.

Main controller subsystems implemented so far:

* :mod:`~bliss.controllers.correlator`
* :mod:`~bliss.controllers.ct2`
* :mod:`~bliss.controllers.diffractometers`
* :mod:`~bliss.controllers.interferometers`
* :mod:`~bliss.controllers.lima`
* :mod:`~bliss.controllers.lima2`
* :mod:`~bliss.controllers.mca`
* :mod:`~bliss.controllers.monochromator`
* :mod:`~bliss.controllers.motors`
* :mod:`~bliss.controllers.oscilloscope`
* :mod:`~bliss.controllers.powersupply`
* :mod:`~bliss.controllers.regulation`
* :mod:`~bliss.controllers.sca`
* :mod:`~bliss.controllers.spectrometers`
* :mod:`~bliss.controllers.speedgoat`
* :mod:`~bliss.controllers.wago`

All other controllers have too much specific functionality to be categorized.
This may change in the future when more controllers patterns are discovered
and their common API and functionality can be determined

.. autosummary::
    :toctree:

    actuator
    ah401
    andeen_hagerling_2550a
    apc
    bcdu8
    bliss_controller_mockup
    bliss_controller
    calccnt_background
    celeroton
    city
    counter
    correlator
    ct2
    diffractometers
    ebv
    emh
    expression_based_calc
    gasrig
    interferometers
    intraled
    kb
    keithley_scpi_mapping
    keithley
    keithley428
    keithley3706
    keller
    lima
    lima2
    machinfo
    matt
    mca
    mcce
    mcs_la2000
    moco
    monochromator
    motor
    motors
    multiplepositions
    multiplexer
    musst
    nano_bpm
    opiom
    opiomoutput
    oscilloscope
    pepu
    powersupply
    regulator
    regulation
    rontec
    sca
    shutters
    simulation_actuator
    simulation_calc_counter
    simulation_counter
    simulation_diode
    spectrometers
    speedgoat
    tango_attr_as_counter
    tango_elettra
    tango_tfg
    tflens
    transfocator
    transmission
    vacuum_gauge
    wago
    white_beam_attenuator
"""
