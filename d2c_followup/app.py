"""D2C Order Follow-up Automation System - Streamlit UI.

Run with:  streamlit run app.py
"""
import os
from datetime import datetime, date, time as dtime

import pandas as pd
import streamlit as st

import db
import rules
import validation

HERE = os.path.dirname(os.path.abspath(__file__))
SAMPLE_PATH = os.path.join(HERE, "sample_orders.csv")
PRIORITIES = ["High", "Medium", "Low"]
DEFAULT_EMPLOYEES = ["Employee A", "Employee B", "Employee C", "Employee D"]

st.set_page_config(page_title="D2C Order Follow-up", page_icon="📦", layout="wide")
db.init_db()


# ----------------------------------------------------------------- helpers
def load_tasks(conn):
    df = pd.read_sql_query("SELECT * FROM tasks", conn)
    if df.empty:
        return df
    df["rule_name"] = df["rule_type"].map(rules.RULE_NAMES)
    df["deadline_dt"] = pd.to_datetime(df["deadline"])
    df["overdue"] = (df["deadline_dt"] < pd.Timestamp(asof)) & (df["status"] != "Resolved")
    df["order_date_d"] = pd.to_datetime(df["order_date"]).dt.date
    return df


def employees(conn):
    rows = conn.execute("SELECT DISTINCT assigned_employee FROM orders").fetchall()
    names = sorted({r[0] for r in rows if r[0]} | set(DEFAULT_EMPLOYEES))
    return names


def process_upload(conn, df, filename, user):
    """Validate, store, run the rules. Returns (summary, issues)."""
    valid, issues = validation.validate(df, asof)
    cur = conn.execute(
        "INSERT INTO uploads (filename, uploaded_at, uploaded_by, total_rows, valid_rows, issue_rows) "
        "VALUES (?,?,?,?,?,?)",
        (filename, db.now_str(), user, len(df), len(valid), len(issues)),
    )
    upload_id = cur.lastrowid
    for o in valid:  # upsert: same order ID in a new file updates the order, never duplicates it
        conn.execute(
            "INSERT OR REPLACE INTO orders VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            tuple(o.values()) + (upload_id,),
        )
    for i in issues:
        conn.execute(
            "INSERT INTO data_issues (upload_id, row_number, order_id, issues, raw_row) VALUES (?,?,?,?,?)",
            (upload_id, i["row_number"], i["order_id"], i["issues"], i["raw_row"]),
        )
    db.log_audit(conn, "UPLOAD", f"Uploaded '{filename}': {len(df)} rows, {len(valid)} valid, "
                                  f"{len(issues)} need data correction", user)
    conn.commit()
    summary = rules.run_rules(conn, cfg_now(conn), asof, user)
    return summary, issues, len(valid)


def cfg_now(conn):
    return db.get_config(conn)


# ----------------------------------------------------------------- sidebar
conn = db.get_conn()
employee_list = employees(conn)

st.sidebar.title("📦 D2C Follow-up")
user = st.sidebar.selectbox("Logged-in user", ["Manager"] + employee_list,
                            help="Only the Manager can reassign tasks. Employees can update their own tasks.")
is_manager = user == "Manager"

st.sidebar.subheader("Rule clock")
use_now = st.sidebar.checkbox("Use current time as 'as of'", value=True)
if use_now:
    asof = datetime.now().replace(second=0, microsecond=0)
else:
    d = st.sidebar.date_input("As-of date", value=date.today())
    t = st.sidebar.time_input("As-of time", value=dtime(11, 0))
    asof = datetime.combine(d, t)
st.sidebar.caption(f"Rules evaluated as of **{asof:%d %b %Y %H:%M}**")

tasks_all = load_tasks(conn)



def filter_bar(df, prefix):
    """Render the 4 required filters (date, exception type, employee, priority) on the page
    and return the filtered tasks. `prefix` keeps widget keys unique per tab."""
    if df.empty:
        return df
    dmin, dmax = df["order_date_d"].min(), df["order_date_d"].max()
    c1, c2, c3, c4 = st.columns([1.3, 1.3, 1.2, 1])
    dates = c1.date_input("Order date range", value=(dmin, dmax), min_value=dmin, max_value=dmax,
                          key=f"{prefix}_dates")
    types = c2.multiselect("Exception type", list(rules.RULE_NAMES.values()), key=f"{prefix}_types",
                           placeholder="All types")
    emps = c3.multiselect("Employee", sorted(df["assigned_employee"].unique()), key=f"{prefix}_emps",
                          placeholder="All employees")
    prios = c4.multiselect("Priority", PRIORITIES, key=f"{prefix}_prios", placeholder="All")
    out = df
    if isinstance(dates, (tuple, list)) and len(dates) == 2:
        out = out[(out["order_date_d"] >= dates[0]) & (out["order_date_d"] <= dates[1])]
    if types:
        out = out[out["rule_name"].isin(types)]
    if emps:
        out = out[out["assigned_employee"].isin(emps)]
    if prios:
        out = out[out["priority"].isin(prios)]
    return out


tab_up, tab_dash, tab_tasks, tab_report, tab_audit, tab_rules = st.tabs(
    ["1. Upload & data quality", "2. Dashboard", "3. Tasks", "4. Daily report", "5. Audit trail", "6. Rule settings"]
)

# ------------------------------------------------------------ 1. Upload
with tab_up:
    st.header("Order data import")
    st.write("Upload a CSV or Excel file with: order_id, order_date, customer_name, phone, order_value, "
             "payment_type (Prepaid/COD), status, dispatch_date, expected_delivery_date, assigned_employee.")
    c1, c2 = st.columns([3, 1])
    with c1:
        uploaded = st.file_uploader("Choose a file", type=["csv", "xlsx", "xls"])
        if uploaded is not None and st.button("Process uploaded file", type="primary"):
            try:
                df = validation.read_file(uploaded)
                summary, issues, n_valid = process_upload(conn, df, uploaded.name, user)
                st.session_state["last_result"] = (uploaded.name, len(df), n_valid, issues, summary)
            except ValueError as exc:
                st.error(str(exc))
            except Exception as exc:  # never show a raw stack trace to the user
                st.error(f"Unexpected error while processing the file: {exc}")
    with c2:
        st.write("")
        st.write("")
        if st.button("Load bundled sample dataset"):
            try:
                df = validation.read_file(SAMPLE_PATH)
                summary, issues, n_valid = process_upload(conn, df, "sample_orders.csv", user)
                st.session_state["last_result"] = ("sample_orders.csv", len(df), n_valid, issues, summary)
            except Exception as exc:
                st.error(f"Could not load sample: {exc}")

    if "last_result" in st.session_state:
        name, n_rows, n_valid, issues, summary = st.session_state["last_result"]
        st.success(f"**{name}**: {n_rows} rows read, {n_valid} valid, {len(issues)} need data correction.")
        m1, m2, m3 = st.columns(3)
        m1.metric("New tasks created", summary["created"])
        m2.metric("Tasks already existing (not duplicated)", summary["already_existed"])
        m3.metric("Auto-resolved (condition cleared)", summary["auto_resolved"])
        if issues:
            st.subheader("⚠️ Needs data correction (not checked by the rules)")
            st.dataframe(pd.DataFrame(issues)[["row_number", "order_id", "issues"]],
                         width="stretch", hide_index=True)
            st.download_button("Download data-correction list (CSV)",
                               pd.DataFrame(issues)[["row_number", "order_id", "issues"]].to_csv(index=False),
                               "data_corrections.csv", "text/csv")
    else:
        last = conn.execute("SELECT * FROM uploads ORDER BY upload_id DESC LIMIT 1").fetchone()
        if last:
            st.info(f"Last upload: {last['filename']} at {last['uploaded_at']} "
                    f"({last['total_rows']} rows, {last['issue_rows']} need correction).")
            iss = pd.read_sql_query(
                "SELECT row_number, order_id, issues FROM data_issues WHERE upload_id=?", conn, params=(last["upload_id"],))
            if not iss.empty:
                st.dataframe(iss, width="stretch", hide_index=True)

    with st.expander("Danger zone: reset demo data"):
        if st.checkbox("I understand this deletes all orders, tasks and the audit trail"):
            if st.button("Reset everything"):
                db.reset_all(conn)
                st.session_state.pop("last_result", None)
                st.success("All data cleared. Reload the page.")

# ------------------------------------------------------------ 2. Dashboard
with tab_dash:
    st.header("Dashboard")
    last = conn.execute("SELECT * FROM uploads ORDER BY upload_id DESC LIMIT 1").fetchone()
    if last is None:
        st.info("Upload a file first (tab 1).")
    else:
        st.caption(f"Data from **{last['filename']}** uploaded {last['uploaded_at']} · rules evaluated as of "
                   f"{asof:%d %b %Y %H:%M}")
        tasks = filter_bar(tasks_all, "dash")
        filtering = len(tasks) != len(tasks_all)
        if filtering:
            st.info(f"Filters active: showing {len(tasks)} of {len(tasks_all)} tasks.")

        has = not tasks.empty
        unresolved = tasks[tasks["status"] != "Resolved"] if has else tasks
        n_open = int((tasks["status"] == "Open").sum()) if has else 0
        n_prog = int((tasks["status"] == "In Progress").sum()) if has else 0
        n_done = int((tasks["status"] == "Resolved").sum()) if has else 0
        n_overdue = int(tasks["overdue"].sum()) if has else 0
        n_attn = unresolved["order_id"].nunique() if has else 0
        n_high = int((unresolved["priority"] == "High").sum()) if has else 0
        n_fix = conn.execute("SELECT COUNT(*) FROM data_issues WHERE upload_id=?",
                             (last["upload_id"],)).fetchone()[0]

        # --- KPI cards (the six items the assignment asks for + extras)
        a1, a2, a3, a4 = st.columns(4)
        a1.metric("Total orders uploaded", last["total_rows"],
                  help=f"{last['valid_rows']} valid, {last['issue_rows']} need data correction (latest upload)")
        a2.metric("Orders requiring attention", n_attn,
                  help="Distinct orders with at least one unresolved exception (respects filters)")
        a3.metric("Open tasks", n_open, help="Status = Open (not started)")
        a4.metric("Overdue tasks", n_overdue, help="Deadline passed and status is not Resolved")
        b1, b2, b3, b4 = st.columns(4)
        b1.metric("In-progress tasks", n_prog)
        b2.metric("High-priority pending", n_high, help="High priority, not Resolved")
        b3.metric("Resolved tasks", n_done)
        b4.metric("Rows needing data correction", n_fix, help="Held out of the rule engine - fix in the source file")

        if not has:
            st.warning("No exceptions match the current filters.")
        else:
            st.divider()
            cA, cB = st.columns(2)
            with cA:
                st.subheader("Exceptions by category")
                cat = (tasks.groupby(["rule_name", "priority"]).size().unstack(fill_value=0)
                       .reindex(columns=PRIORITIES, fill_value=0))
                st.bar_chart(cat, color=["#d62728", "#f5a623", "#2e9e5b"], horizontal=True)
                st.caption("Stacked by priority: red = High, amber = Medium, green = Low")
            with cB:
                st.subheader("Employee-wise pending tasks")
                pend = tasks[tasks["status"] != "Resolved"]
                if pend.empty:
                    st.success("No pending tasks.")
                else:
                    emp = (pend.assign(open_=pend["status"] == "Open", prog_=pend["status"] == "In Progress",
                                       high_=pend["priority"] == "High")
                           .groupby("assigned_employee")
                           .agg(Open=("open_", "sum"), In_Progress=("prog_", "sum"), Overdue=("overdue", "sum"),
                                High_priority=("high_", "sum"), Total_pending=("task_id", "count"))
                           .rename(columns={"In_Progress": "In progress", "High_priority": "High priority",
                                            "Total_pending": "Total pending"})
                           .sort_values("Total pending", ascending=False))
                    st.bar_chart(emp[["Open", "In progress"]], color=["#4c78a8", "#f5a623"])
                    st.dataframe(emp, width="stretch")

            st.divider()
            cols = ["task_id", "order_id", "customer_name", "phone", "order_value", "rule_name", "reason",
                    "priority", "assigned_employee", "action_required", "deadline", "status"]
            st.subheader(f"⏰ Overdue tasks ({n_overdue})")
            od = tasks[tasks["overdue"]].sort_values("deadline")
            if od.empty:
                st.success("No overdue tasks right now. (Tasks become overdue once their deadline passes - "
                           "move the 'as of' time in the sidebar forward to see this.)")
            else:
                st.dataframe(od[cols], width="stretch", hide_index=True)

            st.subheader(f"🔴 High-priority cases requiring immediate action ({n_high})")
            hp = (unresolved[unresolved["priority"] == "High"]
                  .sort_values(["overdue", "deadline"], ascending=[False, True]))
            st.dataframe(hp[cols + ["overdue"]], width="stretch", hide_index=True)
            st.caption("Overdue cases first, then by nearest deadline. Update them in tab 3.")

# ------------------------------------------------------------ 3. Tasks
with tab_tasks:
    st.header("Task assignment")
    if tasks_all.empty:
        st.info("No tasks yet.")
    else:
        tasks = filter_bar(tasks_all, "tasks")
        show = tasks.sort_values(["status", "deadline"])
        st.dataframe(
            show[["task_id", "order_id", "customer_name", "phone", "order_value", "rule_name", "reason",
                  "priority", "assigned_employee", "action_required", "deadline", "status", "overdue"]],
            width="stretch", hide_index=True)

        st.subheader("Update a task")
        ids = list(show["task_id"])
        if not ids:
            st.info("No tasks match the filters.")
        else:
            tid = st.selectbox("Task ID", ids, format_func=lambda i: (
                f"#{i} - {show[show.task_id == i].iloc[0]['order_id']} - "
                f"{show[show.task_id == i].iloc[0]['rule_name']}"))
            row = show[show.task_id == tid].iloc[0]
            st.write(f"**{row['order_id']}** · {row['customer_name']} · {row['phone']} · "
                     f"₹{row['order_value']:,.0f} — {row['reason']}")
            st.write(f"**Action:** {row['action_required']}  |  **Deadline:** {row['deadline']}"
                     + ("  |  🔴 **OVERDUE**" if row["overdue"] else ""))
            c1, c2, c3 = st.columns([2, 2, 1])
            new_status = c1.selectbox("Status", db.TASK_STATUSES,
                                      index=db.TASK_STATUSES.index(row["status"]))
            if is_manager:
                new_emp = c2.selectbox("Assigned employee", employee_list,
                                       index=employee_list.index(row["assigned_employee"])
                                       if row["assigned_employee"] in employee_list else 0)
            else:
                new_emp = row["assigned_employee"]
                c2.text_input("Assigned employee", value=row["assigned_employee"], disabled=True,
                              help="Only the Manager can reassign")
            c3.write("")
            c3.write("")
            if c3.button("Save", type="primary"):
                if not is_manager and row["assigned_employee"] != user:
                    st.error("You can only update tasks assigned to you (switch user to Manager).")
                else:
                    changes = rules.update_task(conn, int(tid), user, new_status, new_emp)
                    st.success("Saved: " + ", ".join(changes) if changes else "No changes to save.")
                    st.rerun()

# ------------------------------------------------------------ 4. Report
with tab_report:
    st.header("Daily exception report")
    if tasks_all.empty:
        st.info("No tasks yet.")
    else:
        t = tasks_all
        cutoff = pd.Timestamp(asof) - pd.Timedelta(hours=24)
        t = t.assign(created_dt=pd.to_datetime(t["created_at"]))
        unresolved = t[t["status"] != "Resolved"]
        sections = {
            "1. New exceptions (last 24h)": t[t["created_dt"] >= cutoff],
            "2. Unresolved exceptions": unresolved,
            "3. Overdue tasks": unresolved[unresolved["overdue"]],
            "4. High-priority cases": unresolved[unresolved["priority"] == "High"],
        }
        cols = ["order_id", "customer_name", "phone", "order_value", "rule_name", "reason", "priority",
                "assigned_employee", "action_required", "deadline", "status"]
        frames = []
        for name, d in sections.items():
            x = d[cols].copy()
            x.insert(0, "section", name)
            frames.append(x)
            st.subheader(f"{name} - {len(d)}")
            st.dataframe(d[cols], width="stretch", hide_index=True)
        pend = unresolved.groupby("assigned_employee").agg(
            pending=("task_id", "count"),
            overdue=("overdue", "sum"),
            high=("priority", lambda s: (s == "High").sum())).reset_index()
        st.subheader("5. Employee-wise pending actions")
        st.dataframe(pend, width="stretch", hide_index=True)
        for emp, d in unresolved.groupby("assigned_employee"):
            x = d[cols].copy()
            x.insert(0, "section", f"5. Pending actions - {emp}")
            frames.append(x)
        report = pd.concat(frames, ignore_index=True)
        st.download_button("⬇️ Download daily report (CSV)", report.to_csv(index=False),
                           f"daily_exception_report_{asof:%Y%m%d}.csv", "text/csv", type="primary")

# ------------------------------------------------------------ 5. Audit
with tab_audit:
    st.header("Audit trail")
    audit = pd.read_sql_query("SELECT id, order_id, action, timestamp, user FROM audit_log ORDER BY id DESC", conn)
    q = st.text_input("Filter by order ID (e.g. ORD0001)")
    if q:
        audit = audit[audit["order_id"].str.contains(q.strip(), case=False)]
    st.dataframe(audit, width="stretch", hide_index=True)
    st.download_button("Download audit trail (CSV)", audit.to_csv(index=False), "audit_trail.csv", "text/csv")

# ------------------------------------------------------------ 6. Rule settings
with tab_rules:
    st.header("Rule settings")
    st.caption("Change a business rule here - no code change needed. Saving re-runs all rules.")
    cfg = db.get_config(conn)
    c1, c2, c3 = st.columns(3)
    unconf = c1.number_input("Unconfirmed after (hours)", min_value=0.5, value=cfg["unconfirmed_hours"], step=0.5)
    disp = c2.number_input("Dispatch deadline (hours)", min_value=1.0, value=cfg["dispatch_hours"], step=1.0)
    hv = c3.number_input("High-value threshold (₹)", min_value=1.0, value=cfg["high_value_threshold"], step=500.0)
    d1, d2, d3 = st.columns(3)
    dh = d1.number_input("Task deadline - High (hours)", min_value=1.0, value=cfg["deadline_hours_high"])
    dm = d2.number_input("Task deadline - Medium (hours)", min_value=1.0, value=cfg["deadline_hours_medium"])
    dl = d3.number_input("Task deadline - Low (hours)", min_value=1.0, value=cfg["deadline_hours_low"])
    if st.button("Save settings and re-run rules", type="primary"):
        new = {"unconfirmed_hours": unconf, "dispatch_hours": disp, "high_value_threshold": hv,
               "deadline_hours_high": dh, "deadline_hours_medium": dm, "deadline_hours_low": dl}
        for k, v in new.items():
            if float(cfg[k]) != float(v):
                db.set_config(conn, k, v, user)
        conn.commit()
        s = rules.run_rules(conn, db.get_config(conn), asof, user)
        st.success(f"Rules re-run: {s['created']} new tasks, {s['auto_resolved']} auto-resolved, "
                   f"{s['already_existed']} unchanged.")
    st.markdown("""
**Rules implemented**
1. **Unconfirmed** - status Unconfirmed for more than the limit (default 2h) since order time
2. **Dispatch delay** - status Confirmed, no dispatch within the limit (default 24h) since order time
3. **Shipment delay** - status Dispatched and expected delivery date has passed
4. **Failed delivery** - status Failed Delivery (always High priority)
5. **High-value delay** - value above the threshold (default ₹5,000), not delivered, expected date passed

*No confirmation timestamp exists in the file, so order time is the clock start for rules 1 and 2.*
""")

conn.close()
