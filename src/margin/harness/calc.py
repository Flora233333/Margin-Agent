"""compute 与 date_calc 两个工具背后的确定性计算。

compute 的安全思路：绝不用 eval()。先把表达式解析成语法树（AST），
只允许数字、变量、加减乘除乘方、白名单函数这几种节点，再用 Decimal 逐节点求值。
这样模型写出 "__import__('os')" 之类的东西也只会得到一个错误，而不会被执行。
用 Decimal 而不是 float，是为了避免 0.1 + 0.2 = 0.30000000000000004 这类金融计算误差。
"""

from __future__ import annotations

import ast
import re
from datetime import date, timedelta
from decimal import Decimal, localcontext

from .answers import round_half_up

# 允许的函数及其参数个数范围 (最少, 最多)；None 表示不限。
FUNCTIONS: dict[str, tuple[int, int | None]] = {
    "max": (1, None),
    "min": (1, None),
    "abs": (1, 1),
    "round": (1, 2),
    "sum": (1, None),
    "avg": (1, None),
    "sqrt": (1, 1),
    "ln": (1, 1),
    "log10": (1, 1),
    "exp": (1, 1),
}

# 乘方指数和 exp 参数的上限，防止 10**100000 这种把 CPU 卡死的输入。
MAX_EXPONENT = Decimal(100)

_NUMBER = r"(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?"
# "10%" 这种紧跟在数字后面的百分号 -> "(10 / 100)"
_PERCENT_LITERAL = re.compile(rf"(?<![\w.])({_NUMBER})%")
_VARIABLE_VALUE = re.compile(rf"^[+-]?{_NUMBER}$")


# ---------------------------------------------------------------- compute


def normalize_expression(expression: str, variables: dict[str, str]) -> tuple[str, dict[str, str]]:
    """把模型写的表达式整理成 Python 语法，并把变量值转成纯数字字符串。

    例：normalize_expression("9.3 × 10%", {}) -> ("9.3 * (10 / 100)", {})
    """
    text = expression.replace("×", "*").replace("÷", "/").replace("％", "%").replace("^", "**")
    text = _PERCENT_LITERAL.sub(lambda m: f"({m.group(1)} / 100)", text)
    if "%" in text:
        # 剩下的 % 不在数字后面，只可能是取模，金融计算里禁止。
        raise ValueError("percent must be an immediate suffix on a numeric literal")

    values: dict[str, str] = {}
    for name, raw in variables.items():
        if not name.isidentifier():
            raise ValueError(f"invalid variable name: {name!r}")
        value = str(raw).strip().replace("％", "%")
        is_percent = value.endswith("%")
        number = value[:-1] if is_percent else value
        if not _VARIABLE_VALUE.fullmatch(number):
            raise ValueError(f"variable {name!r} must be a numeric string")
        decimal = Decimal(number) / 100 if is_percent else Decimal(number)
        values[name] = format(decimal, "f")
    return text, values


def evaluate(expression: str, variables: dict[str, str]) -> Decimal:
    """在白名单 AST 上用 Decimal 求值。"""
    tree = ast.parse(expression, mode="eval")

    def visit(node: ast.AST) -> Decimal:
        if isinstance(node, ast.Expression):
            return visit(node.body)
        if isinstance(node, ast.Constant) and type(node.value) in (int, float):
            return Decimal(str(node.value))
        if isinstance(node, ast.Name):
            if node.id not in variables:
                raise ValueError(f"unknown variable: {node.id}")
            return Decimal(variables[node.id])
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
            value = visit(node.operand)
            return -value if isinstance(node.op, ast.USub) else value
        if isinstance(node, ast.BinOp):
            left, right = visit(node.left), visit(node.right)
            if isinstance(node.op, ast.Add):
                return left + right
            if isinstance(node.op, ast.Sub):
                return left - right
            if isinstance(node.op, ast.Mult):
                return left * right
            if isinstance(node.op, ast.Div):
                if right == 0:
                    raise ZeroDivisionError("division by zero")
                return left / right
            if isinstance(node.op, ast.Pow):
                return _power(left, right)
        if isinstance(node, ast.Call):
            return _call(node, [visit(arg) for arg in node.args])
        raise ValueError(f"disallowed compute node: {type(node).__name__}")

    return visit(tree)


def _power(base: Decimal, exponent: Decimal) -> Decimal:
    if abs(exponent) > MAX_EXPONENT:
        raise ValueError("power exponent must be within [-100, 100]")
    if exponent == exponent.to_integral_value():
        if base == 0 and exponent < 0:
            raise ZeroDivisionError("zero cannot be raised to a negative power")
        return base ** int(exponent)
    # 分数次方（如年均复合增长率的 1/n 次方）用 exp(ln(x) * y) 计算，底数必须为正。
    if base <= 0:
        raise ValueError("fractional power requires a positive base")
    return (base.ln() * exponent).exp()


def _call(node: ast.Call, args: list[Decimal]) -> Decimal:
    if not isinstance(node.func, ast.Name) or node.func.id not in FUNCTIONS or node.keywords:
        raise ValueError("only positional calls to " + ", ".join(FUNCTIONS) + " are allowed")
    name = node.func.id
    low, high = FUNCTIONS[name]
    if len(args) < low or (high is not None and len(args) > high):
        raise ValueError(f"{name} takes {low}-{high or 'n'} arguments")
    if name == "max":
        return max(args)
    if name == "min":
        return min(args)
    if name == "sum":
        return sum(args, Decimal(0))
    if name == "avg":
        return sum(args, Decimal(0)) / len(args)
    if name == "abs":
        return abs(args[0])
    if name == "round":
        digits = int(args[1]) if len(args) > 1 else 0
        return round_half_up(args[0], digits)
    if name == "sqrt":
        if args[0] < 0:
            raise ValueError("sqrt requires a non-negative argument")
        return args[0].sqrt()
    if name in {"ln", "log10"}:
        if args[0] <= 0:
            raise ValueError(f"{name} requires a positive argument")
        return args[0].ln() if name == "ln" else args[0].log10()
    # exp
    if abs(args[0]) > MAX_EXPONENT:
        raise ValueError("exp argument must be within [-100, 100]")
    return args[0].exp()


def compute(expression: str, variables: dict[str, str], precision: int) -> dict[str, str]:
    """compute 工具的完整计算：规范化 -> 求值 -> 返回取整结果和不取整结果。

    返回 result_exact 是 RC6-C 的改进：多步计算时模型应该用不取整的中间值，
    避免先四舍五入再代入造成误差。
    """
    normalized, values = normalize_expression(expression, variables)
    value = evaluate(normalized, values)
    with localcontext() as context:
        context.prec = 16
        exact = +value  # 一元加号会按当前精度（16 位有效数字）重新舍入
    return {
        "expression_normalized": normalized,
        "variables_normalized": values,
        "result": format(round_half_up(value, precision), "f"),
        "result_exact": format(exact, "f"),
    }


# ---------------------------------------------------------------- date_calc


def parse_date(value: str) -> date:
    m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", value.strip()) or re.fullmatch(
        r"(\d{4})年(\d{1,2})月(\d{1,2})日", value.strip()
    )
    if not m:
        raise ValueError("base_date must be YYYY-MM-DD or YYYY年M月D日")
    return date(*(int(part) for part in m.groups()))


def _is_working_day(value: date) -> bool:
    # 只排除周末，不推断法定节假日（工具描述里也这样告诉模型）。
    return value.weekday() < 5


def _add_months(value: date, months: int) -> date:
    """加 N 个月；目标月没有这一天时取月末（1月31日 + 1个月 = 2月28/29日）。"""
    index = value.year * 12 + value.month - 1 + months
    year, month = index // 12, index % 12 + 1
    next_month = date(year + month // 12, month % 12 + 1, 1)
    last_day = (next_month - timedelta(days=1)).day
    return date(year, month, min(value.day, last_day))


def calculate_date(
    base_date: str,
    count_from: str,
    duration: int,
    unit: str,
    calendar: str = "natural",
    roll: str = "none",
) -> dict[str, str]:
    """按起算规则计算期限的到期日。

    count_from:
        next_day  起算日当天不算，第二天是第 1 天（法律文书里最常见的算法）
        same_day  起算日当天就是第 1 天
    calendar=working 时按工作日（只排除周末）逐天数。
    roll 用于“到期日遇周末顺延/提前”。
    """
    start = parse_date(base_date)
    if unit == "day" and calendar == "working":
        current = start if count_from == "same_day" else start + timedelta(days=1)
        remaining = duration
        result = current
        while remaining > 0:
            if _is_working_day(current):
                remaining -= 1
                result = current
            current += timedelta(days=1)
    else:
        # same_day 把起算日算作第 1 天，所以比 next_day 少加 1。
        count = max(0, duration - (0 if count_from == "next_day" else 1))
        if unit == "day":
            result = start + timedelta(days=count)
        elif unit == "month":
            result = _add_months(start, count)
        else:
            result = _add_months(start, count * 12)

    if roll == "next_working_day":
        while not _is_working_day(result):
            result += timedelta(days=1)
    elif roll == "previous_working_day":
        while not _is_working_day(result):
            result -= timedelta(days=1)
    return {
        "date": f"{result.year}年{result.month}月{result.day}日",
        "iso_date": result.isoformat(),
        "calendar": calendar,
        "calendar_scope": "weekend_only" if calendar == "working" or roll != "none" else "natural",
    }
