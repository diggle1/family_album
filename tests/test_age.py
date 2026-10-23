from datetime import date

from app.services.clock import age_label


def test_age_labels():
    birth = date(2024, 7, 12)
    assert age_label(birth, date(2024, 7, 20)) == "不足1个月"
    assert age_label(birth, date(2024, 8, 11)) == "不足1个月"
    assert age_label(birth, date(2024, 8, 12)) == "1个月"
    assert age_label(birth, date(2025, 7, 12)) == "1岁"
    assert age_label(birth, date(2026, 9, 12)) == "2岁2个月"
    assert age_label(birth, date(2026, 10, 22)) == "2岁3个月"
    assert age_label(birth, date(2024, 6, 1)) == "不足1个月"
