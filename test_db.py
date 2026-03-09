import sqlite3
import json
from api_utils import fetch_codechef_data
print('Fetching:')
print(json.dumps(fetch_codechef_data('selva_vikash_p'), indent=2))
conn = sqlite3.connect('database.db')
cursor = conn.cursor()
cursor.execute("SELECT id, name, platform, recent_problems, total_contests, profile_url FROM user_profile WHERE platform='CodeChef'")
print('DB Rows:')
for r in cursor.fetchall():
    print(r)
conn.close()
