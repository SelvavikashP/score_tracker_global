import pandas as pd
import os
from datetime import datetime

if os.environ.get('RENDER') or os.environ.get('VERCEL'):
    EXCEL_FILE = "/tmp/contest_data.xlsx"
else:
    _BASE = os.path.abspath(os.path.dirname(__file__))
    _USER_DATA = os.path.join(_BASE, 'User_data')
    os.makedirs(_USER_DATA, exist_ok=True)
    EXCEL_FILE = os.path.join(_USER_DATA, "contest_data.xlsx")

def update_excel(users):
    """
    Exports user data to Excel, grouped by platform and sorted by rating (rank) descending.
    """
    data = []
    for user in users:
        data.append({
            "Platform": user.platform,
            "Name": user.name,
            "Rating": user.rating,
            "Rank": user.rank,
            "Global/Last Contest Rank": user.global_rank,
            "Country Rank": user.country_rank,
            "Problems Solved": user.recent_problems,
            "Total Contests": user.total_contests,
            "Last Updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        })

    df = pd.DataFrame(data)

    if not df.empty:
        # Sort: platform alphabetically, then rating descending within each platform
        df = df.sort_values(by=["Platform", "Rating"], ascending=[True, False])
        # Add a per-platform rank column
        df.insert(0, "#Rank", df.groupby("Platform")["Rating"].rank(method="min", ascending=False).astype(int))

    try:
        with pd.ExcelWriter(EXCEL_FILE, engine="openpyxl") as writer:
            if not df.empty:
                # Write a combined "All" sheet FIRST so it's the default active sheet
                df.to_excel(writer, sheet_name="All Platforms", index=False)
                # Then write one sheet per platform
                for platform, group in df.groupby("Platform"):
                    group = group.drop(columns=["Platform"])
                    group.to_excel(writer, sheet_name=platform[:31], index=False)
            else:
                df.to_excel(writer, sheet_name="All Platforms", index=False)
    except Exception as e:
        print(f"Error updating Excel: {e}")

def get_excel_path():
    return os.path.abspath(EXCEL_FILE)
