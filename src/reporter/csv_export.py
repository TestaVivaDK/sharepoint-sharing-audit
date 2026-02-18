"""CSV report generation from Neo4j sharing data."""

import csv

CSV_COLUMNS = [
    ("RiskLevel", "risk_level"),
    ("Source", "source"),
    ("ItemPath", "item_path"),
    ("ItemWebUrl", "item_web_url"),
    ("SharingType", "sharing_type"),
    ("SharedWith", "shared_with"),
    ("SharedWithType", "shared_with_type"),
    ("Role", "role"),
    ("CreatedDateTime", "created_date_time"),
]

RISK_ORDER = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}


def generate_csv(records: list[dict], output_path: str) -> str:
    """Generate a CSV report sorted by risk level. Returns the output path."""
    sorted_records = sorted(records, key=lambda r: RISK_ORDER.get(r.get("risk_level", "LOW"), 2))

    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=[col for col, _ in CSV_COLUMNS])
        writer.writeheader()
        for record in sorted_records:
            writer.writerow({col: record.get(key, "") for col, key in CSV_COLUMNS})

    return output_path
