"""建账号或改密码（没有注册页，账号由管理员用这个脚本建，D30）。

用法：
    conda run -n margin --no-capture-output python scripts/create_user.py <用户名>
    例：给开发用户 dev 设密码（旧题都归它）：... scripts/create_user.py dev

用户名不存在就新建，存在就换密码；换密码后这个用户已有的登录全部失效（要重新登录）。
数据库地址读配置 MARGIN_DATABASE_URL（.env）。

密码交互输入、输两遍，不回显，不出现在命令行参数和 shell 历史里。
标准输入不是终端时（例如实验脚本用管道传入），按行读两行，同样不经过命令行参数。
"""

from __future__ import annotations

import argparse
import getpass
import sys

from margin.auth import check_new_account, set_password
from margin.db import get_engine


def read_password() -> tuple[str, str]:
    if sys.stdin.isatty():
        return getpass.getpass("密码："), getpass.getpass("再输一遍：")
    return sys.stdin.readline().rstrip("\n"), sys.stdin.readline().rstrip("\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="建账号或改密码")
    parser.add_argument("username")
    args = parser.parse_args()

    password, again = read_password()
    if password != again:
        sys.exit("两次输入的密码不一样，没有修改")
    problem = check_new_account(args.username, password)
    if problem:
        sys.exit(problem)
    user_id, created = set_password(get_engine(), args.username, password)
    print(f"{'已建账号' if created else '已改密码'}：{args.username}（id={user_id}）")


if __name__ == "__main__":
    main()
