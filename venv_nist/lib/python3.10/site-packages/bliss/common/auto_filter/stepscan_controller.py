import numpy
from bliss import current_session
from bliss.common.cleanup import cleanup, axis as cleanup_axis
from bliss.common.auto_filter.base_controller import AutoFilter as AutoFilterBase
from bliss.common.auto_filter import scan_presets
from bliss.controllers.diffractometers import get_current_diffractometer


class AutoFilterStepScan(AutoFilterBase):
    def ascan(self, motor, start, stop, intervals, count_time, *counter_args, **kwargs):
        """
        Basically same as normal ascan with auto filter management
        """
        scan_pars = {"type": "ascan"}
        return self.anscan(
            [(motor, start, stop)],
            intervals,
            count_time,
            *counter_args,
            scan_info=scan_pars,
            **kwargs,
        )

    def a2scan(
        self,
        motor1,
        start1,
        stop1,
        motor2,
        start2,
        stop2,
        intervals,
        count_time,
        *counter_args,
        **kwargs,
    ):
        """
        Basically same as normal ascan with auto filter management
        """
        scan_pars = {"type": "ascan"}
        return self.anscan(
            [(motor1, start1, stop1), (motor2, start2, stop2)],
            intervals,
            count_time,
            *counter_args,
            scan_info=scan_pars,
            **kwargs,
        )

    def dscan(self, motor, start, stop, intervals, count_time, *counter_args, **kwargs):
        """
        Basically same as normal ascan with auto filter management
        """
        scan_pars = {"type": "dscan"}
        with cleanup(motor, restore_list=(cleanup_axis.POS,), verbose=True):
            return self.anscan(
                [(motor, start, stop)],
                intervals,
                count_time,
                *counter_args,
                scan_info=scan_pars,
                scan_type="dscan",
                name="dscan",
                **kwargs,
            )

    def d2scan(
        self,
        motor1,
        start1,
        stop1,
        motor2,
        start2,
        stop2,
        intervals,
        count_time,
        *counter_args,
        **kwargs,
    ):
        """
        Basically same as normal ascan with auto filter management
        """
        scan_pars = {"type": "dscan"}
        with cleanup(motor1, motor2, restore_list=(cleanup_axis.POS,), verbose=True):
            return self.anscan(
                [(motor1, start1, stop1), (motor2, start2, stop2)],
                intervals,
                count_time,
                *counter_args,
                scan_info=scan_pars,
                scan_type="dscan",
                name="dscan",
                **kwargs,
            )

    def anscan(
        self,
        motor_tuple_list,
        intervals,
        count_time,
        *counter_args,
        scan_info=None,
        scan_type=None,
        **kwargs,
    ):
        save_flag = kwargs.get("save", True)
        npoints = intervals + 1
        if scan_info is None:
            scan_info = dict()
        scan_info.update(
            {
                "npoints": npoints,
                "count_time": count_time,
                "save": save_flag,
            }
        )
        if kwargs.get("sleep_time") is not None:
            scan_info["sleep_time"] = kwargs.get("sleep_time")

        motors_positions = list()
        title_list = list()
        for m_tup in motor_tuple_list:
            mot = m_tup[0]
            d = mot._set_position if scan_type == "dscan" else 0
            start = m_tup[1] + d
            stop = m_tup[2] + d
            title_list.extend(
                (
                    mot.name,
                    mot.axis_rounder(start),
                    mot.axis_rounder(stop),
                )
            )
            motors_positions.extend((mot, numpy.linspace(start, stop, npoints)))

        # scan type is forced to be either aNscan or dNscan
        if scan_type == "dscan":
            scan_type = (
                f"autof.d{len(title_list)//3}scan"
                if len(title_list) // 3 > 1
                else "autof.dscan"
            )
        else:
            scan_type = (
                f"autof.a{len(title_list)//3}scan"
                if len(title_list) // 3 > 1
                else "autof.ascan"
            )
        name = kwargs.setdefault("name", None)
        if not name:
            name = scan_type

        # build the title
        args = [scan_type.replace("d", "a")]
        args += title_list
        args += [intervals, count_time]
        template = " ".join(["{{{0}}}".format(i) for i in range(len(args))])
        title = template.format(*args)
        scan_info["title"] = title

        return self.__create_step_scan(
            counter_args, motors_positions, name, scan_info, kwargs
        )

    def hklscan(self, hkl1, hkl2, intervals, count_time, *counter_args, **kwargs):
        diffracto = get_current_diffractometer()
        (h1, k1, l1) = hkl1
        (h2, k2, l2) = hkl2

        npoints = intervals + 1
        h_pos = numpy.linspace(h1, h2, npoints)
        k_pos = numpy.linspace(k1, k2, npoints)
        l_pos = numpy.linspace(l1, l2, npoints)

        h_motor = diffracto.get_axis("hkl_h")
        k_motor = diffracto.get_axis("hkl_k")
        l_motor = diffracto.get_axis("hkl_l")

        motpos_args = [(h_motor, h_pos), (k_motor, k_pos), (l_motor, l_pos)]

        kwargs.setdefault("scan_type", "autof.hklscan")
        kwargs.setdefault("name", "hklscan")
        kwargs.setdefault(
            "title",
            "hklscan {0} {1} {2} {3}".format(
                tuple(round(x, 3) for x in hkl1),
                tuple(round(x, 3) for x in hkl2),
                intervals,
                count_time,
            ),
        )
        kwargs.setdefault("scan_info", {"start": [h1, k1, l1], "stop": [h2, k2, l2]})

        return self.lookupscan(motpos_args, count_time, *counter_args, **kwargs)

    def lookupscan(
        self,
        motor_pos_tuple_list,
        count_time,
        *counter_args,
        scan_info=None,
        scan_type=None,
        **kwargs,
    ):
        npoints = len(motor_pos_tuple_list[0][1])
        motors_positions = list()
        scan_axes = set()
        for m_tup in motor_pos_tuple_list:
            mot = m_tup[0]
            if mot in scan_axes:
                raise ValueError(f"Duplicated axis {mot.name}")
            scan_axes.add(mot)
            assert len(m_tup[1]) == npoints
            motors_positions.extend((mot, m_tup[1]))

        if scan_info is None:
            scan_info = dict()
        scan_info.update(
            {
                "npoints": npoints,
                "count_time": count_time,
                "save": kwargs.get("save", True),
            }
        )
        if kwargs.get("sleep_time") is not None:
            scan_info["sleep_time"] = kwargs.get("sleep_time")

        title = "lookupscan %f on motors (%s)" % (
            count_time,
            ",".join(x[0].name for x in motor_pos_tuple_list),
        )
        scan_info["title"] = title

        scan_type = "autof.lookupscan"
        name = kwargs.setdefault("name", None)
        if not name:
            name = scan_type

        return self.__create_step_scan(
            counter_args, motors_positions, name, scan_info, kwargs
        )

    def _scan_detectors(self, counter_args):
        # add autof controller itself as counters controller
        # by the way all counters generated by autof will be acquired not only mon and det
        if counter_args:
            return list(counter_args) + [self]
        else:
            mg = current_session.active_mg
            if mg is None:
                return [self]
            else:
                return [mg, self]

    def __create_step_scan(
        self, counter_args, motors_positions, scan_name, scan_info, kwargs
    ):
        counter_args = self._scan_detectors(counter_args)
        scan = self._create_step_scan(
            counter_args, motors_positions, scan_name, scan_info, kwargs
        )
        self._add_scan_presets(scan)
        if kwargs.get("run", True):
            scan.run()
        return scan

    def _create_step_scan(
        self, counter_args, motors_positions, scan_name, scan_info, kwargs
    ):
        raise NotImplementedError

    def _add_scan_presets(self, scan):
        scan.add_preset(scan_presets.RestoreFilterPosition(self))
        scan.add_preset(scan_presets.SynchronizedFilterSet(self))
