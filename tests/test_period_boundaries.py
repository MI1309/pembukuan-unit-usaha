import pytest
from datetime import date
from app.services.periodic_total_service import get_period_boundaries, get_period_for_date

def test_period_boundaries_feb_non_leap():
    # 2026-02 (non-leap): minggu 4 = 22-28
    periods = get_period_boundaries(2026, 2)
    week4 = periods[3]
    assert week4.start_date == date(2026, 2, 22)
    assert week4.end_date == date(2026, 2, 28)
    assert week4.kind == "week"

def test_period_boundaries_feb_leap():
    # 2028-02 (leap): minggu 4 = 22-29
    periods = get_period_boundaries(2028, 2)
    week4 = periods[3]
    assert week4.start_date == date(2028, 2, 22)
    assert week4.end_date == date(2028, 2, 29)

def test_period_boundaries_30_days():
    # 2026-04 (30 hari): minggu 4 = 22-30
    periods = get_period_boundaries(2026, 4)
    week4 = periods[3]
    assert week4.start_date == date(2026, 4, 22)
    assert week4.end_date == date(2026, 4, 30)

def test_period_boundaries_31_days():
    # 2026-12 (31 hari): minggu 4 = 22-31
    periods = get_period_boundaries(2026, 12)
    week4 = periods[3]
    assert week4.start_date == date(2026, 12, 22)
    assert week4.end_date == date(2026, 12, 31)

def test_get_period_for_date():
    # get_period_for_date(2026-02-15) -> minggu 3 (15-21)
    p = get_period_for_date(date(2026, 2, 15))
    assert p.start_date == date(2026, 2, 15)
    assert p.end_date == date(2026, 2, 21)
    
    # get_period_for_date(2026-02-28) -> minggu 4
    p = get_period_for_date(date(2026, 2, 28))
    assert p.start_date == date(2026, 2, 22)
    assert p.end_date == date(2026, 2, 28)
