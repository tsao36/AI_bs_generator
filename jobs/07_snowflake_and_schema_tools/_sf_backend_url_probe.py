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

print('=== DISTINCT BACKEND SYSTEM NAMES (top 40) ===')
cur.execute("""
SELECT BAKEND_SYSTEM_NM, COUNT(*) AS CNT
FROM SALES_MARKETING.SALES_SUPPORT_PREMIER_ANALYSIS.DIM_IPS_BACKEND_SYSTEM
GROUP BY 1
ORDER BY CNT DESC
LIMIT 40
""")
for r in cur.fetchall():
    print(r[0], '|', r[1])

print('\n=== SAMPLE BACKEND URL FIELD VALUES (non-null, top 30 distinct) ===')
cur.execute("""
SELECT DISTINCT CORE_IPS_BACKED_ID_URL_TXT
FROM SALES_MARKETING.SALES_SUPPORT_PREMIER_ANALYSIS.FACT_CASE
WHERE CORE_IPS_BACKED_ID_URL_TXT IS NOT NULL
  AND TRIM(CORE_IPS_BACKED_ID_URL_TXT) <> ''
LIMIT 30
""")
rows = cur.fetchall()
for r in rows:
    print(r[0])

print('\n=== COUNT URL-LIKE VALUES ===')
cur.execute("""
SELECT COUNT(*)
FROM SALES_MARKETING.SALES_SUPPORT_PREMIER_ANALYSIS.FACT_CASE
WHERE LOWER(CORE_IPS_BACKED_ID_URL_TXT) LIKE 'http%'
""")
print(cur.fetchone()[0])

conn.close()
