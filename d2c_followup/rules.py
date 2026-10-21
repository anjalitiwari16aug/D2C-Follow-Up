"""Automatic rule engine + task creation.

Each rule looks at ONE order and returns an exception (reason, priority,
recommended action) or None. Thresholds come from the config table so they can
be changed from the UI without touching code.

Assumption: the file has no "confirmation time" column, so order_date is used as
the start of the clock for the 2-hour (unconfirmed) and 24-hour (dispatch) rules.
"""
from datetime import datetime, timedelta

from db import log_audit, now_str

RULE_NAMES = {
    "UNCONFIRMED": "Unconfirmed order",
    "DISPATCH_DELAY": "Dispatch delay",
    "SHIPMENT_DELAY": "Shipment delay",
    "FAILED_DELIVERY": "Failed delivery",
    "HIGH_VALUE_DELAY": "High-value delayed order",
}


def _dt(text):
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            pass
    return None


def _hours(delta):
    return delta.total_seconds() / 3600


def evaluate_order(o, cfg, asof):
    """Return a list of exceptions (dicts with rule_type, reason, priority, action)."""
    found = []
    order_dt = _dt(o["order_date"])
    exp_date = _dt(o["expected_delivery_date"]).date()
    status = o["status"]
    value = float(o["order_value"])
    overdue_days = (asof.date() - exp_date).days  # >0 means expected date has passed

    # Rule 1: unconfirmed for more than N hours
    if status == "Unconfirmed":
        waited = _hours(asof - order_dt)
        if waited > cfg["unconfirmed_hours"]:
            prio = "High" if waited > 24 else "Medium" if waited > 6 else "Low"
            found.append({
                "rule_type": "UNCONFIRMED",
                "reason": f"Unconfirmed for {waited:.1f} hours (limit {cfg['unconfirmed_hours']:g}h)",
                "priority": prio,
                "action": "Call the customer to confirm the order; cancel it if unreachable after 2 attempts",
            })

    # Rule 2: confirmed but not dispatched within N hours
    if status == "Confirmed" and not o["dispatch_date"]:
        waited = _hours(asof - order_dt)
        if waited > cfg["dispatch_hours"]:
            prio = "High" if waited > 2 * cfg["dispatch_hours"] else "Medium"
            found.append({
                "rule_type": "DISPATCH_DELAY",
                "reason": f"Confirmed {waited:.1f} hours ago, not dispatched (limit {cfg['dispatch_hours']:g}h)",
                "priority": prio,
                "action": "Check stock/packing with the warehouse and hand the parcel to the courier today",
            })

    # Rule 3: dispatched, expected delivery date passed
    if status == "Dispatched" and overdue_days > 0:
        found.append({
            "rule_type": "SHIPMENT_DELAY",
            "reason": f"Dispatched but not delivered; expected {exp_date} ({overdue_days} day(s) late)",
            "priority": "High" if overdue_days >= 3 else "Medium",
            "action": "Ask the courier for tracking status and tell the customer the new delivery date",
        })

    # Rule 4: failed delivery attempt
    if status == "Failed Delivery":
        found.append({
            "rule_type": "FAILED_DELIVERY",
            "reason": "Courier reported a failed delivery attempt",
            "priority": "High",
            "action": "Call the customer now, confirm address/availability and request a re-attempt (or arrange return)",
        })

    # Rule 5: high-value order, undelivered past expected date
    if value > cfg["high_value_threshold"] and status != "Delivered" and overdue_days > 0:
        found.append({
            "rule_type": "HIGH_VALUE_DELAY",
            "reason": f"Order value Rs.{value:,.0f} (> Rs.{cfg['high_value_threshold']:,.0f}) undelivered; "
                      f"expected {exp_date} ({overdue_days} day(s) late)",
            "priority": "High",
            "action": "Escalate to manager; contact courier and customer today and track to delivery",
        })
    return found


def _deadline(priority, cfg, asof):
    hours = cfg[f"deadline_hours_{priority.lower()}"]
    return (asof + timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M")


def run_rules(conn, cfg, asof, user="System"):
    """Evaluate all stored orders and sync tasks.

    - New exception  -> INSERT OR IGNORE (UNIQUE(order_id, rule_type) stops duplicates)
    - Already exists -> left untouched (keeps manager's reassignment / status)
    - Condition no longer true (e.g. order now delivered) -> auto-resolved
    Returns a summary dict.
    """
    orders = conn.execute("SELECT * FROM orders").fetchall()
    triggered = set()
    created = 0
    skipped_existing = 0

    for o in orders:
        for exc in evaluate_order(o, cfg, asof):
            triggered.add((o["order_id"], exc["rule_type"]))
            cur = conn.execute(
                """INSERT OR IGNORE INTO tasks
                   (order_id, rule_type, customer_name, phone, order_value, order_date,
                    reason, priority, assigned_employee, action_required, deadline,
                    status, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (o["order_id"], exc["rule_type"], o["customer_name"], o["phone"],
                 o["order_value"], o["order_date"], exc["reason"], exc["priority"],
                 o["assigned_employee"], exc["action"], _deadline(exc["priority"], cfg, asof),
                 "Open", now_str(), now_str()),
            )
            if cur.rowcount == 1:
                created += 1
                log_audit(conn, o["order_id"],
                          f"Task created ({RULE_NAMES[exc['rule_type']]}, {exc['priority']}) "
                          f"and assigned to {o['assigned_employee']}", user)
            else:
                skipped_existing += 1

    auto_resolved = 0
    for t in conn.execute("SELECT task_id, order_id, rule_type FROM tasks WHERE status != 'Resolved'").fetchall():
        if (t["order_id"], t["rule_type"]) not in triggered:
            conn.execute("UPDATE tasks SET status='Resolved', updated_at=? WHERE task_id=?",
                         (now_str(), t["task_id"]))
            log_audit(conn, t["order_id"],
                      f"Status changed to Resolved ({RULE_NAMES[t['rule_type']]}: condition no longer true)",
                      user)
            auto_resolved += 1

    conn.commit()
    return {"created": created, "already_existed": skipped_existing, "auto_resolved": auto_resolved}


def update_task(conn, task_id, user, new_status=None, new_employee=None):
    """Change status and/or reassign a task, writing an audit row for each change."""
    t = conn.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
    if t is None:
        raise ValueError(f"Task {task_id} not found")
    changes = []
    if new_employee and new_employee != t["assigned_employee"]:
        conn.execute("UPDATE tasks SET assigned_employee=?, updated_at=? WHERE task_id=?",
                     (new_employee, now_str(), task_id))
        log_audit(conn, t["order_id"],
                  f"Reassigned {RULE_NAMES[t['rule_type']]} task from {t['assigned_employee']} to {new_employee}",
                  user)
        changes.append("reassigned")
    if new_status and new_status != t["status"]:
        conn.execute("UPDATE tasks SET status=?, updated_at=? WHERE task_id=?",
                     (new_status, now_str(), task_id))
        log_audit(conn, t["order_id"],
                  f"Status changed from {t['status']} to {new_status} ({RULE_NAMES[t['rule_type']]})", user)
        changes.append("status updated")
    conn.commit()
    return changes
