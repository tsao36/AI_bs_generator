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

print('=== IPS-RELATED OBJECTS IN SALES_SUPPORT_PREMIER_ANALYSIS ===')
cur.execute("""
SELECT table_schema, table_name, table_type
FROM INFORMATION_SCHEMA.TABLES
WHERE table_schema = 'SALES_SUPPORT_PREMIER_ANALYSIS'
  AND (
    UPPER(table_name) LIKE '%IPS%'
    OR UPPER(table_name) LIKE '%CASE%'
    OR UPPER(table_name) LIKE '%OWNER%'
    OR UPPER(table_name) LIKE '%ASSIGN%'
  )
ORDER BY table_name
""")
rows = cur.fetchall()
print('COUNT', len(rows))
for r in rows:
    print('.'.join([r[0], r[1]]), '|', r[2])

print('\n=== GET_DDL FOR KEY IPS VIEWS (first 8k chars each) ===')
key_views = [
    'DIM_IPS_CASE',
    'DIM_CORE_IPS_CASE_COMMENTS',
    'DIM_IPS_BACKEND_SYSTEM',
]
for v in key_views:
    fq = f"SALES_MARKETING.SALES_SUPPORT_PREMIER_ANALYSIS.{v}"
    print(f"\\n--- {fq} ---")
    try:
        cur.execute(f"SELECT GET_DDL('VIEW', '{fq}')")
        ddl = cur.fetchone()[0]
        text = str(ddl or '')
        print(text[:8000])
    except Exception as e:
        print('DDL_ERROR', e)

conn.close()
