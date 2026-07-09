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
cur = conn.cursor()
for v in ['DIM_CORE_IPS_CASE_COMMENTS','DIM_IPS_BACKEND_SYSTEM','FACT_CASE','DIM_CASE_HISTORY']:
    fq = f"SALES_MARKETING.SALES_SUPPORT_PREMIER_ANALYSIS.{v}"
    cur.execute(f"SELECT GET_DDL('VIEW', '{fq}')")
    ddl = str(cur.fetchone()[0] or '')
    print(f"\\n=== {v} LEN={len(ddl)} ===")
    print('TAIL>>>')
    print(ddl[-600:])

conn.close()
