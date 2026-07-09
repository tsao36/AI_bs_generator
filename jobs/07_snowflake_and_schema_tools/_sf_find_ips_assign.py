import re
from dotenv import dotenv_values
import snowflake.connector

env = dotenv_values('.env')
conn = snowflake.connector.connect(
    user=env.get('SNOWFLAKE_IPS_USER'),
    password=env.get('SNOWFLAKE_IPS_PASSWORD'),
    account=env.get('SNOWFLAKE_IPS_ACCOUNT'),
    warehouse=env.get('SNOWFLAKE_IPS_WAREHOUSE'),
    role=env.get('SNOWFLAKE_IPS_ROLE'),
    database=env.get('SNOWFLAKE_IPS_DATABASE'),
)
patterns = ['assign','reassign','owner','case_owner','ips','ticket','incident','routing','transfer']
rx = re.compile('|'.join(patterns), re.I)
queries = [
    ('PROCEDURES', 'SHOW PROCEDURES IN ACCOUNT'),
    ('FUNCTIONS', 'SHOW USER FUNCTIONS IN ACCOUNT'),
    ('EXTERNAL_FUNCTIONS', 'SHOW EXTERNAL FUNCTIONS IN ACCOUNT'),
    ('TASKS', 'SHOW TASKS IN ACCOUNT'),
]
cur = conn.cursor()
for label, q in queries:
    print(f"\\n=== {label} ===")
    try:
        cur.execute(q)
        rows = cur.fetchall()
        cols = [d[0] for d in cur.description]
        name_idx = cols.index('name') if 'name' in cols else None
        schema_idx = cols.index('schema_name') if 'schema_name' in cols else None
        db_idx = cols.index('database_name') if 'database_name' in cols else None
        matched = []
        for r in rows:
            parts = []
            if db_idx is not None: parts.append(str(r[db_idx] or ''))
            if schema_idx is not None: parts.append(str(r[schema_idx] or ''))
            if name_idx is not None: parts.append(str(r[name_idx] or ''))
            full = '.'.join([p for p in parts if p])
            if rx.search(full):
                matched.append(full)
        print('MATCH_COUNT', len(matched))
        for m in sorted(set(matched))[:200]:
            print(m)
    except Exception as e:
        print('QUERY_ERROR', q, e)

conn.close()
