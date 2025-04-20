#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Examples of calibrations for test and demo purposes.
"""

import logging
import os
import numpy
from xcalibu import Xcalibu

XCALIBU_DIRBASE = os.path.dirname(os.path.realpath(__file__))

log = logging.getLogger("DEMO_XCALIBU")


# Some data for demo
demo_calib_string = """
# TEST calibration
# Type TABLE
# Comments are starting with '#'
# Spaces are mainly ignored.

CALIB_NAME = B52
CALIB_TYPE = TABLE
CALIB_TIME = 1400081171.300155
CALIB_DESC = 'test table : example of matching lines'
#CALIB_TITI = 14

B52[0.8e-2] = -0.83e-2
B52[1]=1
B52[3]= 2
B52[5]=-12
B52 [6]=  -3
B52 [7]=   2
B52[10]=   4.5
 B52[13]=7.5
   B52[15]=18.5
B52[18]=0.5e2
B52[23]=	42
B52[23.1]=0.61e2
B52[27.401] = 0.72e2
B52[32]=  62
B52[0.5e2] = +0.53e2
"""


def demo_xcalibu_1(do_plot):
    """
    string calibration
    """
    log = logging.getLogger("XCALIBU")

    # log.info("===== use: demo_calib_string str ; POLYFIT ; fit_order = 2 ===================\n")
    myCalibString = Xcalibu(
        calib_string=demo_calib_string, fit_order=2, reconstruction_method="POLYFIT"
    )
    log.info("TEST -         demo_calib_string(%f) = %f" % (5, myCalibString.get_y(5)))
    log.info("TEST - inverse_demo_calib_string(%f) = %f" % (4, myCalibString.get_x(4)))

    myCalibString.print_info()

    if do_plot:
        myCalibString.plot()


def demo_xcalibu_2(do_plot):
    """
    2nd order POLY calibration from file.
    """
    myCalibPoly = Xcalibu(calib_file_name=XCALIBU_DIRBASE + "/examples/poly.calib")
    myCalibPoly.print_info()

    if do_plot:
        myCalibPoly.plot()


def demo_xcalibu_3(do_plot):
    """
    TABLE calibration from file with POLYFIT reconstruction method
    """
    log = logging.getLogger("XCALIBU")

    myCalib2 = Xcalibu(
        calib_file_name=XCALIBU_DIRBASE + "/examples/undu_table.calib",
        fit_order=2,
        reconstruction_method="POLYFIT",
    )
    log.info("TEST - Gap for %f keV : %f" % (5, myCalib2.get_y(5)))

    myCalib2.print_info()

    if do_plot:
        myCalib2.plot()


def demo_xcalibu_U32a(do_plot):
    """
    U32a undu table calibration.
    U32a_1_table.dat looks like:
        #E    u32a
        4.500 13.089
        4.662 13.410
        4.823 13.727
        ...
        10.444 34.814
        10.500 36.708
        10.675 41.711
    """
    log = logging.getLogger("XCALIBU")
    log.info("demo_xcalibu_U32a")
    myCalibU32a = Xcalibu(calib_name="U32ATABLE", calib_type="TABLE",
                          calib_file_name=XCALIBU_DIRBASE + "/examples/U32a_1_table.txt",
                          description="2cols table calib of U32A undulator")
    # myCalibU32a.set_reconstruction_method("INTERPOLATION", kind="cubic")
    myCalibU32a.set_reconstruction_method("INTERPOLATION", kind="quadratic")

    myCalibU32a.set_x_limits(None, None)
    myCalibU32a.set_interpol_fill_value(numpy.nan)

    myCalibU32a.compute_interpolation()

    myCalibU32a.print_info()

    print(f"   myCalibU32a(-42) = {myCalibU32a.get_y(-42)}")
    print(f"   myCalibU32a(4.5) = {myCalibU32a.get_y(4.5)}")
    print(f"     myCalibU32a(7) = {myCalibU32a.get_y(7)}")
    print(f"myCalibU32a(10.675) = {myCalibU32a.get_y(10.675)}")
    print(f"   myCalibU32a(+42) = {myCalibU32a.get_y(+42)}")

#    print("get_x(get_y(7))=", myCalibU32a.get_x(float(myCalibU32a.get_y(7))))
    # quadratic -> 7.0000295
    #     cubic -> 6.9997924

#    print("get_x(get_y(7.04))=", myCalibU32a.get_x(float(myCalibU32a.get_y(7.04))))
    # quadratic -> 7.04
    #     cubic -> 7.039999

    myCalibU32a.print_info()
    if do_plot:
        myCalibU32a.plot()


def demo_xcalibu_table15(do_plot):
    """
    TABLE calibration examples/table.calib
    15 points table calib (legacy format B52[1]=1)
    """
    log = logging.getLogger("XCALIBU")

    myCalibTable = Xcalibu(
        calib_file_name=XCALIBU_DIRBASE + "/examples/table.calib",
        fit_order=2,
        reconstruction_method="INTERPOLATION",
    )
    log.info("TEST - Gap for %f keV : %f" % (1, myCalibTable.get_y(1)))
    log.info("TEST - Gap for %f keV : %f" % (2, myCalibTable.get_y(2)))
    log.info("TEST - Gap for %f keV : %f" % (4, myCalibTable.get_y(4)))
    log.info("TEST - Gap for %f keV : %f" % (9, myCalibTable.get_y(9)))
    # errors :
    #    log.info("TEST - Gap for %f keV : %f" % (0.5, myCalibTable.get_y(0.5)))
    #    log.info("TEST - Gap for %f keV : %f" % (12, myCalibTable.get_y(12)))
    #  myCalibTable.get_x(42)

    myCalibTable.print_info()

    if do_plot:
        # myCalibTable.plot("cal", "var", "inv") # , save=True, plot_file_name="fff")
        # save : fff_cal.pdf  fff_var.pdf   fff_inv.pdf
        myCalibTable.plot( "cal", "var", "inv", display=False, save=True, file_name="/tmp/toto")


    print("ffffffffffffffffffffffffff")



def demo_xcalibu_cubic(do_plot):
    """
    10 points table ~~ cubic func
    """
    log = logging.getLogger("XCALIBU")

    myCalibCubic = Xcalibu(
        calib_file_name=XCALIBU_DIRBASE + "/examples/cubic.calib",
        fit_order=3,
        reconstruction_method="POLYFIT",
    )

    myCalibCubic.print_info()

    if do_plot:
        myCalibCubic.plot()

def demo_xcalibu_RingRy(do_plot):
    """
    RingRy TABLE calibration 360 points
    """
    log = logging.getLogger("XCALIBU")


    myCalibRingRy = Xcalibu(
        calib_file_name=XCALIBU_DIRBASE + "/examples/hpz_ring_Ry.calib",
        fit_order=5,
        reconstruction_method="POLYFIT",
    )
    print("saving table demo....")
    myCalibRingRy.set_calib_file_name("ttt.calib")
    myCalibRingRy.save()

    myCalibRingRy.print_info()

    if do_plot:
        myCalibRingRy.plot()


def demo_xcalibu_dynamic_table(do_plot):
    """
    Dynamicaly populated calibration
    """
    log = logging.getLogger("XCALIBU")

    print("Example : creation of an empty TABLE calib then populate it with in-memory data")
    myDynamicCalib = Xcalibu()
    myDynamicCalib.set_calib_file_name("ddd.calib")
    myDynamicCalib.set_calib_name("DynCalTable")
    myDynamicCalib.set_calib_type("TABLE")
    myDynamicCalib.set_calib_time("1234.5678")
    myDynamicCalib.set_calib_description("dynamic calibration created for demo")
    myDynamicCalib.set_raw_x(numpy.linspace(1, 10, 10))
    myDynamicCalib.set_raw_y(numpy.array([3, 6, 5, 4, 2, 5, 7, 3, 7, 4]))
    myDynamicCalib.set_reconstruction_method("INTERPOLATION", "linear")
    myDynamicCalib.compute_interpolation()
    myDynamicCalib.save()
    print("myDynamicCalib.get_y(2.3)=%f" % myDynamicCalib.get_y(2.3))

    myDynamicCalib.print_info()

    if do_plot:
        myDynamicCalib.plot()

    myDynamicCalib.delete(x=1)
    myDynamicCalib.insert(x=11, y=0)

    if do_plot:
        myDynamicCalib.plot()


def demo_xcalibu_PolyB(do_plot):
    """
    Dynamic Poly calibration
    """

    coeffsB = [0, -0.0004, 0.0223, -0.2574, 1.4143, 1.0227]
    coeffsB = [1, 0, 0]  # cst  x  x2
    coeffsB = [-8, -6, 3, 1]  # cst  x  x2 x3   ===>   -8.0 - 6.0·x¹ + 3.0·x² + 1.0·x³
    # u32a
    coeffsB = [1500.46611131, -1346.88761616, 499.06810627, -97.02312684, 10.45705476, -0.59283464, 0.01382656]

    myCalibPolyB = Xcalibu(calib_name="PolyB", description="Polynom calib deg=? for PolyB")
    myCalibPolyB.set_calib_type("POLY")
    myCalibPolyB.set_coeffs(coeffsB)
    # myCalibPolyB.set_x_limits(-15, 25)  # For POLY, limits are used for plot.
    # myCalibPolyB.set_x_limits(-800, 3800)  # no more increasing after ~33.
    myCalibPolyB.set_x_limits(5, 10)

    myCalibPolyB.set_sampling_nb_points(100)
    myCalibPolyB.check_monotonic()

#    assert numpy.isclose(myCalibPolyB.get_y(0), 1.0227)
#    assert numpy.isclose(myCalibPolyB.get_y(-10), -65.1603)
#    assert numpy.isclose(myCalibPolyB.get_y(22), 51.3037)
    print(f"f(0)={myCalibPolyB.get_y(0)}", end="    ")
    print(f"f(-10)={myCalibPolyB.get_y(-10)}", end="   ")
    print(f"f(22)={myCalibPolyB.get_y(22)}")

    print(f"f(-4)={myCalibPolyB.get_y(-4)}")
    print(f"f(-1)={myCalibPolyB.get_y(-1)}")
    print(f"f(2)={myCalibPolyB.get_y(2)}")

    myCalibPolyB.print_info()

    if do_plot:
        myCalibPolyB.plot()


def demo_xcalibu_PolyU42b(do_plot):
    """
    Dynamic Poly calibration
    """
    coeffsU42b = [-1.18050708, 13.33015136, -3.74365885, 0.60740689, -0.02089632, -0.00541641, 0.00050147]

    myCalibPolyU42b = Xcalibu(calib_name="PolyU42b", description="Polynom calib 6 for U42b")
    myCalibPolyU42b.set_calib_type("POLY")
    myCalibPolyU42b.set_coeffs(coeffsU42b)
    myCalibPolyU42b.set_x_limits(2.1, 7.5)

    # myCalibPolyU42b.set_sampling_nb_points(20)
    myCalibPolyU42b.set_reconstruction_method("INTERPOLATION")
    myCalibPolyU42b.check_monotonic()
    myCalibPolyU42b.compute_interpolation()

    myCalibPolyU42b.print_info()

    # get_y
    print(f"myCalibPolyU42b.get_y(2.5) = { myCalibPolyU42b.get_y(2.5)}   (~17)")
    print(f"myCalibPolyU42b.get_y(7) = { myCalibPolyU42b.get_y(7)}     (~35)")

    # get_x
    print(f"myCalibPolyU42b.get_x(35) = { myCalibPolyU42b.get_x(35)}  (~7) ")
    print(f"myCalibPolyU42b.get_x(17) = { myCalibPolyU42b.get_x(17)}  (~2.5)")

    if do_plot:
        myCalibPolyU42b.plot()


def demo_xcalibu_poly_cubic(do_plot):
    """
    POLY cubic calibration from file
    """

    poly_cubic = Xcalibu(calib_file_name=XCALIBU_DIRBASE + "/examples/cubic_poly.calib",
                         reconstruction_method="INTERPOLATION")

    poly_cubic.set_x_limits(1, 5)
    poly_cubic.check_monotonic()
    poly_cubic.compute_interpolation()
    poly_cubic.print_info()

    print(f" poly_cubic.get_y( 1 ) = {poly_cubic.get_y(1)}")
    print(f" poly_cubic.get_y( 0 ) = {poly_cubic.get_y(0)}")
    print(f"poly_cubic.get_y( -5 ) = {poly_cubic.get_y(-5)}")
    print(f"poly_cubic.get_y( -9 ) = {poly_cubic.get_y(-9)}")
    print("--------------------------------------------------")
    print(f"poly_cubic.get_x( 12 ) = {poly_cubic.get_x(12)}")


    print("--------------------------------------------------")

    if do_plot:
        poly_cubic.plot()


def demo_xcalibu_table_non_monotonic(do_plot):
    """
    TABLE non monotonic
    """

    calib_nmt = Xcalibu(calib_type="TABLE",
                        calib_file_name=XCALIBU_DIRBASE + "/examples/U32a_table_non_monotonic.txt",
                        reconstruction_method="INTERPOLATION")

    calib_nmt.check_monotonic()
    calib_nmt.compute_interpolation()
    calib_nmt.print_info()

    print(f" calib_nmt.get_y( 1 ) = {calib_nmt.get_y(1)}")
    print(f" calib_nmt.get_y( 0 ) = {calib_nmt.get_y(0)}")
    print(f"calib_nmt.get_y( -5 ) = {calib_nmt.get_y(-5)}")
    print(f"calib_nmt.get_y( -9 ) = {calib_nmt.get_y(-9)}")
    print("--------------------------------------------------")
    print(f"calib_nmt.get_x( 12 ) = {calib_nmt.get_x(12)}")


    print("--------------------------------------------------")

    if do_plot:
        calib_nmt.plot()

'''
def demo_xcalibu_(do_plot):
    """
    ... calibration
    """
    log = logging.getLogger("XCALIBU")


    .print_info()

    if do_plot:
        .plot()
'''

'''
def demo_xcalibu_(do_plot):
    """
    ... calibration
    """
    log = logging.getLogger("XCALIBU")

    .print_info()

    if do_plot:
        .plot()
'''

def main():

    """
    arguments parsing
    """
    from optparse import OptionParser

    parser = OptionParser("demo_xcalibu.py")
    parser.add_option(
        "-d",
        "--debug",
        type="string",
        help="Available levels are :\n CRITICAL(50)\n \
                      ERROR(40)\n WARNING(30)\n INFO(20)\n DEBUG(10)",
        default="INFO",
    )

    parser.add_option(
        "-p",
        "--plot",
        action="store_true",
        dest="plot",
        default=False,
        help="Calibration plotting",
    )

    # Gather options and arguments.
    (options, args) = parser.parse_args()
    # print(options)
    # print(args)

    try:
        loglevel = getattr(logging, options.debug.upper())
    except AttributeError:
        # print "AttributeError  o.d=",options.debug
        loglevel = {
            50: logging.CRITICAL,
            40: logging.ERROR,
            30: logging.WARNING,
            20: logging.INFO,
            10: logging.DEBUG,
        }[int(options.debug)]

    print(
        "[xcalibu] - log level = %s (%d)" % (logging.getLevelName(loglevel), loglevel)
    )

    LOG_FORMAT = "%(name)s - %(levelname)s - %(message)s"
    logging.basicConfig(format=LOG_FORMAT, level=loglevel)

    print("-------------- args ------------------------")
    print(options)
    print(" plot=", options.plot)
    print("--------------------------------------")

    #demo_xcalibu_1(options.plot)
    #demo_xcalibu_2(options.plot)
    #demo_xcalibu_3(options.plot)
    #demo_xcalibu_U32a(options.plot)
    # demo_xcalibu_table15(options.plot)
    #demo_xcalibu_cubic(options.plot)
    #demo_xcalibu_RingRy(options.plot)
    #demo_xcalibu_dynamic_table(options.plot)
    #demo_xcalibu_PolyB(options.plot)
    # demo_xcalibu_PolyU42b(options.plot)
    #demo_xcalibu_poly_cubic(options.plot)
    demo_xcalibu_table_non_monotonic(options.plot)

if __name__ == "__main__":
    main()

"""
 33270 Feb  6 23:31            book5.txt : index + 5 cols
   354 Feb  2 13:56          cubic.calib : 10 points table ~~ cubic func
   416 Feb  2 13:56          gauss.calib : 11 points table, roughly a gaussian...
 12349 Feb  2 13:56    hpz_ring_Ry.calib : 360 points table piezo hexapod metrologic ring calibration
   331 Feb 22 21:00           poly.calib : order 2 poly : 28.78 - 5.57·x¹ + 0.56·x²
    93 Feb  6 23:36   table_1_column.txt : Single column datafile.
   340 Feb  2 13:56    table_2_col.calib : 15 points table calib
   128 Feb 22 21:00  table_2_columns.txt : double columns datafile.
   462 Feb  2 13:56          table.calib : 15 points table calib (legacy format B52[1]=1)
   537 Feb 22 21:00     U32a_1_table.txt : 40 lines 2 cols datafile
   443 Mar 16 13:43      u32a_poly.calib : 6th order poly
   476 Feb 22 21:00     undu_table.calib : 12 lines table
   375 Feb  2 13:56 unsorted_table.calib : 5 lines table
"""
