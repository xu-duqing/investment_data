import csv
import os
import subprocess
from typing import Dict, Iterable, Iterator, List, Optional


def require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def mysql_env() -> Dict[str, str]:
    env = os.environ.copy()
    env["MYSQL_PWD"] = require_env("MYSQL_PASSWORD")
    return env


def mysql_cmd(*, quick: bool = False) -> List[str]:
    cmd = [
        "mysql",
        "-h",
        require_env("MYSQL_HOST"),
        "-P",
        require_env("MYSQL_PORT"),
        "-u",
        require_env("MYSQL_USER"),
        "-D",
        require_env("MYSQL_DATABASE"),
        "--batch",
        "--raw",
        "--default-character-set=utf8mb4",
        "--connect-timeout=20",
    ]
    if quick:
        cmd.append("--quick")
    return cmd


def mysql_label() -> str:
    return "configured MySQL source"


def sql_literal(value: object) -> str:
    if value is None:
        return "NULL"
    text = str(value)
    return "'" + text.replace("\\", "\\\\").replace("'", "''") + "'"


def iter_mysql_dicts(sql: str, *, quick: bool = False) -> Iterator[Dict[str, str]]:
    proc = subprocess.Popen(
        mysql_cmd(quick=quick),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=mysql_env(),
    )
    assert proc.stdin is not None
    assert proc.stdout is not None
    assert proc.stderr is not None
    proc.stdin.write(sql)
    proc.stdin.close()

    reader = csv.DictReader(proc.stdout, delimiter="\t")
    for row in reader:
        yield row

    stderr = proc.stderr.read()
    return_code = proc.wait()
    if return_code != 0:
        raise RuntimeError(stderr.strip() or f"mysql exited with status {return_code}")


def fetch_mysql_dicts(sql: str) -> List[Dict[str, str]]:
    return list(iter_mysql_dicts(sql))


def fetch_one_value(sql: str, column: Optional[str] = None) -> Optional[str]:
    rows = fetch_mysql_dicts(sql)
    if not rows:
        return None
    if column is None:
        return next(iter(rows[0].values()))
    return rows[0].get(column)


def write_csv_rows(path: str, fieldnames: Iterable[str], rows: Iterable[Dict[str, str]]) -> int:
    count = 0
    with open(path, "w", newline="", encoding="utf-8") as fp:
        writer = csv.DictWriter(fp, fieldnames=list(fieldnames))
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
            count += 1
    return count
