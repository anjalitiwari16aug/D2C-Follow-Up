"""Headless checks of the core logic (no browser needed): python test_logic.py"""
import os
import tempfile
from datetime import datetime

import db
import rules
import validation

# Use a throw-away database so the real d2c.db is untouched.
db.DB_PATH = os.path.join(tempfile.mkdtemp(), "test.db")
db.init_db()
conn = db.get_conn()
cfg = db.get_config(conn)
ASOF = datetime(2026, 10, 21, 11, 30)

HERE = os.path.dirname(os.path.abspath(__file__))
df = validation.read_file(os.path.join(HERE, "sample_orders.csv"))
valid, issues = validation.validate(df, ASOF)
print(f"rows={len(df)} valid={len(valid)} issues={len(issues)}")
for i in issues:
    print("  row", i["row_number"], i["order_id"], "->", i["issues"])

for o in valid:
    conn.execute("INSERT OR REPLACE INTO orders VALUES (?,?,?,?,?,?,?,?,?,?,NULL)", tuple(o.values()))
conn.commit()

res = rules.run_rules(conn, cfg, ASOF)
print("first run:", res)
by_type = conn.execute("SELECT rule_type, COUNT(*) c FROM tasks GROUP BY rule_type").fetchall()
print({r["rule_type"]: r["c"] for r in by_type})
total = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]

res2 = rules.run_rules(conn, cfg, ASOF)
assert res2["created"] == 0, "re-run must not create duplicates"
assert conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == total
print("re-run created 0 duplicate tasks: OK")

# rule change: dispatch deadline 24h -> 12h
before = conn.execute("SELECT COUNT(*) FROM tasks WHERE rule_type='DISPATCH_DELAY'").fetchone()[0]
db.set_config(conn, "dispatch_hours", 12, "Manager")
cfg = db.get_config(conn)
res3 = rules.run_rules(conn, cfg, ASOF, "Manager")
after = conn.execute("SELECT COUNT(*) FROM tasks WHERE rule_type='DISPATCH_DELAY'").fetchone()[0]
print(f"dispatch delay tasks 24h={before} -> 12h={after}; run: {res3}")

# reassignment of an overdue task
t = conn.execute("SELECT task_id, order_id, assigned_employee FROM tasks WHERE status='Open' LIMIT 1").fetchone()
new_emp = "Employee A" if t["assigned_employee"] != "Employee A" else "Employee B"
print("reassign:", rules.update_task(conn, t["task_id"], "Manager", "In Progress", new_emp))
print([dict(r) for r in conn.execute(
    "SELECT order_id, action, user FROM audit_log WHERE order_id=? ORDER BY id DESC LIMIT 3", (t["order_id"],))])
print("ALL CHECKS PASSED")
