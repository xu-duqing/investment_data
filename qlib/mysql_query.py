import argparse
import csv
import sys

from mysql_utils import fetch_mysql_dicts


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a MySQL query through the project's PyMySQL client.")
    parser.add_argument("-e", "--execute", required=True, help="SQL to execute")
    parser.add_argument("--skip-column-names", action="store_true", help="Do not print the header row")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        rows = fetch_mysql_dicts(args.execute)
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1

    writer = None
    for index, row in enumerate(rows):
        if writer is None:
            fieldnames = list(row.keys())
            writer = csv.DictWriter(sys.stdout, fieldnames=fieldnames, delimiter="\t", lineterminator="\n")
            if not args.skip_column_names:
                writer.writeheader()
        writer.writerow(row)

    if not rows and not args.skip_column_names:
        sys.stdout.write("")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
