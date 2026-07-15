import datetime as dt
import decimal
import os
from typing import Dict, Iterable, Iterator, List, Optional


def require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def mysql_connection(*, quick: bool = False):
    try:
        import pymysql
        from pymysql.cursors import DictCursor, SSDictCursor
    except ImportError as exc:
        raise RuntimeError("Python dependency PyMySQL is missing. Run: pip install -r requirements.txt") from exc

    cursorclass = SSDictCursor if quick else DictCursor
    return pymysql.connect(
        host=require_env("MYSQL_HOST"),
        port=int(require_env("MYSQL_PORT")),
        user=require_env("MYSQL_USER"),
        password=require_env("MYSQL_PASSWORD"),
        database=require_env("MYSQL_DATABASE"),
        charset="utf8mb4",
        connect_timeout=int(os.environ.get("MYSQL_CONNECT_TIMEOUT", "20")),
        read_timeout=int(os.environ.get("MYSQL_READ_TIMEOUT", "3600")),
        write_timeout=int(os.environ.get("MYSQL_WRITE_TIMEOUT", "3600")),
        autocommit=True,
        cursorclass=cursorclass,
    )


def mysql_label() -> str:
    host = os.environ.get("MYSQL_HOST", "<host>")
    port = os.environ.get("MYSQL_PORT", "<port>")
    database = os.environ.get("MYSQL_DATABASE", "<database>")
    return f"{host}:{port}/{database}"


def sql_literal(value: object) -> str:
    if value is None:
        return "NULL"
    text = str(value)
    return "'" + text.replace("\\", "\\\\").replace("'", "''") + "'"


def split_mysql_statements(sql: str) -> List[str]:
    statements: List[str] = []
    current: List[str] = []
    quote: Optional[str] = None
    index = 0

    while index < len(sql):
        char = sql[index]
        current.append(char)

        if quote:
            if char == "\\" and quote != "`" and index + 1 < len(sql):
                index += 1
                current.append(sql[index])
            elif char == quote:
                quote = None
        elif char in ("'", '"', "`"):
            quote = char
        elif char == ";":
            statement = "".join(current[:-1]).strip()
            if statement:
                statements.append(statement)
            current = []

        index += 1

    statement = "".join(current).strip()
    if statement:
        statements.append(statement)
    return statements


def mysql_value_to_string(value: object) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, dt.datetime):
        return value.isoformat(sep=" ")
    if isinstance(value, (dt.date, dt.time)):
        return value.isoformat()
    if isinstance(value, decimal.Decimal):
        return str(value)
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return str(value)


def stringify_row(row: Dict[str, object]) -> Dict[str, str]:
    return {key: mysql_value_to_string(value) for key, value in row.items()}


def iter_mysql_dicts(sql: str, *, quick: bool = False) -> Iterator[Dict[str, str]]:
    statements = split_mysql_statements(sql)
    if not statements:
        return

    with mysql_connection(quick=quick) as connection:
        for statement in statements[:-1]:
            with connection.cursor() as cursor:
                cursor.execute(statement)
                cursor.fetchall()

        with connection.cursor() as cursor:
            cursor.execute(statements[-1])
            if cursor.description is None:
                return
            for row in cursor:
                yield stringify_row(row)


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
