import unittest
from datetime import date

from flowcrate.dates import LATEST, parse_day, spoken_date

# A Friday.
TODAY = date(2026, 10, 23)


class ParseDayTests(unittest.TestCase):
    def check(self, text, expected):
        self.assertEqual(parse_day(text, today=TODAY), expected, text)

    def test_latest_words(self):
        for text in ("", None, "today", "latest", "the newest one", "Today's"):
            self.check(text, LATEST)

    def test_relative_days(self):
        self.check("yesterday", date(2026, 10, 22))
        self.check("Yesterday's Flow Crate", date(2026, 10, 22))
        self.check("the day before yesterday", date(2026, 10, 21))
        self.check("3 days ago", date(2026, 10, 20))
        self.check("three days ago", date(2026, 10, 20))
        self.check("a week ago", date(2026, 10, 16))
        self.check("two weeks ago", date(2026, 10, 9))
        self.check("last week", date(2026, 10, 16))

    def test_weekdays(self):
        self.check("last Wednesday", date(2026, 10, 21))
        self.check("Wednesday.", date(2026, 10, 21))
        self.check("on monday", date(2026, 10, 19))
        self.check("the Flow Crate from last Thursday", date(2026, 10, 22))
        # Today's weekday alone means today; "last" means a week ago.
        self.check("Friday", date(2026, 10, 23))
        self.check("last Friday", date(2026, 10, 16))

    def test_month_and_day(self):
        self.check("October 15", date(2026, 10, 15))
        self.check("October 15th", date(2026, 10, 15))
        self.check("Oct. 15", date(2026, 10, 15))
        self.check("flow crate from october fifteenth", date(2026, 10, 15))
        self.check("the fifteenth of October", date(2026, 10, 15))
        self.check("15th of October", date(2026, 10, 15))
        self.check("October twenty first", date(2026, 10, 21))
        self.check("September 3rd, 2025", date(2025, 9, 3))
        self.check("Wednesday, October 1st", date(2026, 10, 1))

    def test_month_day_in_future_means_last_year(self):
        self.check("December 25", date(2025, 12, 25))

    def test_numeric_dates(self):
        self.check("10/15", date(2026, 10, 15))
        self.check("10/15/2025", date(2025, 10, 15))
        self.check("2026-10-15", date(2026, 10, 15))

    def test_unreadable(self):
        for text in ("banana", "February 30", "play something nice"):
            self.check(text, None)


class SpokenDateTests(unittest.TestCase):
    def test_same_year_omits_year(self):
        self.assertEqual(spoken_date(date(2026, 10, 21), today=TODAY), "Wednesday, October 21")

    def test_other_year_includes_year(self):
        self.assertEqual(spoken_date(date(2025, 12, 25), today=TODAY), "Thursday, December 25, 2025")
