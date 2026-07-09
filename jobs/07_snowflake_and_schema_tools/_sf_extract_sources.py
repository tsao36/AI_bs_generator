from dotenv import dotenv_values
import snowflake.connector
import re

env = dotenv_values('.env')
conn = snowflake.connector.connect(
    user=env.get('SNOWFLAKE_IPS_USER'),
    password=env.get('SNOWFLAKE_IPS_PASSWORD'),
    account=env.get('SNOWFLAKE_IPS_ACCOUNT'),
    warehouse=env.get('SNOWFLAKE_IPS_WAREHOUSE'),
    role=env.get('SNOWFLAKE_IPS_ROLE'),
    database=env.get('SNOWFLAKE_IPS_DATABASE'),
)
cur = conn.cursor()

views = [
    'DIM_IPS_CASE',
    'DIM_CORE_IPS_CASE_COMMENTS',
    'DIM_IPS_BACKEND_SYSTEM',
    'FACT_CASE',
    'DIM_CASE_HISTORY',
]

for v in views:
    fq = f"SALES_MARKETING.SALES_SUPPORT_PREMIER_ANALYSIS.{v}"
    print(f"\\n=== {fq} ===")
    try:
        cur.execute(f"SELECT GET_DDL('VIEW', '{fq}')")
        ddl = str(cur.fetchone()[0] or '')
        # find source objects after FROM / JOIN
        sources = set(re.findall(r"(?:FROM|JOIN)\\s+([A-Za-z0-9_\.\"]+)", ddl, flags=re.I))
        print('SOURCE_COUNT', len(sources))
        for s in sorted(sources):
            print('SOURCE', s)
        # quick signal words
        for k in ['http', 'api', 'endpoint', 'external function', 'procedure', 'call ', 'rest']:
            if k.lower() in ddl.lower():
                print('HAS_KEYWORD', k)
    except Exception as e:
        print('ERROR', e)

conn.close()
