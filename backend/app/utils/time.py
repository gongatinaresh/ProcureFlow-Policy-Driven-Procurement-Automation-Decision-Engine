from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from app.config import get_settings


def business_timezone() -> ZoneInfo:
    return ZoneInfo(get_settings().business_timezone)


def normalize_datetime(value: datetime, *, naive_is_business: bool = True) -> datetime:
    if value.tzinfo is None:
        assumed_timezone = business_timezone() if naive_is_business else timezone.utc
        value = value.replace(tzinfo=assumed_timezone)
    return value.astimezone(timezone.utc)


def business_date(value: datetime) -> date:
    if value.tzinfo is None:
        value = value.replace(tzinfo=business_timezone())
    return value.astimezone(business_timezone()).date()


def business_today() -> date:
    return datetime.now(business_timezone()).date()
