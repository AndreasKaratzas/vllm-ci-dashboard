"""Live assertions for bounded workload history and truthful partial windows."""
from datetime import datetime, timedelta, timezone


def _utc(value):
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    assert result.tzinfo is not None
    assert result.utcoffset() == timedelta(0)
    return result.astimezone(timezone.utc)


def assert_retention_coverage(data):
    generated = _utc(data["generated_at"])
    hour = generated.replace(minute=0, second=0, microsecond=0)
    day = generated.replace(hour=0, minute=0, second=0, microsecond=0)
    for collection, key, minimum, seconds in (
        ("hourly", "hour", 7, 3600), ("daily", "date", 90, 86400),
    ):
        days = data["retention"][collection + "_days"]
        assert type(days) is int and days >= minimum
        start = hour - timedelta(days=days) if key == "hour" else day - timedelta(days=days - 1)
        end = hour + timedelta(hours=1) if key == "hour" else day + timedelta(days=1)
        rows = data[collection]
        assert rows, "A current workload snapshot must include its open bucket"
        dates = [_utc(row[key] if key == "hour" else row[key] + "T00:00:00Z") for row in rows]
        assert dates == sorted(set(dates)), "Workload buckets must be unique and ordered"
        assert all(start <= date < end for date in dates)
        assert dates[-1] == (hour if key == "hour" else day)
        expected = int((end - start).total_seconds() / seconds)
        assert 0 < len(rows) <= expected
        coverage = data["coverage"][collection]
        assert coverage["retention_days"] == days
        assert _utc(coverage["expected_start"]) == start
        assert _utc(coverage["start"]) == dates[0]
        assert _utc(coverage["end_exclusive"]) == end
        assert coverage["observed_through"] == data["generated_at"]
        assert coverage["bucket_count"] == len(rows)
        assert coverage["expected_bucket_count"] == expected
        missing = expected - len(rows)
        assert coverage["missing_bucket_count"] == missing
        assert coverage["contiguous"] is (missing == 0)
        assert coverage["collection_complete"] is (
            missing == 0 and all(row["collection_complete"] is True for row in rows)
        )
        assert coverage["has_open_bucket"] is True


def assert_window_coverage(data):
    generated = _utc(data["generated_at"])
    window = data["window"]
    assert window["days"] == 14
    start = generated.date() - timedelta(days=window["days"] - 1)
    assert window["start_date"] == start.isoformat()
    assert window["end_date"] == generated.date().isoformat()
    assert _utc(window["start"]) == datetime.combine(start, datetime.min.time(), timezone.utc)
    assert window["observed_through"] == data["generated_at"]
    rows = [row for row in data["daily"] if window["start_date"] <= row["date"] <= window["end_date"]]
    dates = [row["date"] for row in rows]
    assert dates == sorted(set(dates))
    assert 0 < len(rows) <= window["days"]
    complete = len(rows) == window["days"] and all(row["collection_complete"] is True for row in rows)
    assert window["collection_complete"] is complete
    assert window["complete"] is complete
    assert window["lower_bound"] is (not complete)
