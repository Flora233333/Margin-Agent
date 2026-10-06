"""compute / date_calc 的计算正确性与安全性。"""

import pytest

from margin.harness.calc import calculate_date, compute


@pytest.mark.parametrize(
    ("expression", "variables", "expected"),
    [
        ("9.3 × 10%", {}, "0.93"),  # 百分号是“除以 100”，不是取模
        ("(cur - prior) / prior × 100", {"cur": "120.5", "prior": "107.2"}, "12.41"),
        ("(250/100)**(1/3)-1", {}, "0.36"),  # 年均复合增长率：分数次方
        ("avg(1, 2, 4)", {}, "2.33"),
        ("max(100-35, 72)", {}, "72.00"),
        ("rate * 2", {"rate": "3.15%"}, "0.06"),  # 变量也可以带百分号
    ],
)
def test_compute_results(expression, variables, expected):
    assert compute(expression, variables, precision=2)["result"] == expected


def test_compute_returns_unrounded_exact_value():
    result = compute("1 / 3", {}, precision=2)
    assert result["result"] == "0.33"
    assert result["result_exact"] == "0.3333333333333333"


@pytest.mark.parametrize(
    "expression",
    ["__import__('os').system('dir')", "open('x')", "10 % 3", "1 if 1 else 2", "[1, 2][0]"],
)
def test_compute_rejects_code(expression):
    with pytest.raises((ValueError, SyntaxError)):
        compute(expression, {}, precision=2)


def test_compute_division_by_zero():
    with pytest.raises(ZeroDivisionError):
        compute("1 / 0", {}, precision=2)


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        (("2024-03-01", "next_day", 10, "day"), "2024年3月11日"),  # 当天不算
        (("2024-03-01", "same_day", 10, "day"), "2024年3月10日"),  # 当天算第 1 天
        (("2024年1月31日", "next_day", 1, "month"), "2024年2月29日"),  # 月末取闰年 2 月最后一天
        # 按月不看 count_from：原来 same_day 也减 1，少算一整个月（run 66 得出 6 月 20 日）
        (("2026-01-20", "same_day", 6, "month"), "2026年7月20日"),
        (("2024-03-01", "next_day", 5, "day", "working"), "2024年3月8日"),  # 跳过周末
        (("2024-03-02", "next_day", 0, "day", "natural", "next_working_day"), "2024年3月4日"),
    ],
)
def test_date_calc(args, expected):
    assert calculate_date(*args)["date"] == expected
