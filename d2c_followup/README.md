# D2C Order Follow-up Automation System

A small internal app that reads an order file, finds orders that need attention, creates follow-up tasks
for employees, and keeps an audit trail so nothing is forgotten.

**Stack:** Python 3, Streamlit (UI), pandas, SQLite (persistence in `d2c.db`).

## Run with Anaconda

```
conda create -n d2c python=3.11 -y
conda activate d2c
cd <path to this folder>
pip install -r requirements.txt
streamlit run app.py
```
The app opens at http://localhost:8501. Then:
1. **Upload & data quality** tab -> click **Load bundled sample dataset** (or upload your own CSV/XLSX and click *Process*).
2. Explore **Dashboard**, **Tasks**, **Daily report**, **Audit trail**, **Rule settings**.

Optional logic test without the browser: `python test_logic.py`

## Files
| File | Purpose |
|---|---|
| `app.py` | Streamlit UI (6 tabs) |
| `validation.py` | Reads files, checks missing fields, duplicate IDs, invalid dates/values/phones |
| `rules.py` | The 5 rules, task creation (no duplicates), reassign / status update |
| `db.py` | SQLite schema, config, audit logging |
| `sample_orders.csv` | 121-row sample with deliberate exceptions and bad rows |
| `new_dataset_with_errors.csv` | Small file full of errors for the live-interview demo |

## How it works
1. **Import:** each row is validated. Rows with a missing mandatory field, duplicate order ID, invalid date,
   non-positive value, bad phone, unknown payment type, or illogical dates are listed under
   *Needs data correction* and are **not** run through the rules.
2. **Rule engine** (thresholds editable in *Rule settings*):
   | Rule | Condition | Priority |
   |---|---|---|
   | Unconfirmed | Unconfirmed for > 2h | Low (<=6h), Medium (>6h), High (>24h) |
   | Dispatch delay | Confirmed, not dispatched within 24h | Medium; High if > 2x limit |
   | Shipment delay | Dispatched, expected date passed | Medium; High if >= 3 days late |
   | Failed delivery | Status = Failed Delivery | High |
   | High-value delay | Value > ₹5,000, undelivered, expected date passed | High |
3. **Tasks:** every exception becomes a task with order/customer details, reason, priority, assigned employee
   (from the file), recommended action, deadline (as-of time + 4h High / 24h Medium / 48h Low) and status
   (Open / In Progress / Resolved).
4. **Manager view:** pick user *Manager* in the sidebar to reassign tasks and change status.
   Employees can update only their own tasks.
5. **Audit trail:** every upload, task creation, assignment, reassignment, status change and rule change is
   stored with order ID, action, timestamp and user.
6. **Persistence:** tasks, orders and the audit trail live in `d2c.db`, so a browser refresh or restart loses nothing.

## Preventing duplicate tasks on re-upload
`tasks` has `UNIQUE (order_id, rule_type)` and tasks are created with `INSERT OR IGNORE`. Uploading the same
file again creates **0** new tasks and leaves the manager's reassignments/status untouched. Orders are upserted
by `order_id`. If an order's condition clears (e.g. it becomes Delivered in a new upload), its task is
auto-resolved and logged.

## Assumptions
- The file has no confirmation timestamp, so **order_date is the start of the clock** for the 2h and 24h rules.
- Times are naive local time. The rule clock ("as of") defaults to now and can be set manually in the sidebar
  to demo overdue tasks or reproduce a result.
- A duplicate order ID keeps the first row; later repeats are flagged and ignored.
- "Overdue task" = task deadline passed and status not Resolved.

## Business impact (for the demo)
1. **Reduces manual work:** no scanning Excel or chasing WhatsApp; the rules run in seconds over every order and
   each person sees only their own prioritised list with a recommended action.
2. **Prevents orders being forgotten:** every exception becomes a persistent task with an owner and deadline;
   overdue and high-priority tasks surface on the dashboard and daily report; re-uploads never lose or duplicate tasks.
3. **Metrics for effectiveness:** % of orders with an exception; average time from exception to resolution;
   % of tasks resolved within deadline; number of overdue tasks; unconfirmed-to-confirmed time; dispatch delay
   rate; failed-delivery recovery rate; repeat of the same exception per order; data-error rate per upload.
4. **Time saved per day (estimate):** (orders checked manually per day x ~1-2 min per order) + (time spent asking
   colleagues for status ~ 1-2 min per exception) - (time spent in the app). Example: 150 orders x 1 min +
   25 exceptions x 2 min = 200 min/day manually vs. ~30 min of working the task list -> **~2.5-3 hours saved
   per day**. Validate by timing staff before/after for a week.
5. **Limitations:** one-way file import (no live order system or courier API); no confirmation timestamp so
   order time is a proxy; no automatic WhatsApp/email reminders; simple user selector instead of real login;
   rule priorities are fixed formulas; SQLite suits a single team, not heavy concurrent use.

## Interview cheat sheet
| Challenge | Where |
|---|---|
| Upload a file with errors | Tab 1; try `new_dataset_with_errors.csv`. Errors shown with row number and reason. Logic: `validation.validate()` |
| Change a rule (24h -> 12h) | Tab 6 *Dispatch deadline* -> Save (re-runs rules). In code: `config` table, used in `rules.evaluate_order()` |
| Prevent duplicate tasks | `UNIQUE(order_id, rule_type)` + `INSERT OR IGNORE` in `rules.run_rules()` / `db.py` |
| Reassign an overdue task | Set sidebar as-of to a later time so tasks go overdue, choose *Manager*, Tasks tab -> pick task -> change employee -> Save; see Audit trail |
