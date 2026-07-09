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

print('=== VIEW DEFINITIONS WITH API/REST/HSDES SIGNALS (PREMIER schemas) ===')
q = """
SELECT table_schema, table_name
FROM INFORMATION_SCHEMA.VIEWS
WHERE table_schema IN ('SALES_SUPPORT_PREMIER_ANALYSIS','SALES_SUPPORT_PREMIER_ANALYSIS_BASE')
ORDER BY table_schema, table_name
"""
cur.execute(q)
views = cur.fetchall()
print('VIEW_COUNT', len(views))

keywords = ['HSDES','API','ENDPOINT','REST','SERVICE','SALESFORCE','CASE_OWNER','ASSIGN']
for sch, name in views:
    fq = f"SALES_MARKETING.{sch}.{name}"
    try:
        cur.execute(f"SELECT GET_DDL('VIEW', '{fq}')")
        ddl = str(cur.fetchone()[0] or '')
    except Exception:
        continue
    hits = [k for k in keywords if k.lower() in ddl.lower()]
    if hits:
        print(f"{sch}.{name} | HITS={','.join(hits)}")

conn.close()
