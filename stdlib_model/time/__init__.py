# the model of time: the clock and the local timezone are magic, the rest of what the models use is written in
# plain Python. ctime() draws its text and gmtime() the seconds since the epoch when it isn't given them;
# struct_time is a copy that stands in for it in a run; strftime() formats a time for the directives of glibc's C
# locale modelled. The rest of the module isn't modelled

import time
from operator import index as _index

from PyMagic import RunningUnstatedModel, constraint, magic, model

from ..errno import EOVERFLOW

TIME_T_MIN, TIME_T_MAX = -2**63, 2**63 - 1
INT_MIN, INT_MAX = -2**31, 2**31 - 1

DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
MONTH_NAMES = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
               "November", "December"]

FIELDS = ["tm_year", "tm_mon", "tm_mday", "tm_hour", "tm_min", "tm_sec", "tm_wday", "tm_yday", "tm_isdst"]
HIDDEN_FIELDS = ["tm_zone", "tm_gmtoff"]


@model(time.struct_time)
class struct_time(tuple):
    """The time value as returned by gmtime(), localtime(), and strptime(), and
 accepted by asctime(), mktime() and strftime().  May be considered as a
 sequence of 9 integers.

 Note that several fields' values are not the same as those defined by
 the C language standard for struct tm.  For example, the value of the
 field tm_year is the actual year, not year - 1900.  See individual
 fields' descriptions for details."""

    n_sequence_fields = 9
    n_fields = 11
    n_unnamed_fields = 0
    __match_args__ = tuple(FIELDS)

    def __new__(cls, sequence, dict=None):
        # the first 9 items are the tuple; tm_zone and tm_gmtoff come after them or from dict, or are None
        try:
            items = list(sequence)
        except TypeError:
            raise TypeError("constructor requires a sequence") from None
        if dict is not None and not isinstance(dict, type({})):
            raise TypeError("time.struct_time() takes a dict as second arg, if any")
        if len(items) < len(FIELDS):
            raise TypeError(f"time.struct_time() takes an at least 9-sequence ({len(items)}-sequence given)")
        if len(items) > len(FIELDS) + len(HIDDEN_FIELDS):
            raise TypeError(f"time.struct_time() takes an at most 11-sequence ({len(items)}-sequence given)")
        made = tuple.__new__(cls, items[:len(FIELDS)])
        for position, name in enumerate(HIDDEN_FIELDS, len(FIELDS)):
            value = items[position] if position < len(items) else (dict or {}).get(name)
            vars(made)[name] = value  # under the read-only properties of their names
        return made

    def __setattr__(self, name, value):
        if name in FIELDS + HIDDEN_FIELDS:
            raise AttributeError("readonly attribute")
        raise AttributeError(f"'time.struct_time' object has no attribute '{name}'")

    def __repr__(self):
        return "time.struct_time(%s)" % ", ".join(f"{name}={value!r}" for name, value in zip(FIELDS, self))

    def __reduce__(self):
        return struct_time, (tuple(self), {name: vars(self)[name] for name in HIDDEN_FIELDS})

    tm_year = property(lambda self: self[0], doc="year, for example, 1993")
    tm_mon = property(lambda self: self[1], doc="month of year, range [1, 12]")
    tm_mday = property(lambda self: self[2], doc="day of month, range [1, 31]")
    tm_hour = property(lambda self: self[3], doc="hours, range [0, 23]")
    tm_min = property(lambda self: self[4], doc="minutes, range [0, 59]")
    tm_sec = property(lambda self: self[5], doc="seconds, range [0, 61])")
    tm_wday = property(lambda self: self[6], doc="day of week, range [0, 6], Monday is 0")
    tm_yday = property(lambda self: self[7], doc="day of year, range [1, 366]")
    tm_isdst = property(lambda self: self[8], doc="1 if summer time is in effect, 0 if not, and -1 if unknown")
    tm_zone = property(lambda self: vars(self)["tm_zone"], doc="abbreviation of timezone name")
    tm_gmtoff = property(lambda self: vars(self)["tm_gmtoff"], doc="offset from UTC in seconds")


def time_t(seconds):
    # seconds as the C time_t that time takes them: a float rounded down, and either refused out of its range
    if isinstance(seconds, float):
        if seconds != seconds:
            raise ValueError("Invalid value NaN (not a number)")
        if not TIME_T_MIN <= seconds < -float(TIME_T_MIN):
            raise OverflowError("timestamp out of range for platform time_t")
        whole = int(seconds)
        return whole - 1 if whole > seconds else whole
    whole = _index(seconds)
    if not TIME_T_MIN <= whole <= TIME_T_MAX:
        raise OverflowError("timestamp out of range for platform time_t")
    return whole

def days_from_civil(year, month, day):
    # the days from 1970-01-01 to a date of the proleptic Gregorian calendar
    year -= month <= 2
    era = year // 400
    year_of_era = year - era * 400
    day_of_year = (153 * (month + (-3 if month > 2 else 9)) + 2) // 5 + day - 1
    return era * 146097 + year_of_era * 365 + year_of_era // 4 - year_of_era // 100 + day_of_year - 719468

def civil_from_days(days):
    # the (year, month, day) of a number of days from 1970-01-01
    days += 719468
    era = days // 146097
    day_of_era = days - era * 146097
    year_of_era = (day_of_era - day_of_era // 1460 + day_of_era // 36524 - day_of_era // 146096) // 365
    day_of_year = day_of_era - (365 * year_of_era + year_of_era // 4 - year_of_era // 100)
    shifted_month = (5 * day_of_year + 2) // 153
    day = day_of_year - (153 * shifted_month + 2) // 5 + 1
    month = shifted_month + 3 if shifted_month < 10 else shifted_month - 9
    return year_of_era + era * 400 + (month <= 2), month, day

def utc(seconds):
    # the struct_time of seconds since the epoch in UTC, as glibc's gmtime() makes it
    days, rest = divmod(seconds, 86400)
    year, month, day = civil_from_days(days)
    if not INT_MIN <= year - 1900 <= INT_MAX:
        raise OSError(EOVERFLOW, "Value too large for defined data type")
    yday = days - days_from_civil(year, 1, 1) + 1
    clock = (rest // 3600, rest % 3600 // 60, rest % 60)
    return struct_time((year, month, day, *clock, (days + 3) % 7, yday, 0), {"tm_zone": "GMT", "tm_gmtoff": 0})

def is_ctime(text):
    # whether text has ctime's form: "Www Mmm dd hh:mm:ss yyyy", the day of the month padded with a space
    def number(part, low, high, width):
        return part.strip(" ").isascii() and part.strip(" ").isdigit() and low <= int(part) <= high and \
            part == f"{int(part):{width}d}"
    year = text[20:].removeprefix("-")
    return (len(text) > 20 and text[:3] in [name[:3] for name in DAY_NAMES] and text[4:7] in
            [name[:3] for name in MONTH_NAMES] and text[3] + text[10] + text[19] == "   " and
            number(text[7:10], 1, 31, 3) and text[13] + text[16] == "::" and number(text[11:13], 0, 23, "02") and
            number(text[14:16], 0, 59, "02") and number(text[17:19], 0, 61, "02") and
            year.isascii() and year.isdigit() and year == str(int(year)))

def c_int(value):
    value = _index(value)
    if value > INT_MAX:
        raise OverflowError("signed integer is greater than maximum")
    if value < INT_MIN:
        raise OverflowError("signed integer is less than minimum")
    return value

def broken_down(t):
    # the struct tm of a time tuple, checked as time's strftime() checks it: (fields, tm_zone, tm_gmtoff)
    if not isinstance(t, tuple):
        raise TypeError("Tuple or struct_time argument required")
    if len(t) != len(FIELDS):
        raise TypeError("strftime(): illegal time tuple argument")
    year, mon, mday, hour, minute, sec, wday, yday, isdst = map(c_int, t)
    fields = dict(year=year, mon=mon - 1, mday=mday, hour=hour, min=minute, sec=sec, wday=(wday + 1) % 7,
                  yday=yday - 1, isdst=isdst)
    zone, gmtoff = None, 0
    if type(t) in (struct_time, struct_time.__model__):
        zone, gmtoff = t.tm_zone, t.tm_gmtoff if t.tm_gmtoff is not None else 0
    if fields["mon"] == -1:
        fields["mon"] = 0
    elif not 0 <= fields["mon"] <= 11:
        raise ValueError("month out of range")
    if fields["mday"] == 0:
        fields["mday"] = 1
    elif not 0 <= fields["mday"] <= 31:
        raise ValueError("day of month out of range")
    for name, high, what in (("hour", 23, "hour"), ("min", 59, "minute"), ("sec", 61, "seconds")):
        if not 0 <= fields[name] <= high:
            raise ValueError(f"{what} out of range")
    if fields["wday"] < 0:
        raise ValueError("day of week out of range")
    if fields["yday"] == -1:
        fields["yday"] = 0
    elif not 0 <= fields["yday"] <= 365:
        raise ValueError("day of year out of range")
    return fields, zone, gmtoff

def directive(letter, tm, zone, gmtoff):
    # the text of one directive in glibc's C locale; the ones not modelled are refused
    day, month = DAY_NAMES[(tm["wday"] - 1) % 7], MONTH_NAMES[tm["mon"]]
    hour12 = tm["hour"] % 12 or 12
    texts = {
        "a": day[:3], "A": day, "b": month[:3], "h": month[:3], "B": month,
        "d": f"{tm['mday']:02d}", "e": f"{tm['mday']:2d}", "j": f"{tm['yday'] + 1:03d}", "m": f"{tm['mon'] + 1:02d}",
        "H": f"{tm['hour']:02d}", "I": f"{hour12:02d}", "M": f"{tm['min']:02d}", "S": f"{tm['sec']:02d}",
        "p": "AM" if tm["hour"] < 12 else "PM", "u": str(tm["wday"] or 7), "w": str(tm["wday"]),
        "y": f"{tm['year'] % 100:02d}", "Y": str(tm["year"]), "n": "\n", "t": "\t", "%": "%",
        "z": "%s%02d%02d" % ("-" if gmtoff < 0 else "+", abs(gmtoff) // 3600, abs(gmtoff) % 3600 // 60),
    }
    composed = {"c": "%a %b %e %H:%M:%S %Y", "D": "%m/%d/%y", "x": "%m/%d/%y", "T": "%H:%M:%S", "X": "%H:%M:%S",
                "R": "%H:%M", "r": "%I:%M:%S %p"}
    if letter in texts:
        return texts[letter]
    if letter in composed:
        return formatted(composed[letter], tm, zone, gmtoff)
    if letter == "F" and 0 <= tm["year"] <= 9999:
        return f"{tm['year']:04d}-{tm['mon'] + 1:02d}-{tm['mday']:02d}"
    if letter == "Z" and zone is not None:
        return zone
    raise RunningUnstatedModel(
        f"the time model doesn't model strftime()'s %{letter} for {tm}: it models only some directives of the C "
        f"locale, and %Z only for a struct_time with its timezone"
    )

def formatted(format, tm, zone, gmtoff):
    text, at = [], 0
    while at < len(format):
        if format[at] == "%" and at + 1 < len(format):
            text.append(directive(format[at + 1], tm, zone, gmtoff))
            at += 2
        else:
            text.append(format[at])
            at += 1
    return "".join(text)


@model(time)
class _model_time:
    # the copy of its struct_time
    struct_time = struct_time

    @model(time.ctime)
    def ctime(seconds=None, /):
        # the local time of seconds since the epoch, or of now, as text: the clock and the timezone are magic
        if seconds is not None:
            time_t(seconds)
        text = magic(str)
        constraint(is_ctime(text))
        return text

    @model(time.gmtime)
    def gmtime(seconds=None, /):
        # the UTC time of seconds since the epoch, or of now, whose seconds are magic
        if seconds is None:
            now = magic(int)
            constraint(TIME_T_MIN <= now <= TIME_T_MAX)
            seconds = now
        return utc(time_t(seconds))

    @model(time.strftime)
    def strftime(format, t=None, /):
        # t as format says; the local time, which strftime() formats when there's no t, isn't modelled
        if not isinstance(format, str):
            raise TypeError(f"strftime() argument 1 must be str, not {type(format).__name__}")
        if t is None:
            raise RunningUnstatedModel("the time model doesn't model strftime() of the local time; give it a time")
        tm, zone, gmtoff = broken_down(t)
        if "\0" in format:
            raise ValueError("embedded null character")
        return formatted(format, tm, zone, gmtoff)
