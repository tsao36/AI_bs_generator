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

print('=== CURRENT CONTEXT ===')
cur.execute('SELECT CURRENT_ROLE(), CURRENT_WAREHOUSE(), CURRENT_DATABASE(), CURRENT_SCHEMA()')
print(cur.fetchone())

print('\n=== DATABASES (first 20) ===')
cur.execute('SHOW DATABASES')
rows = cur.fetchall()
for r in rows[:20]:
    print(r[1])  # name

print('\n=== TABLE/VIEW NAME KEYWORD SCAN (ASSIGN/OWNER/IPS) ===')
q = """
SELECT table_catalog, table_schema, table_name, table_type
FROM INFORMATION_SCHEMA.TABLES
WHERE UPPER(table_name) LIKE '%ASSIGN%'
   OR UPPER(table_name) LIKE '%OWNER%'
   OR UPPER(table_name) LIKE '%IPS%'
ORDER BY table_catalog, table_schema, table_name
LIMIT 200
"""
try:
    cur.execute(q)
    found = cur.fetchall()
    print('COUNT', len(found))
    for r in found[:80]:
        print('.'.join([str(r[0]), str(r[1]), str(r[2])]), '|', r[3])
except Exception as e:
    print('TABLE_SCAN_ERROR', e)

print('\n=== COLUMN NAME KEYWORD SCAN (API/URL/ENDPOINT/OWNER/ASSIGN) ===')
q2 = """
SELECT table_catalog, table_schema, table_name, column_name
FROM INFORMATION_SCHEMA.COLUMNS
WHERE UPPER(column_name) LIKE '%API%'
   OR UPPER(column_name) LIKE '%URL%'
   OR UPPER(column_name) LIKE '%ENDPOINT%'
   OR UPPER(column_name) LIKE '%OWNER%'
   OR UPPER(column_name) LIKE '%ASSIGN%'
ORDER BY table_catalog, table_schema, table_name, ordinal_position
LIMIT 300
"""
try:
    cur.execute(q2)
    cols = cur.fetchall()
    print('COUNT', len(cols))
    for r in cols[:120]:
        print('.'.join([str(r[0]), str(r[1]), str(r[2])]), '->', r[3])
except Exception as e:
    print('COLUMN_SCAN_ERROR', e)

conn.close()
