from datetime import date, datetime, timezone

from dates import (
    date_alias,
    is_evidenced_date,
    is_full_date,
    is_rfc3339_instant,
    normalise_published,
    published_scalar,
)


def test_a_bare_date_is_already_right():
    assert normalise_published("2026-07-11") == "2026-07-11"


def test_an_evidenced_timestamp_keeps_its_time_and_offset():
    assert normalise_published("2026-07-11 00:00:00+00:00") == (
        "2026-07-11T00:00:00+00:00"
    )
    assert normalise_published("2026-07-20T00:00:00.000Z") == (
        "2026-07-20T00:00:00.000Z"
    )
    assert normalise_published("2020-01-01T12:30:45+09:00") == (
        "2020-01-01T12:30:45+09:00"
    )


def test_timestamp_is_not_timezone_converted():
    assert normalise_published("2026-07-11T23:30:00+09:00") == (
        "2026-07-11T23:30:00+09:00"
    )


def test_partial_precision_survives_untouched():
    """A source that evidences only a year gets a year. Padding it to 2026-01-01
    would state a day the source does not - the exact failure this helper exists to
    avoid. 8 records legitimately carry year-only and 2 month-only."""
    assert normalise_published("2026") == "2026"
    assert normalise_published("2026-07") == "2026-07"


def test_date_and_datetime_objects_come_back_as_strings():
    """A re-ingest reads frontmatter back through YAML, which hands back objects."""
    assert normalise_published(date(2026, 7, 11)) == "2026-07-11"
    assert (
        normalise_published(datetime(2026, 7, 11, 0, 0, tzinfo=timezone.utc))
        == "2026-07-11T00:00:00+00:00"
    )


def test_ytdlp_compact_dates_are_not_mistaken_for_a_year():
    """20260711 truncated to its first four characters would read as the year 2026 -
    a silent loss of the month and day."""
    assert normalise_published("20260711") == "2026-07-11"


def test_unpadded_components_come_back_canonical():
    assert normalise_published("2026-7-1") == "2026-07-01"


def test_empty_defers_to_the_caller():
    """ "" not today's date: the handler owns its own fallback, and a helper that
    invented one would date a record to the day it was processed."""
    assert normalise_published(None) == ""
    assert normalise_published("") == ""
    assert normalise_published("   ") == ""


def test_an_unreadable_value_is_preserved_not_discarded():
    """Losing the value would destroy the only evidence of what the source said."""
    assert normalise_published("circa 1972") == "circa 1972"
    assert normalise_published("11/07/2026") == "11/07/2026"


def test_invalid_full_date_does_not_degrade_to_lower_precision():
    assert normalise_published("2026-02-30") == "2026-02-30"
    assert not is_evidenced_date("2026-02-30")
    assert not is_evidenced_date("2026-13")


def test_every_temporal_scalar_is_quoted_as_a_yaml_string():
    assert published_scalar("2026-07-11") == '"2026-07-11"'
    assert published_scalar("2026-07-11T00:00:00+00:00") == (
        '"2026-07-11T00:00:00+00:00"'
    )


def test_a_year_is_quoted_because_a_bare_year_is_an_integer():
    """`date_published: 2026` parses as the number 2026, which is the same
    one-field-many-types problem this helper exists to end - the corpus held 7 of
    them. Below day precision the value is a string that says how much is known."""
    assert published_scalar("2026") == '"2026"'
    assert published_scalar("2026-07") == '"2026-07"'


def test_an_unreadable_value_is_quoted_rather_than_emitted_raw():
    assert published_scalar("circa 1972") == '"circa 1972"'


def test_no_value_writes_nothing_and_lets_the_caller_decide():
    assert published_scalar(None) == ""
    assert published_scalar("") == ""


def test_temporal_predicates_enforce_offset_and_precision():
    for value in (
        "1988",
        "2020-08",
        "2020-08-09",
        "2020-08-09T17:30:00Z",
        "2020-08-09T17:30:00+09:00",
        "2020-08-09T17:30:00-04:30",
    ):
        assert is_evidenced_date(value)
    assert is_rfc3339_instant("2020-08-09T17:30:00+09:00")
    assert not is_rfc3339_instant("2020-08-09T17:30:00")
    assert not is_rfc3339_instant("2020-08-09")
    assert is_full_date("2020-08-09")
    assert not is_full_date("2020-08")


def test_filename_alias_uses_calendar_precision_without_truncating_metadata():
    assert date_alias("1988") == "1988"
    assert date_alias("2020-08") == "2020-08"
    assert date_alias("2020-08-09") == "2020-08-09"
    assert date_alias("2020-08-09T17:30:00+09:00") == "2020-08-09"
