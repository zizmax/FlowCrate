"""Turn a spoken day ("yesterday", "last Wednesday", "October 15th") into a date.

Used by the "Rewind Flow Crate" Siri shortcut, which sends whatever the user
said after Siri asks "Which day?". Parsing is deliberately forgiving: Siri's
dictation varies ("October 15th", "Oct. 15", "the fifteenth of October"), and
anything we cannot read comes back as None so the caller can ask again.
"""

import re
from datetime import date, timedelta

LATEST = "latest"

_MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9, "oct": 10,
    "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}
_WEEKDAYS = {
    "monday": 0, "mon": 0, "tuesday": 1, "tue": 1, "tues": 1, "wednesday": 2, "wed": 2,
    "thursday": 3, "thu": 3, "thur": 3, "thurs": 3, "friday": 4, "fri": 4,
    "saturday": 5, "sat": 5, "sunday": 6, "sun": 6,
}
_UNITS = {
    "one": 1, "first": 1, "a": 1, "an": 1, "two": 2, "second": 2, "three": 3, "third": 3,
    "four": 4, "fourth": 4, "five": 5, "fifth": 5, "six": 6, "sixth": 6,
    "seven": 7, "seventh": 7, "eight": 8, "eighth": 8, "nine": 9, "ninth": 9,
    "ten": 10, "tenth": 10, "eleven": 11, "eleventh": 11, "twelve": 12, "twelfth": 12,
    "thirteen": 13, "thirteenth": 13, "fourteen": 14, "fourteenth": 14,
    "fifteen": 15, "fifteenth": 15, "sixteen": 16, "sixteenth": 16,
    "seventeen": 17, "seventeenth": 17, "eighteen": 18, "eighteenth": 18,
    "nineteen": 19, "nineteenth": 19,
}
_TENS = {"twenty": 20, "twentieth": 20, "thirty": 30, "thirtieth": 30}
_LATEST_WORDS = {"latest", "newest", "recent", "new", "current", "todays"}


def parse_day(text, today=None):
    """Return a ``date``, ``LATEST`` (for "today"/"latest"/blank), or None if unreadable.

    Dates never land in the future: a bare "October 15" said in January means
    last October, and a bare weekday means the most recent one (today included).
    """
    today = today or date.today()
    words = _words(text)
    if not words or set(words) & _LATEST_WORDS or words == ["today"]:
        return LATEST
    joined = " ".join(words)

    if "day before yesterday" in joined:
        return today - timedelta(days=2)
    if "yesterday" in words:
        return today - timedelta(days=1)
    if "today" in words:
        return LATEST

    found = _numeric_date(joined, today) or _month_day(words, today)
    if found:
        return found

    ago = re.search(r"(\S+(?: \S+)?) (day|days|week|weeks) ago", joined)
    if ago:
        count = _number(ago.group(1).split())
        if count is not None:
            days = count * (7 if ago.group(2).startswith("week") else 1)
            return today - timedelta(days=days)
    if "last week" in joined:
        return today - timedelta(days=7)

    for idx, word in enumerate(words):
        if word in _WEEKDAYS:
            back = (today.weekday() - _WEEKDAYS[word]) % 7
            if back == 0 and idx > 0 and words[idx - 1] == "last":
                back = 7
            return today - timedelta(days=back)
    return None


def spoken_date(day, today=None):
    """Format a date the way Siri should say it: "Tuesday, October 21"."""
    today = today or date.today()
    text = f"{day.strftime('%A, %B')} {day.day}"
    if day.year != today.year:
        text += f", {day.year}"
    return text


def _words(text):
    text = (text or "").lower().replace("’", "'")
    text = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", text)
    text = re.sub(r"[^a-z0-9/'\- ]+", " ", text).replace("-", " ")
    text = re.sub(r"'s\b", "", text)  # "yesterday's" -> "yesterday"
    return [word.strip("'") for word in text.split() if word.strip("'")]


def _numeric_date(joined, today):
    iso = re.search(r"\b(\d{4})/(\d{1,2})/(\d{1,2})\b|\b(\d{4}) (\d{1,2}) (\d{1,2})\b", joined)
    if iso:
        year, month, day = (int(g) for g in (iso.groups()[:3] if iso.group(1) else iso.groups()[3:]))
        return _safe_date(year, month, day)
    us = re.search(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\b", joined)
    if us:
        month, day = int(us.group(1)), int(us.group(2))
        if us.group(3):
            year = int(us.group(3))
            return _safe_date(year + 2000 if year < 100 else year, month, day)
        return _past_month_day(month, day, today)
    return None


def _month_day(words, today):
    for idx, word in enumerate(words):
        month = _MONTHS.get(word.rstrip("."))
        if not month:
            continue
        # "October 15 [2025]" or "the 15th of October".
        after = _leading_number(words[idx + 1 :])
        if after:
            day, used = after
            rest = words[idx + 1 + used :]
            if rest and re.fullmatch(r"\d{4}", rest[0]):
                return _safe_date(int(rest[0]), month, day)
            return _past_month_day(month, day, today)
        before = _trailing_number(words[:idx], allow_of=True)
        if before:
            return _past_month_day(month, before, today)
    return None


def _leading_number(words):
    for used in (2, 1):
        value = _number(words[:used])
        if value is not None and 1 <= value <= 31:
            return value, used
    return None


def _trailing_number(words, allow_of=False):
    if allow_of and words and words[-1] == "of":
        words = words[:-1]
    for used in (2, 1):
        if len(words) >= used:
            value = _number(words[-used:])
            if value is not None and 1 <= value <= 31:
                return value
    return None


def _number(words):
    if not words:
        return None
    if len(words) == 1:
        word = words[0]
        if word.isdigit():
            return int(word)
        return _UNITS.get(word, _TENS.get(word))
    if len(words) == 2 and words[0] in _TENS and words[1] in _UNITS and _UNITS[words[1]] < 10:
        return _TENS[words[0]] + _UNITS[words[1]]
    return None


def _past_month_day(month, day, today):
    found = _safe_date(today.year, month, day)
    if found and found > today:
        found = _safe_date(today.year - 1, month, day)
    return found


def _safe_date(year, month, day):
    try:
        return date(year, month, day)
    except ValueError:
        return None
