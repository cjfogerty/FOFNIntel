#!/usr/bin/env python3
"""
FOSS Swim School Dashboard Updater
Takes a CSV export from the FOSS extraction script and updates
the corresponding dashboard HTML file with fresh data.

Usage:
    python3 update_dashboard.py <csv_file> <html_file> [--location NAME]

The CSV must have columns:
    Day, Time, Class Level, Time Range, Spots Left, Total Capacity, Enrolled, Student:Teacher Ratio
"""

import csv
import json
import re
import sys
import os
from datetime import datetime
from collections import defaultdict

# === CATEGORY MAPPING ===
# Maps class level prefixes to BBSS program categories
CATEGORY_MAP = {
    "Backfloat Baby": "Backfloat Baby",
    "Little": "Littles",
    "Middle": "Middles",
    "Big": "Bigs",
    "10+": "10+",
    "Adult": "Adults and Privates",
    "Private Lesson": "Adults and Privates",
}

def get_category(class_level):
    """Determine the category for a given class level."""
    for prefix, category in CATEGORY_MAP.items():
        if class_level.startswith(prefix):
            return category
    return "Other"

def time_to_slot(time_str):
    """Convert '9:00 AM' -> '09:00' (24h format for sorting)."""
    time_str = time_str.strip()
    try:
        # Try parsing with leading zero
        for fmt in ["%I:%M %p", "%I:%M%p"]:
            try:
                t = datetime.strptime(time_str, fmt)
                return t.strftime("%H:%M")
            except ValueError:
                continue
    except Exception:
        pass
    return time_str

def load_csv(filepath):
    """Load CSV and return list of dicts with computed fields."""
    rows = []
    with open(filepath, 'r', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        for r in reader:
            # Parse numeric fields
            spots = r.get('Spots Left', '')
            cap = r.get('Total Capacity', '')
            enrolled = r.get('Enrolled', '')

            try:
                spots = int(spots) if spots != '' else 0
            except (ValueError, TypeError):
                spots = 0
            try:
                cap = int(cap) if cap != '' else 0
            except (ValueError, TypeError):
                cap = 0
            try:
                enrolled = int(enrolled) if enrolled != '' else 0
            except (ValueError, TypeError):
                enrolled = 0

            row = {
                "Day": r.get("Day", "").strip(),
                "Time": r.get("Time", "").strip(),
                "Class Level": r.get("Class Level", "").strip(),
                "Time Range": r.get("Time Range", "").strip(),
                "Spots Left": spots,
                "Total Capacity": cap,
                "Enrolled": enrolled,
                "Student:Teacher Ratio": r.get("Student:Teacher Ratio", "").strip(),
                "Category": get_category(r.get("Class Level", "").strip()),
                "TimeSlot": time_to_slot(r.get("Time", "").strip()),
            }
            rows.append(row)
    return rows

def compute_analytics(raw_data):
    """Compute all ANALYTICS summary structures from raw data."""

    # --- dailySummary ---
    daily = defaultdict(lambda: {"TotalCapacity": 0, "Enrolled": 0, "SpotsOpen": 0, "LessonsScheduled": 0})
    for r in raw_data:
        d = daily[r["Day"]]
        d["TotalCapacity"] += r["Total Capacity"]
        d["Enrolled"] += r["Enrolled"]
        d["SpotsOpen"] += r["Spots Left"]
        d["LessonsScheduled"] += 1

    day_order = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
    daily_summary = []
    for day in day_order:
        if day in daily:
            d = daily[day]
            util = round(d["Enrolled"] / d["TotalCapacity"] * 100, 1) if d["TotalCapacity"] > 0 else 0.0
            daily_summary.append({
                "Day": day,
                "TotalCapacity": d["TotalCapacity"],
                "Enrolled": d["Enrolled"],
                "SpotsOpen": d["SpotsOpen"],
                "LessonsScheduled": d["LessonsScheduled"],
                "Utilization": util,
            })

    # --- timeslotSummary ---
    timeslot = defaultdict(lambda: {"TotalCapacity": 0, "Enrolled": 0, "SpotsOpen": 0, "LessonsScheduled": 0})
    for r in raw_data:
        ts = timeslot[r["TimeSlot"]]
        ts["TotalCapacity"] += r["Total Capacity"]
        ts["Enrolled"] += r["Enrolled"]
        ts["SpotsOpen"] += r["Spots Left"]
        ts["LessonsScheduled"] += 1

    timeslot_summary = []
    for slot in sorted(timeslot.keys()):
        t = timeslot[slot]
        util = round(t["Enrolled"] / t["TotalCapacity"] * 100, 1) if t["TotalCapacity"] > 0 else 0.0
        timeslot_summary.append({
            "TimeSlot": slot,
            "TotalCapacity": t["TotalCapacity"],
            "Enrolled": t["Enrolled"],
            "SpotsOpen": t["SpotsOpen"],
            "LessonsScheduled": t["LessonsScheduled"],
            "Utilization": util,
        })

    # --- categorySummary ---
    category = defaultdict(lambda: {"TotalCapacity": 0, "Enrolled": 0, "SpotsOpen": 0, "LessonsScheduled": 0})
    for r in raw_data:
        c = category[r["Category"]]
        c["TotalCapacity"] += r["Total Capacity"]
        c["Enrolled"] += r["Enrolled"]
        c["SpotsOpen"] += r["Spots Left"]
        c["LessonsScheduled"] += 1

    category_summary = []
    for cat in sorted(category.keys()):
        c = category[cat]
        util = round(c["Enrolled"] / c["TotalCapacity"] * 100, 1) if c["TotalCapacity"] > 0 else 0.0
        category_summary.append({
            "Category": cat,
            "TotalCapacity": c["TotalCapacity"],
            "Enrolled": c["Enrolled"],
            "SpotsOpen": c["SpotsOpen"],
            "LessonsScheduled": c["LessonsScheduled"],
            "Utilization": util,
        })

    # --- levelSummary ---
    level = defaultdict(lambda: {"Category": "", "TotalCapacity": 0, "Enrolled": 0, "SpotsOpen": 0, "LessonsScheduled": 0})
    for r in raw_data:
        lv = level[r["Class Level"]]
        lv["Category"] = r["Category"]
        lv["TotalCapacity"] += r["Total Capacity"]
        lv["Enrolled"] += r["Enrolled"]
        lv["SpotsOpen"] += r["Spots Left"]
        lv["LessonsScheduled"] += 1

    level_summary = []
    for lv_name in sorted(level.keys()):
        lv = level[lv_name]
        util = round(lv["Enrolled"] / lv["TotalCapacity"] * 100, 1) if lv["TotalCapacity"] > 0 else 0.0
        level_summary.append({
            "Level": lv_name,
            "Category": lv["Category"],
            "TotalCapacity": lv["TotalCapacity"],
            "Enrolled": lv["Enrolled"],
            "SpotsOpen": lv["SpotsOpen"],
            "LessonsScheduled": lv["LessonsScheduled"],
            "Utilization": util,
        })

    # --- dailyLevelMatrix ---
    daily_level = defaultdict(lambda: {"Category": "", "TotalCapacity": 0, "Enrolled": 0, "SpotsOpen": 0, "LessonsScheduled": 0})
    for r in raw_data:
        key = (r["Day"], r["Class Level"])
        dl = daily_level[key]
        dl["Category"] = r["Category"]
        dl["TotalCapacity"] += r["Total Capacity"]
        dl["Enrolled"] += r["Enrolled"]
        dl["SpotsOpen"] += r["Spots Left"]
        dl["LessonsScheduled"] += 1

    daily_level_matrix = []
    for (day, lv_name) in sorted(daily_level.keys(), key=lambda x: (day_order.index(x[0]) if x[0] in day_order else 99, x[1])):
        dl = daily_level[(day, lv_name)]
        util = round(dl["Enrolled"] / dl["TotalCapacity"] * 100, 1) if dl["TotalCapacity"] > 0 else 0.0
        daily_level_matrix.append({
            "Day": day,
            "Level": lv_name,
            "Category": dl["Category"],
            "TotalCapacity": dl["TotalCapacity"],
            "Enrolled": dl["Enrolled"],
            "SpotsOpen": dl["SpotsOpen"],
            "LessonsScheduled": dl["LessonsScheduled"],
            "Utilization": util,
        })

    # --- timeslotDayLevelMatrix ---
    ts_day_level = defaultdict(lambda: {"Category": "", "TotalCapacity": 0, "Enrolled": 0, "SpotsOpen": 0, "LessonsScheduled": 0})
    for r in raw_data:
        key = (r["TimeSlot"], r["Day"], r["Class Level"])
        tdl = ts_day_level[key]
        tdl["Category"] = r["Category"]
        tdl["TotalCapacity"] += r["Total Capacity"]
        tdl["Enrolled"] += r["Enrolled"]
        tdl["SpotsOpen"] += r["Spots Left"]
        tdl["LessonsScheduled"] += 1

    ts_day_level_matrix = []
    for (ts, day, lv_name) in sorted(ts_day_level.keys(), key=lambda x: (x[0], day_order.index(x[1]) if x[1] in day_order else 99, x[2])):
        tdl = ts_day_level[(ts, day, lv_name)]
        util = round(tdl["Enrolled"] / tdl["TotalCapacity"] * 100, 1) if tdl["TotalCapacity"] > 0 else 0.0
        ts_day_level_matrix.append({
            "TimeSlot": ts,
            "Day": day,
            "Level": lv_name,
            "Category": tdl["Category"],
            "TotalCapacity": tdl["TotalCapacity"],
            "Enrolled": tdl["Enrolled"],
            "SpotsOpen": tdl["SpotsOpen"],
            "LessonsScheduled": tdl["LessonsScheduled"],
            "Utilization": util,
        })

    return {
        "dailySummary": daily_summary,
        "timeslotSummary": timeslot_summary,
        "categorySummary": category_summary,
        "levelSummary": level_summary,
        "dailyLevelMatrix": daily_level_matrix,
        "timeslotDayLevelMatrix": ts_day_level_matrix,
    }

def update_html(html_path, raw_data, analytics, location_name=None):
    """Replace ANALYTICS and RAW_DATA in the HTML file."""
    with open(html_path, 'r', encoding='utf-8') as f:
        content = f.read()

    # Update date in title
    today = datetime.now().strftime("%b %-d, %Y")
    content = re.sub(
        r'(<title>Foss Swim School [^<]*?)\([^)]*\)',
        lambda m: m.group(1) + f'({today})',
        content
    )

    # Replace ANALYTICS block (use lambda to avoid regex escape issues with \u in JSON)
    analytics_json = json.dumps(analytics, indent=8, ensure_ascii=False)
    content = re.sub(
        r'const ANALYTICS = \{.*?\};',
        lambda m: f'const ANALYTICS = {analytics_json};',
        content,
        flags=re.DOTALL
    )

    # Replace RAW_DATA block
    raw_json = json.dumps(raw_data, indent=8, ensure_ascii=False)
    content = re.sub(
        r'const RAW_DATA = \[.*?\];',
        lambda m: f'const RAW_DATA = {raw_json};',
        content,
        flags=re.DOTALL
    )

    with open(html_path, 'w', encoding='utf-8') as f:
        f.write(content)

    print(f"Updated {html_path}")
    print(f"  - {len(raw_data)} class slots in RAW_DATA")
    print(f"  - {len(analytics['dailySummary'])} days in dailySummary")
    print(f"  - {len(analytics['levelSummary'])} levels in levelSummary")
    print(f"  - {len(analytics['timeslotDayLevelMatrix'])} entries in full matrix")

def main():
    if len(sys.argv) < 3:
        print("Usage: python3 update_dashboard.py <csv_file> <html_file> [--location NAME]")
        sys.exit(1)

    csv_file = sys.argv[1]
    html_file = sys.argv[2]
    location = None
    if "--location" in sys.argv:
        idx = sys.argv.index("--location")
        if idx + 1 < len(sys.argv):
            location = sys.argv[idx + 1]

    if not os.path.exists(csv_file):
        print(f"ERROR: CSV file not found: {csv_file}")
        sys.exit(1)
    if not os.path.exists(html_file):
        print(f"ERROR: HTML file not found: {html_file}")
        sys.exit(1)

    print(f"Loading CSV: {csv_file}")
    raw_data = load_csv(csv_file)
    print(f"  Loaded {len(raw_data)} rows")

    print("Computing analytics...")
    analytics = compute_analytics(raw_data)

    print(f"Updating HTML: {html_file}")
    update_html(html_file, raw_data, analytics, location)

    print("Done!")

if __name__ == "__main__":
    main()
