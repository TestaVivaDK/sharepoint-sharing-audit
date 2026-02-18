"""Tests for CSV report generation."""

import csv
import io
from reporter.csv_export import generate_csv


def make_records():
    return [
        {"risk_level": "HIGH", "source": "OneDrive", "item_path": "/doc.xlsx",
         "item_web_url": "https://x.com/doc", "sharing_type": "Link-Anyone",
         "shared_with": "Anyone with the link", "shared_with_type": "Anonymous",
         "role": "Read", "created_date_time": "2025-01-01", "owner_email": "a@test.dk"},
        {"risk_level": "LOW", "source": "SharePoint", "item_path": "/report.pdf",
         "item_web_url": "https://x.com/report", "sharing_type": "User",
         "shared_with": "b@test.dk", "shared_with_type": "Internal",
         "role": "Write", "created_date_time": "2025-02-01", "owner_email": "a@test.dk"},
    ]


class TestGenerateCsv:
    def test_generates_correct_columns(self, tmp_path):
        path = tmp_path / "test.csv"
        generate_csv(make_records(), str(path))

        with open(path) as f:
            reader = csv.DictReader(f)
            rows = list(reader)

        assert len(rows) == 2
        assert rows[0]["RiskLevel"] == "HIGH"
        assert "ItemPath" in reader.fieldnames

    def test_sorted_by_risk(self, tmp_path):
        records = list(reversed(make_records()))  # LOW first
        path = tmp_path / "test.csv"
        generate_csv(records, str(path))

        with open(path) as f:
            reader = csv.DictReader(f)
            rows = list(reader)

        assert rows[0]["RiskLevel"] == "HIGH"
        assert rows[1]["RiskLevel"] == "LOW"
