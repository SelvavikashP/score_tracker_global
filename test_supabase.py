import psycopg2
import os
from dotenv import load_dotenv

load_dotenv()

uri = os.environ.get('DATABASE_URL')
print(f"Testing URI: {uri[:15]}...")

try:
    conn = psycopg2.connect(uri)
    cur = conn.cursor()
    cur.execute("SELECT 1;")
    result = cur.fetchone()
    print(f"Connection successful! Result: {result}")
    conn.close()
except Exception as e:
    print(f"Connection failed: {e}")
