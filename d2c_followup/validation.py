"""Input validation for uploaded order files (CSV / Excel).

Rows with problems are NOT sent to the rule engine; they are reported as
"needs data correction" instead (as the assignment requires).
"""
import json
import re
from datetime import datetime

import pandas as pd

REQUIRED_COLUMNS = [
    "order_id", "order_date", "customer_name", "phone", "order_value",
    "payment_type", "status", "dispatch_date", "expected_delivery_date",
    "assigned_employee",
]
# Columns that must be filled for every row.
ALWAYS_MANDATORY = [
    "order_id", "order_date", "customer_name", "phone", "order_value",
    "payment_type", "status", "expected_delivery_date", "assigned_employee",
]
VALID_STATUSES = {
    "unconfirmed": "Unconfirmed",
    "confirmed": "Confirmed",
    "dispatched": "Dispatched",
    "delivered": "Delivered",
    "failed delivery": "Failed Delivery",
    "failed": "Failed Delivery",
}
# Statuses where a dispatch date must exist.
NEEDS_DISPATCH_DATE = {"Dispatched", "Delivered", "Failed Delivery"}
VALID_PAYMENT = {"prepaid": "Prepaid", "cod": "COD"}

DATE_FORMATS = ["%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d"]


def parse_dt(value):
    """Return a datetime or None if the text is not a valid date."""
    text = str(value).strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def read_file(uploaded):
    """Read a CSV/Excel upload into a DataFrame of strings. Raises ValueError on bad files."""
    name = getattr(uploaded, "name", str(uploaded)).lower()
    try:
        if name.endswith(".csv"):
            df = pd.read_csv(uploaded, dtype=str, keep_default_na=False)
        elif name.endswith((".xlsx", ".xls")):
            df = pd.read_excel(uploaded, dtype=str).fillna("")
        else:
            raise ValueError("Unsupported file type. Please upload a .csv or .xlsx file.")
    except ValueError:
        raise
    except Exception as exc:  # corrupt file, wrong encoding, empty file...
        raise ValueError(f"Could not read the file: {exc}")

    df.columns = [str(c).strip().lower().replace(" ", "_") for c in df.columns]
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError("File is missing required column(s): " + ", ".join(missing))
    if df.empty:
        raise ValueError("The file has no data rows.")
    return df[REQUIRED_COLUMNS]


def validate(df, asof):
    """Validate every row. Returns (valid_orders, issues).

    valid_orders: list of dicts ready to store (dates normalised as text)
    issues: list of dicts {row_number, order_id, issues, raw_row}
    """
    valid, issues = [], []
    first_seen = {}  # order_id -> (row_number, raw dict)

    for idx, row in df.iterrows():
        row_no = idx + 2  # +1 for header, +1 for 1-based
        raw = {c: str(row[c]).strip() for c in REQUIRED_COLUMNS}
        problems = []

        # 1. mandatory fields
        for col in ALWAYS_MANDATORY:
            if raw[col] == "":
                problems.append(f"Missing {col}")

        # 2. duplicate order IDs (first occurrence kept, repeats flagged)
        oid = raw["order_id"]
        if oid:
            if oid in first_seen:
                first_row, first_raw = first_seen[oid]
                if first_raw == raw:
                    problems.append(f"Duplicate order ID (exact repeat of row {first_row})")
                else:
                    problems.append(f"Duplicate order ID with different data (first seen at row {first_row})")
            else:
                first_seen[oid] = (row_no, raw)

        # 3. dates
        order_dt = disp_dt = exp_dt = None
        if raw["order_date"]:
            order_dt = parse_dt(raw["order_date"])
            if order_dt is None:
                problems.append(f"Invalid order_date '{raw['order_date']}'")
        if raw["expected_delivery_date"]:
            exp_dt = parse_dt(raw["expected_delivery_date"])
            if exp_dt is None:
                problems.append(f"Invalid expected_delivery_date '{raw['expected_delivery_date']}'")

        status = VALID_STATUSES.get(raw["status"].lower()) if raw["status"] else None
        if raw["status"] and status is None:
            problems.append(f"Unknown status '{raw['status']}'")

        if raw["dispatch_date"]:
            disp_dt = parse_dt(raw["dispatch_date"])
            if disp_dt is None:
                problems.append(f"Invalid dispatch_date '{raw['dispatch_date']}'")
        elif status in NEEDS_DISPATCH_DATE:
            problems.append(f"Missing dispatch_date for status {status}")

        # logical date checks
        if order_dt and exp_dt and exp_dt.date() < order_dt.date():
            problems.append("Expected delivery date is before the order date")
        if order_dt and disp_dt and disp_dt < order_dt:
            problems.append("Dispatch date is before the order date")
        if disp_dt and disp_dt > asof:
            problems.append("Dispatch date is in the future")
        if status in ("Unconfirmed", "Confirmed") and disp_dt:
            problems.append(f"Dispatch date present but status is {status}")

        # 4. value, phone, payment type
        value = None
        if raw["order_value"]:
            try:
                value = float(raw["order_value"])
                if value <= 0:
                    problems.append(f"Order value must be positive (got {raw['order_value']})")
            except ValueError:
                problems.append(f"Order value is not a number '{raw['order_value']}'")
        if raw["phone"]:
            digits = re.sub(r"\D", "", raw["phone"])
            if len(digits) == 12 and digits.startswith("91"):
                digits = digits[2:]
            if len(digits) != 10:
                problems.append(f"Invalid phone number '{raw['phone']}'")
            raw["phone"] = digits
        payment = VALID_PAYMENT.get(raw["payment_type"].lower()) if raw["payment_type"] else None
        if raw["payment_type"] and payment is None:
            problems.append(f"Invalid payment type '{raw['payment_type']}' (use Prepaid or COD)")

        if problems:
            issues.append({
                "row_number": row_no,
                "order_id": oid or "(blank)",
                "issues": "; ".join(problems),
                "raw_row": json.dumps(raw),
            })
            continue

        valid.append({
            "order_id": oid,
            "order_date": order_dt.strftime("%Y-%m-%d %H:%M"),
            "customer_name": raw["customer_name"],
            "phone": raw["phone"],
            "order_value": value,
            "payment_type": payment,
            "status": status,
            "dispatch_date": disp_dt.strftime("%Y-%m-%d %H:%M") if disp_dt else "",
            "expected_delivery_date": exp_dt.strftime("%Y-%m-%d"),
            "assigned_employee": raw["assigned_employee"],
        })
    return valid, issues
