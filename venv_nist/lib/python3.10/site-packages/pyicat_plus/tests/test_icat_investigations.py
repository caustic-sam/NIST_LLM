import random
import datetime
from typing import Optional

import pytest

from ..client.investigation import _select_investigation


def test_investigation_none():
    investigations = _Investigations()

    # No investigations
    investigations.check(datetime.timedelta())

    # Ignore open-ended
    # _____|‾‾‾‾‾
    #         |
    investigations.session(datetime.timedelta(days=1, seconds=1))
    investigations.check(datetime.timedelta(days=2), allow_open_ended=False)


def test_investigation_inside():
    investigations = _Investigations()

    # Inside single investigation
    # _____|‾‾‾‾‾|_____
    #        |
    investigations.session(
        datetime.timedelta(days=1), datetime.timedelta(days=3), expected=True
    )
    investigations.check(datetime.timedelta(days=1, seconds=1))

    # Inside the earliest of two investigation
    # _____|‾‾‾‾‾|___________
    # ___________|‾‾‾‾‾|_____
    #        |
    investigations.session(
        datetime.timedelta(days=1), datetime.timedelta(days=2), expected=True
    )
    investigations.session(datetime.timedelta(days=2), datetime.timedelta(days=3))
    investigations.check(datetime.timedelta(days=1, hours=2))

    # ___________|‾‾‾‾‾|_____
    # _____|‾‾‾‾‾|___________
    #        |
    investigations.session(datetime.timedelta(days=2), datetime.timedelta(days=3))
    investigations.session(
        datetime.timedelta(days=1), datetime.timedelta(days=2), expected=True
    )
    investigations.check(datetime.timedelta(days=1, hours=2))

    # On the border of two subsequent investigations
    # _____|‾‾‾‾‾|___________
    # ___________|‾‾‾‾‾|_____
    #            |
    investigations.session(datetime.timedelta(days=1), datetime.timedelta(days=2))
    investigations.session(
        datetime.timedelta(days=2), datetime.timedelta(days=3), expected=True
    )
    investigations.check(datetime.timedelta(days=2))

    # ___________|‾‾‾‾‾|_____
    # _____|‾‾‾‾‾|___________
    #            |
    investigations.session(datetime.timedelta(days=2), datetime.timedelta(days=3))
    investigations.session(
        datetime.timedelta(days=1), datetime.timedelta(days=2), expected=True
    )
    investigations.check(datetime.timedelta(days=2))

    # Inside closed-ended and open-ended investigation
    # _____|‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾
    # ___________|‾‾‾‾‾|_____
    #              |
    investigations.session(datetime.timedelta(days=1))
    investigations.session(
        datetime.timedelta(days=2), datetime.timedelta(days=3), expected=True
    )
    investigations.check(datetime.timedelta(days=2, seconds=1))

    # ___________|‾‾‾‾‾|_____
    # _____|‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾
    #              |
    investigations.session(
        datetime.timedelta(days=2), datetime.timedelta(days=3), expected=True
    )
    investigations.session(datetime.timedelta(days=1))
    investigations.check(datetime.timedelta(days=2, seconds=1))

    # On the border of closed-ended and open-ended investigation (not same start date)
    # _____|‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾
    # ___________|‾‾‾‾‾|_____
    #            |
    investigations.session(datetime.timedelta(days=1))
    investigations.session(
        datetime.timedelta(days=2), datetime.timedelta(days=3), expected=True
    )
    investigations.check(datetime.timedelta(days=2))

    # ___________|‾‾‾‾‾|_____
    # _____|‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾
    #            |
    investigations.session(
        datetime.timedelta(days=2), datetime.timedelta(days=3), expected=True
    )
    investigations.session(datetime.timedelta(days=1))
    investigations.check(datetime.timedelta(days=2))

    # On the border of closed-ended and open-ended investigation (same start date)
    # ___________|‾‾‾‾‾‾‾‾‾‾‾
    # ___________|‾‾‾‾‾|_____
    #            |
    investigations.session(datetime.timedelta(days=2))
    investigations.session(
        datetime.timedelta(days=2), datetime.timedelta(days=3), expected=True
    )
    investigations.check(datetime.timedelta(days=2))

    investigations.session(datetime.timedelta(days=2))
    investigations.session(
        datetime.timedelta(days=2), datetime.timedelta(days=3), expected=True
    )
    investigations.check(datetime.timedelta(days=2), allow_open_ended=False)

    # ___________|‾‾‾‾‾|_____
    # ___________|‾‾‾‾‾‾‾‾‾‾‾
    #            |
    investigations.session(
        datetime.timedelta(days=2), datetime.timedelta(days=3), expected=True
    )
    investigations.session(datetime.timedelta(days=2))
    investigations.check(datetime.timedelta(days=2))

    investigations.session(
        datetime.timedelta(days=2), datetime.timedelta(days=3), expected=True
    )
    investigations.session(datetime.timedelta(days=2))
    investigations.check(datetime.timedelta(days=2), allow_open_ended=False)

    # Select inside overlapping open-ended investigations
    # _____|‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾
    # ___________|‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾
    # _________________|‾‾‾‾‾‾‾‾‾‾‾
    #        |
    investigations.session(datetime.timedelta(days=1), expected=True)
    investigations.session(datetime.timedelta(days=2))
    investigations.session(datetime.timedelta(days=3))
    investigations.check(datetime.timedelta(days=1, seconds=1))

    # _____|‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾
    # ___________|‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾
    # _________________|‾‾‾‾‾‾‾‾‾‾‾
    #             |
    investigations.session(datetime.timedelta(days=1))
    investigations.session(datetime.timedelta(days=2), expected=True)
    investigations.session(datetime.timedelta(days=3))
    investigations.check(datetime.timedelta(days=2, seconds=1))

    # _____|‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾
    # ___________|‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾
    # _________________|‾‾‾‾‾‾‾‾‾‾‾
    #                   |
    investigations.session(datetime.timedelta(days=1))
    investigations.session(datetime.timedelta(days=2))
    investigations.session(datetime.timedelta(days=3), expected=True)
    investigations.check(datetime.timedelta(days=3, seconds=1))

    # Inside two overlapping closed-ended investigations
    # _____|‾‾‾‾‾|________
    # ________|‾‾‾‾‾|_____
    #           |
    investigations.session(datetime.timedelta(days=1), datetime.timedelta(days=3))
    investigations.session(
        datetime.timedelta(days=2), datetime.timedelta(days=4), expected=True
    )
    investigations.check(datetime.timedelta(days=2, hours=2))

    # ________|‾‾‾‾‾|_____
    # _____|‾‾‾‾‾|________
    #           |
    investigations.session(datetime.timedelta(days=2), datetime.timedelta(days=4))
    investigations.session(
        datetime.timedelta(days=1), datetime.timedelta(days=3), expected=True
    )
    investigations.check(datetime.timedelta(days=2, hours=2))

    # Inside two overlapping open and closed-ended investigations
    # _____|‾‾‾‾‾‾‾‾‾‾‾‾‾‾
    # ________|‾‾‾‾‾|_____
    #           |
    investigations.session(datetime.timedelta(days=1))
    investigations.session(
        datetime.timedelta(days=2), datetime.timedelta(days=4), expected=True
    )
    investigations.check(datetime.timedelta(days=2, hours=2))

    # ________|‾‾‾‾‾|_____
    # _____|‾‾‾‾‾‾‾‾‾‾‾‾‾‾
    #           |
    investigations.session(
        datetime.timedelta(days=2), datetime.timedelta(days=4), expected=True
    )
    investigations.session(datetime.timedelta(days=1))
    investigations.check(datetime.timedelta(days=2, hours=2))

    # _____|‾‾‾‾‾‾‾‾‾‾‾‾‾‾
    # ________|‾‾‾‾‾|_____
    #         |
    investigations.session(datetime.timedelta(days=1))
    investigations.session(
        datetime.timedelta(days=2), datetime.timedelta(days=4), expected=True
    )
    investigations.check(datetime.timedelta(days=2))

    # ________|‾‾‾‾‾|_____
    # _____|‾‾‾‾‾‾‾‾‾‾‾‾‾‾
    #         |
    investigations.session(
        datetime.timedelta(days=2), datetime.timedelta(days=4), expected=True
    )
    investigations.session(datetime.timedelta(days=1))
    investigations.check(datetime.timedelta(days=2))


def test_investigation_inbetween():
    investigations = _Investigations()

    # Between two investigations
    # _____|‾‾‾‾‾|_________________
    # _________________|‾‾‾‾‾|_____
    #             |
    investigations.session(
        datetime.timedelta(days=1), datetime.timedelta(days=2), expected=True
    )
    investigations.session(datetime.timedelta(days=3), datetime.timedelta(days=4))
    investigations.check(datetime.timedelta(days=2, hours=2))

    # _____|‾‾‾‾‾|_________________
    # _________________|‾‾‾‾‾|_____
    #                 |
    investigations.session(datetime.timedelta(days=1), datetime.timedelta(days=2))
    investigations.session(
        datetime.timedelta(days=3), datetime.timedelta(days=4), expected=True
    )
    investigations.check(datetime.timedelta(days=2, hours=13))

    # _____|‾‾‾‾‾|_________________
    # _________________|‾‾‾‾‾‾‾‾‾‾‾
    #             |
    investigations.session(
        datetime.timedelta(days=1), datetime.timedelta(days=2), expected=True
    )
    investigations.session(datetime.timedelta(days=3))
    investigations.check(datetime.timedelta(days=2, hours=2))

    # _____|‾‾‾‾‾|_________________
    # _________________|‾‾‾‾‾‾‾‾‾‾‾
    #                 |
    investigations.session(datetime.timedelta(days=1), datetime.timedelta(days=2))
    investigations.session(datetime.timedelta(days=3), expected=True)
    investigations.check(datetime.timedelta(days=2, hours=13))

    # _____|‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾
    # __________|‾‾‾‾‾|_________________
    # ______________________|‾‾‾‾‾|_____
    #                      |
    investigations.session(datetime.timedelta(days=1))
    investigations.session(datetime.timedelta(days=2), datetime.timedelta(days=3))
    investigations.session(
        datetime.timedelta(days=5), datetime.timedelta(days=6), expected=True
    )
    investigations.check(datetime.timedelta(days=4, hours=13))

    # __________|‾‾‾‾‾|_________________
    # ______________________|‾‾‾‾‾|_____
    # _____|‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾
    #                      |
    investigations.session(datetime.timedelta(days=2), datetime.timedelta(days=3))
    investigations.session(
        datetime.timedelta(days=5), datetime.timedelta(days=6), expected=True
    )
    investigations.session(datetime.timedelta(days=1))
    investigations.check(datetime.timedelta(days=4, hours=13))


def test_investigation_outside():
    investigations = _Investigations()

    # Date before investigation
    # _____|‾‾‾‾‾|_____
    #    |
    investigations.session(
        datetime.timedelta(days=1, seconds=1), datetime.timedelta(days=2), expected=True
    )
    investigations.check(datetime.timedelta(days=1))

    # Date before open-ended investigation
    # _____|‾‾‾‾‾
    # |
    investigations.session(datetime.timedelta(days=1), expected=True)
    investigations.check(datetime.timedelta())

    # Date after investigation
    # _____|‾‾‾‾‾|_____
    #             |
    investigations.session(
        datetime.timedelta(days=1), datetime.timedelta(days=2), expected=True
    )
    investigations.check(datetime.timedelta(days=2, seconds=1))

    # Date before two investigations
    # _____|‾‾‾‾‾|_________________
    # _________________|‾‾‾‾‾|_____
    #  |
    investigations.session(
        datetime.timedelta(days=1), datetime.timedelta(days=2), expected=True
    )
    investigations.session(datetime.timedelta(days=3), datetime.timedelta(days=4))
    investigations.check(datetime.timedelta(hours=2))

    # Date after two investigations
    # _____|‾‾‾‾‾|_________________
    # _________________|‾‾‾‾‾|_____
    #                          |
    investigations.session(datetime.timedelta(days=1), datetime.timedelta(days=2))
    investigations.session(
        datetime.timedelta(days=3), datetime.timedelta(days=4), expected=True
    )
    investigations.check(datetime.timedelta(days=4, hours=2))


def test_investigation_dateonly():
    investigations = _Investigations()

    # One investigation starts on the same day
    # _____|‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾
    # ___________|‾‾‾‾‾|_____
    #           |
    investigations.session(datetime.timedelta(days=1))
    investigations.session(
        datetime.timedelta(days=3), datetime.timedelta(days=4), expected=True
    )
    with pytest.raises(AssertionError):
        investigations.check(datetime.timedelta(days=2, hours=23))

    investigations.session(datetime.timedelta(days=1))
    investigations.session(
        datetime.timedelta(days=3), datetime.timedelta(days=4), expected=True
    )
    investigations.check(datetime.timedelta(days=2, hours=23), date_only=True)

    # Two investigations start on the same day
    # _____|‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾
    # ___________|‾‾‾‾‾|_____
    # ___________|‾‾‾‾‾|_____
    #           |
    investigations.session(datetime.timedelta(days=1))
    investigations.session(datetime.timedelta(days=3), datetime.timedelta(days=4))
    investigations.session(
        datetime.timedelta(days=3), datetime.timedelta(days=4), expected=True
    )
    investigations.check(datetime.timedelta(days=2, hours=23), date_only=True)


class _Investigations:
    def __init__(self):
        self._reset()

    def session(
        self,
        start_offset: datetime.timedelta,
        end_offset: Optional[datetime.timedelta] = None,
        expected: bool = False,
    ) -> None:
        startdate = self._date + start_offset
        investigation = {
            "id": len(self._investigations),
            "startDate": startdate.astimezone().isoformat(),
            "unique": random.uniform(0, 1),
        }
        if end_offset is not None:
            enddate = self._date + end_offset
            investigation["endDate"] = enddate.astimezone().isoformat()
        self._investigations.append(investigation)
        if expected:
            self._expected = investigation

    def _reset(self):
        self._investigations = list()
        self._expected = None
        self._nextid = 10000
        # +1 day: CEST -> EST
        self._date = datetime.datetime(year=2023, month=10, day=28, hour=8)

    def check(
        self,
        offset: datetime.timedelta,
        allow_open_ended: bool = True,
        date_only: bool = False,
    ):
        if self._investigations:
            random.shuffle(self._investigations)
        date = (self._date + offset).astimezone()
        if date_only:
            date = date.date()
        investigation = _select_investigation(
            self._investigations, date=date, allow_open_ended=allow_open_ended
        )
        assert investigation == self._expected
        self._reset()
