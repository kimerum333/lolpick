"""PostgreSQL integration checks for the v7 design.
Requires TEST_DATABASE_URL pointing to a disposable PostgreSQL test database.
Never reads the project's .env or production DATABASE_URL.
Creates/drops only a newly generated lolpick_v7_check_<hex> schema.
Run explicitly on a disposable local test database.
"""
from __future__ import annotations
import hashlib
import os
import re
from pathlib import Path
from uuid import uuid4
import psycopg
from psycopg.rows import dict_row

url = os.environ.get("TEST_DATABASE_URL")
if not url:
    raise SystemExit("Set TEST_DATABASE_URL to a disposable test database. No connection attempted.")
url = url.replace("postgresql+psycopg://", "postgresql://", 1)
schema = "lolpick_v7_check_" + uuid4().hex[:16]
assert re.fullmatch(r"lolpick_v7_check_[0-9a-f]{16}", schema)
source = (Path(__file__).resolve().parents[1] / "app/db/schema.sql").read_text(encoding="utf-8")
bootstrap = re.sub(r"\blolpick\b", schema, source)
conn = psycopg.connect(url, autocommit=True, connect_timeout=10, row_factory=dict_row,
                       options="-c lock_timeout=2000 -c statement_timeout=5000")
created = False
passed = []

def q(statement, params=()):
    return conn.execute(statement.replace("lolpick.", schema + "."), params)

def key(label):
    return hashlib.sha256(("lolpick.points.v7:test:" + label).encode()).digest()

def post(member, season, reason, amount, label, match=None, related=None, side=None):
    return q("SELECT * FROM lolpick.post_points(%s,%s,%s::smallint,%s,%s,%s,%s,%s::smallint)",
             (member, season, reason, amount, key(label), match, related, side)).fetchone()

def balance(member):
    return q("SELECT current_points FROM lolpick.lolpick_members WHERE id=%s", (member,)).fetchone()["current_points"]

def reject(label, operation):
    try:
        with conn.transaction():
            operation()
    except psycopg.Error:
        passed.append(label)
        return
    raise AssertionError(label + ": operation unexpectedly succeeded")

def fixture(label, season, provider, a=None, b=None):
    return q("""INSERT INTO lolpick.lolpick_matches
      (public_code,season_id,api_source_id,external_match_id,round,team_a_id,team_b_id,
       starts_at,status,provider_status)
      VALUES (%s,%s,%s,%s,'test',%s,%s,
       CASE WHEN %s THEN now()+interval '1 day' ELSE NULL END,1,1) RETURNING id""",
       (label,season,provider,label,a,b,a is not None and b is not None)).fetchone()["id"]

try:
    conn.execute(bootstrap, prepare=False)
    created = True
    count = conn.execute("SELECT count(*) AS n FROM pg_catalog.pg_tables WHERE schemaname=%s", (schema,)).fetchone()["n"]
    assert count == 9
    passed.append("exactly nine business tables")
    provider = q("INSERT INTO lolpick.lolpick_api_sources(code) VALUES ('test') RETURNING id").fetchone()["id"]
    season = q("""INSERT INTO lolpick.lolpick_seasons
      (public_code,slug,year,name,status,initial_points,api_source_id,external_series_id)
      VALUES ('test-2026','test',2026,'Test',2,1000,%s,'series') RETURNING id""", (provider,)).fetchone()["id"]
    members = []
    for i in range(2):
        row = q("INSERT INTO lolpick.lolpick_members(auth_user_id,google_sub) VALUES (%s,%s) RETURNING id,type",
                (uuid4(), "test-google-" + str(i))).fetchone()
        assert row["type"] == 1
        members.append(row["id"])
        opened = post(row["id"], season, 1, 1000, "opening-" + str(i))
        assert opened["balance_after"] == balance(row["id"]) == 1000
    a = q("INSERT INTO lolpick.lolpick_teams(api_source_id,external_team_id,name) VALUES (%s,'A','Team A') RETURNING id",
          (provider,)).fetchone()["id"]
    b = q("INSERT INTO lolpick.lolpick_teams(api_source_id,external_team_id,name) VALUES (%s,'B','Team B') RETURNING id",
          (provider,)).fetchone()["id"]
    m1, m2 = members
    tbd = fixture("tbd", season, provider)
    reject("TBD/time-unknown fixture stores but cannot accept wager", lambda: post(m1,season,5,-100,"tbd",tbd,side=1))
    assert balance(m1) == 1000
    match = fixture("regular", season, provider, a, b)
    stake = post(m1,season,5,-100,"stake",match,side=1)
    assert stake["balance_after"] == balance(m1) == 900
    same = post(m1,season,5,-100,"stake",match,side=1)
    assert same["id"] == stake["id"] and balance(m1) == 900
    passed.append("same key and payload returns existing row without extra debit")
    reject("same key different payload rejects", lambda: post(m1,season,5,-200,"stake",match,side=1))
    reject("one wager per member and match", lambda: post(m1,season,5,-100,"stake-again",match,side=2))
    assert balance(m1) == 900
    wager2 = post(m2,season,5,-150,"stake-2",match,side=2)
    reject("funded team slots cannot change",
           lambda: q("UPDATE lolpick.lolpick_matches SET team_a_id=%s,team_b_id=%s WHERE id=%s", (b,a,match)))
    other = fixture("insufficient", season, provider,a,b)
    reject("insufficient balance rolls back", lambda: post(m1,season,5,-901,"too-much",other,side=1))
    assert balance(m1) == 900
    reject("missing selection rejects", lambda: post(m1,season,5,-10,"no-side",other))
    reject("season cannot finish with unsettled funded match",
           lambda: q("UPDATE lolpick.lolpick_seasons SET status=3 WHERE id=%s", (season,)))
    q("UPDATE lolpick.lolpick_matches SET status=3,result=1,result_recorded_at=now() WHERE id=%s", (match,))
    reject("another member cannot use original wager reference",
           lambda: post(m2,season,6,250,"wrong-owner",match,stake["id"]))
    reject("losing wager cannot receive payout",
           lambda: post(m2,season,6,250,"losing-payout",match,wager2["id"]))
    reject("payout cannot exceed funded pool",
           lambda: post(m1,season,6,251,"too-large-payout",match,stake["id"]))
    assert balance(m1) == 900
    with conn.transaction():
        payout = post(m1,season,6,250,"payout",match,stake["id"])
        pending = q("SELECT status,payout FROM lolpick.lolpick_wagers WHERE id=%s", (stake["id"],)).fetchone()
        assert pending["status"] == 1 and pending["payout"] is None
        reject("wrong settlement totals reject",
               lambda: q("""UPDATE lolpick.lolpick_matches SET settlement_status=2,settlement_at=now(),
                        settlement_pool=249,settlement_paid=250 WHERE id=%s""", (match,)))
        q("""UPDATE lolpick.lolpick_matches SET settlement_status=2,settlement_at=now(),
          settlement_pool=250,settlement_paid=250 WHERE id=%s""", (match,))
    winner = q("SELECT status,payout FROM lolpick.lolpick_wagers WHERE id=%s", (stake["id"],)).fetchone()
    loser = q("SELECT status,payout FROM lolpick.lolpick_wagers WHERE id=%s", (wager2["id"],)).fetchone()
    assert winner == {"status":2,"payout":250} and loser == {"status":3,"payout":0}
    assert balance(m1) == 1150 and balance(m2) == 850
    passed.append("winner/loser derived after atomic settlement; loser needs no zero-point row")
    repeat = post(m1,season,6,250,"payout",match,stake["id"])
    assert repeat["id"] == payout["id"] and balance(m1) == 1150
    passed.append("payout replay after completion is idempotent")
    reject("completed financial result immutable",
           lambda: q("UPDATE lolpick.lolpick_matches SET result=2 WHERE id=%s", (match,)))
    cancelled = fixture("cancelled",season,provider,a,b)
    cancelled_stake = post(m1,season,5,-50,"cancel-stake",cancelled,side=1)
    q("UPDATE lolpick.lolpick_matches SET status=4,result=4,result_recorded_at=now() WHERE id=%s", (cancelled,))
    reject("refund must equal stake",
           lambda: post(m1,season,7,49,"bad-refund",cancelled,cancelled_stake["id"]))
    with conn.transaction():
        post(m1,season,7,50,"refund",cancelled,cancelled_stake["id"])
        q("""UPDATE lolpick.lolpick_matches SET settlement_status=3,settlement_at=now(),
          settlement_pool=50,settlement_paid=50 WHERE id=%s""", (cancelled,))
    assert balance(m1) == 1150
    passed.append("cancelled wager refunds original stake")
    reject("ledger update rejected", lambda: q("UPDATE lolpick.lolpick_point_transactions SET amount=0 WHERE id=%s", (stake["id"],)))
    reject("ledger delete rejected", lambda: q("DELETE FROM lolpick.lolpick_point_transactions WHERE id=%s", (stake["id"],)))
    reject("ledger truncate rejected", lambda: q("TRUNCATE lolpick.lolpick_point_transactions"))
    session = q("""INSERT INTO lolpick.lolpick_member_sessions(auth_session_id,member_id)
                  VALUES (%s,%s) RETURNING id,create_dt,expires_at""", (uuid4(),m1)).fetchone()
    assert (session["expires_at"] - session["create_dt"]).total_seconds() == 21600
    passed.append("session lifetime exactly six hours")
    reject("session creation clock cannot refresh",
           lambda: q("UPDATE lolpick.lolpick_member_sessions SET create_dt=now() WHERE id=%s", (session["id"],)))
    q("UPDATE lolpick.lolpick_member_sessions SET status=2 WHERE id=%s", (session["id"],))
    reject("expired session cannot reactivate",
           lambda: q("UPDATE lolpick.lolpick_member_sessions SET status=1 WHERE id=%s", (session["id"],)))
    reject("admin type cannot be set at signup",
           lambda: q("INSERT INTO lolpick.lolpick_members(auth_user_id,google_sub,type) VALUES (%s,'bad-admin',2)", (uuid4(),)))
    reject("oversized log context rejected",
           lambda: q("""INSERT INTO lolpick.lolpick_system_logs(event_code,outcome,severity,source,context)
                  VALUES(900,1,1,3,jsonb_build_object('reason',%s))""", ("x"*1100,)))
    # Two connections: a same-key request must wait for the first transaction,
    # then return its existing entry rather than charging twice.
    concurrent = fixture("concurrent",season,provider,a,b)
    conn2 = psycopg.connect(url,autocommit=True,connect_timeout=10,row_factory=dict_row,
                           options="-c lock_timeout=200 -c statement_timeout=1000")
    try:
        with conn.transaction():
            first = post(m1,season,5,-10,"concurrent",concurrent,side=1)
            try:
                conn2.execute("SELECT * FROM " + schema + ".post_points(%s,%s,5::smallint,-10,%s,%s,NULL,1::smallint)",
                              (m1,season,key("concurrent"),concurrent))
            except psycopg.Error as exc:
                assert exc.sqlstate in ("55P03","57014")
            else:
                raise AssertionError("Concurrent request bypassed the held match/member lock")
        second = conn2.execute("SELECT * FROM " + schema + ".post_points(%s,%s,5::smallint,-10,%s,%s,NULL,1::smallint)",
                               (m1,season,key("concurrent"),concurrent)).fetchone()
        assert first["id"] == second["id"] and balance(m1) == 1140
        passed.append("two connections serialize same wager; retry does not charge twice")
    finally:
        conn2.close()
    print("PASS:",len(passed),"checks")
    for label in passed:
        print(" -",label)
    print("NOT COVERED: runtime-role grants/RLS, weighted payout formula, API auth, load/crash recovery.")
finally:
    # Never use a production schema name or a value supplied by the caller.
    assert re.fullmatch(r"lolpick_v7_check_[0-9a-f]{16}",schema)
    if conn.info.transaction_status != psycopg.pq.TransactionStatus.IDLE:
        conn.rollback()
    if created:
        from psycopg import sql
        conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
    conn.close()
