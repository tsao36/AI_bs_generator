import os, sys, argparse, psycopg2
from dotenv import load_dotenv
load_dotenv()


def _split_table(table: str, env_schema: str) -> tuple[str, str]:
    if "." in table:
        schema, name = table.rsplit(".", 1)
        return schema or env_schema, name or ""
    return env_schema, table


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect columns of the table used by bug_category_mapper.")
    parser.add_argument("--table", default=os.getenv("TABLE") or "ips_jira_bugs", help="Table name (optionally schema.table)")
    args = parser.parse_args()

    env_schema = os.getenv("DB_SCHEMA") or "public"
    schema, table = _split_table(args.table, env_schema)
    try:
        conn = psycopg2.connect(
            host=os.getenv("DB_HOST"),
            port=os.getenv("DB_PORT") or 5433,
            dbname=os.getenv("DB_NAME"),
            user=os.getenv("DB_USER"),
            password=os.getenv("DB_PASS"),
        )
    except Exception as exc:
        print(f"Connection failed: {exc}", file=sys.stderr)
        return 1

    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = %s AND table_name = %s
                ORDER BY column_name;
                """,
                (schema, table),
            )
            rows = cur.fetchall()
            if not rows:
                print(f"No rows returned; check schema/table name (schema={schema}, table={table}).")
            else:
                print(f"Columns in {schema}.{table}:")
                for (col,) in rows:
                    print(f" - {col}")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())