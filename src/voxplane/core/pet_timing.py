"""DICOM time references for classic PET decay correction."""
from datetime import date, datetime, time, timedelta, timezone, tzinfo
from typing import Any

from pydicom import FileDataset
from pydicom.valuerep import DA, DT, TM


def _parse_dicom_datetime(value: Any) -> datetime | None:
    text = str(value).strip() if value is not None else None
    if text is None:
        return None
    try:
        parsed = DT(text)
    except (TypeError, ValueError):
        return None
    return parsed if isinstance(parsed, datetime) else None


def _parse_dicom_date(value: Any) -> date | None:
    text = str(value).strip() if value is not None else None
    if text is None:
        return None
    try:
        parsed = DA(text)
    except (TypeError, ValueError):
        return None
    return parsed if isinstance(parsed, date) else None


def _parse_dicom_time(value: Any) -> time | None:
    text = str(value).strip() if value is not None else None
    if text is None:
        return None
    try:
        parsed = TM(text)
    except (TypeError, ValueError):
        return None
    return parsed if isinstance(parsed, time) else None


def _combine_datetime(date_value: Any, time_value: Any) -> datetime | None:
    parsed_date = _parse_dicom_date(date_value)
    parsed_time = _parse_dicom_time(time_value)
    if parsed_date is None or parsed_time is None:
        return None
    return datetime.combine(parsed_date, parsed_time)


def _with_dataset_offset(value: datetime | None, offset: tzinfo | None) -> datetime | None:
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=offset)


def _private_pet_datetime(dataset: FileDataset, group: int, creator: str, element: int):
    # Resolve the creator block even when a scanner relocates the private tag.
    try:
        value = dataset.private_block(group, creator)[element].value
    except KeyError:
        return None
    if isinstance(value, bytes):
        return value.decode("ascii").rstrip(" \0")
    return value


def pet_scan_start(
    dataset: FileDataset, offset: tzinfo | None, *, check_all_beds: bool,
) -> datetime | None:
    acquisition = _parse_dicom_datetime(dataset.get("AcquisitionDateTime"))
    if acquisition is None:
        acquisition = _combine_datetime(
            dataset.get("AcquisitionDate"), dataset.get("AcquisitionTime"),
        )
    acquisition = _with_dataset_offset(acquisition, offset)
    series = _with_dataset_offset(
        _combine_datetime(dataset.get("SeriesDate"), dataset.get("SeriesTime")), offset,
    )

    # QIBA SUV vendor-neutral pseudocode (2018). Acquisition can be the
    # start of this bed. A later SeriesTime can be a reconstruction time.
    candidates = set()
    for value in (
        dataset.get("DecayCorrectionDateTime"),
        _private_pet_datetime(dataset, 0x0009, "GEMS_PETD_01", 0x0D),
        _private_pet_datetime(dataset, 0x0071, "SIEMENS MED PT", 0x22),
    ):
        if value:
            reference = _with_dataset_offset(_parse_dicom_datetime(value), offset)
            if reference is None or (acquisition is not None and reference > acquisition):
                return None
            candidates.add(reference)
    series_type = dataset.get("SeriesType", ())
    first_type = series_type if isinstance(series_type, str) else next(iter(series_type), "")
    static = str(first_type).upper() == "STATIC"
    # Accept an earlier SeriesTime alone only when the scanner will check
    # this candidate against the other beds in the same series.
    if acquisition is not None and series is not None and series <= acquisition:
        if check_all_beds or static or series == acquisition or candidates:
            candidates.add(series)
    if candidates:
        return candidates.pop() if len(candidates) == 1 else None
    if static:
        return acquisition
    return None


def radiopharmaceutical_start_datetime(
    item: Any,
    acquisition: datetime,
    offset: tzinfo | None,
) -> datetime | None:
    parsed = _parse_dicom_datetime(
        getattr(item, "RadiopharmaceuticalStartDateTime", None)
    )
    if parsed is not None:
        return _with_dataset_offset(parsed, offset)

    parsed_time = _parse_dicom_time(
        getattr(item, "RadiopharmaceuticalStartTime", None)
    )
    if parsed_time is None:
        return None
    local_scan = acquisition.astimezone(offset) if offset is not None else acquisition
    parsed = datetime.combine(local_scan.date(), parsed_time, tzinfo=offset)
    if parsed > local_scan:
        parsed -= timedelta(days=1)
    return parsed


def dataset_utc_offset(dataset: FileDataset) -> tzinfo | None:
    value = dataset.get("TimezoneOffsetFromUTC")
    return datetime.strptime(str(value), "%z").tzinfo if value else None


def scan_start_key(start: datetime) -> str:
    if start.tzinfo is not None:
        start = start.astimezone(timezone.utc)
    return start.isoformat()


def pet_scan_start_candidate(dataset: FileDataset) -> str:
    """Return a comparable candidate for series-wide START validation."""
    try:
        start = pet_scan_start(dataset, dataset_utc_offset(dataset), check_all_beds=True)
        return scan_start_key(start) if start is not None else ""
    except (TypeError, ValueError, OverflowError):
        return ""
