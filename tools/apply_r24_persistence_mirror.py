from pathlib import Path

pb_path = Path('persistent_bridge.py')
pb = pb_path.read_text(encoding='utf-8')
pb = pb.replace('BRIDGE_VERSION = "PERSIST-BRIDGE-R3-CHECKPOINT"', 'BRIDGE_VERSION = "PERSIST-BRIDGE-R4-EXACT-MIRROR"')

start = pb.index('            if data["opportunities"]:')
end = pb.index('            if runtime_identity:', start)
new_remote = '''            synced_runs = 0
            synced_picks = 0
            protected_final_dates = set()

            if data["opportunities"]:
                snapshot_generated_at = str(
                    data["opportunities"][0].get("updated_at")
                    or data["opportunities"][0].get("generated_at")
                    or datetime.now(timezone.utc).isoformat()
                )
                cur.execute(
                    """INSERT INTO asi_opportunity_snapshot
                       (singleton,generated_at,payload,bridge_version)
                       VALUES(1,%s,%s::jsonb,%s)
                       ON CONFLICT(singleton) DO UPDATE SET
                       generated_at=excluded.generated_at,
                       payload=excluded.payload,
                       bridge_version=excluded.bridge_version""",
                    (
                        snapshot_generated_at,
                        json.dumps(_json_records(data["opportunities"]), ensure_ascii=False),
                        BRIDGE_VERSION,
                    ),
                )
            else:
                # Exact mirror semantics: an explicitly empty local snapshot must
                # not leave an old remote snapshot available for a later restore.
                cur.execute("DELETE FROM asi_opportunity_snapshot WHERE singleton=1")

            # H-10 is immutable across workers too. A stale/provisional local
            # worker must never downgrade a remote H10_FINAL run.
            cur.execute("SELECT market_date,note FROM asi_premarket_runs")
            remote_final_dates = {
                str(r[0]) for r in cur.fetchall()
                if str(r[1] or "").startswith("H10_FINAL")
            }
            local_run_dates = set()
            local_pick_counts = {}
            for row in data["runs"]:
                market_date = str(row.get("market_date") or "")
                if not market_date:
                    continue
                note = str(row.get("note") or "")
                local_final = note.startswith("H10_FINAL")
                if market_date in remote_final_dates and not local_final:
                    protected_final_dates.add(market_date)
                    continue
                pick_count = max(0, int(row.get("pick_count") or 0))
                cur.execute(
                    """INSERT INTO asi_premarket_runs
                       (market_date,generated_at,status,pick_count,note,updated_at)
                       VALUES(%s,%s,%s,%s,%s,now())
                       ON CONFLICT(market_date) DO UPDATE SET
                       generated_at=excluded.generated_at,
                       status=excluded.status,
                       pick_count=excluded.pick_count,
                       note=excluded.note,
                       updated_at=excluded.updated_at""",
                    (
                        market_date,
                        str(row.get("generated_at") or ""),
                        str(row.get("status") or ""),
                        pick_count,
                        note,
                    ),
                )
                local_run_dates.add(market_date)
                local_pick_counts[market_date] = pick_count
                synced_runs += 1

            # Replace, do not merely upsert, each mirrored date. This removes
            # ghost rank 4/5 rows when a valid run shrinks to fewer picks or zero.
            for market_date in sorted(local_run_dates):
                cur.execute(
                    "DELETE FROM asi_premarket_picks WHERE market_date=%s",
                    (market_date,),
                )

            for row in data["picks"]:
                market_date = str(row.get("market_date") or "")
                if market_date not in local_run_dates:
                    continue
                rank = int(row.get("rank") or 0)
                pick_count = int(local_pick_counts.get(market_date, 0))
                if rank < 1 or rank > pick_count:
                    continue
                cur.execute(
                    """INSERT INTO asi_premarket_picks
                       (market_date,rank,payload,updated_at)
                       VALUES(%s,%s,%s::jsonb,now())
                       ON CONFLICT(market_date,rank) DO UPDATE SET
                       payload=excluded.payload,updated_at=excluded.updated_at""",
                    (
                        market_date,
                        rank,
                        json.dumps(
                            {str(k): _json_value(v) for k, v in row.items()},
                            ensure_ascii=False,
                        ),
                    ),
                )
                synced_picks += 1
'''
pb = pb[:start] + new_remote + pb[end:]

old_return = '''            "runs": len(data["runs"]),
            "picks": len(data["picks"]),
'''
new_return = '''            "runs": synced_runs,
            "picks": synced_picks,
            "protected_final_dates": len(protected_final_dates),
'''
if old_return not in pb:
    raise SystemExit('remote return-count anchor missing')
pb = pb.replace(old_return, new_return, 1)

func_start = pb.index('def _write_local_payload(state, opportunities, runs, picks):')
body_start = pb.index('        if opportunities:', func_start)
body_end = pb.index('        con.commit()', body_start)
new_local = '''        if opportunities:
            import pandas as pd
            frame = pd.DataFrame(list(opportunities))
            if not frame.empty:
                frame.to_sql("opportunities", con, if_exists="replace", index=False)
        elif _local_table_exists(con, "opportunities"):
            # Remote explicitly has no snapshot. Do not preserve a stale local one.
            con.execute("DELETE FROM opportunities")

        accepted_run_dates = set()
        incoming_pick_counts = {}
        protected_final_dates = set()
        for row in runs or []:
            market_date = str(row.get("market_date") or "")
            if not market_date:
                continue
            note = str(row.get("note") or "")
            existing = con.execute(
                "SELECT note FROM premarket_runs WHERE market_date=?",
                (market_date,),
            ).fetchone()
            if existing and str(existing[0] or "").startswith("H10_FINAL") and not note.startswith("H10_FINAL"):
                protected_final_dates.add(market_date)
                continue
            pick_count = max(0, int(row.get("pick_count") or 0))
            con.execute(
                "INSERT OR REPLACE INTO premarket_runs"
                "(market_date,generated_at,status,pick_count,note) VALUES(?,?,?,?,?)",
                (
                    market_date,
                    str(row.get("generated_at") or ""),
                    str(row.get("status") or ""),
                    pick_count,
                    note,
                ),
            )
            accepted_run_dates.add(market_date)
            incoming_pick_counts[market_date] = pick_count

        for market_date in accepted_run_dates:
            con.execute("DELETE FROM premarket_picks WHERE market_date=?", (market_date,))

        written_picks = 0
        for row in picks or []:
            market_date = str(row.get("market_date") or "")
            if market_date not in accepted_run_dates:
                continue
            rank = int(row.get("rank") or 0)
            if rank < 1 or rank > int(incoming_pick_counts.get(market_date, 0)):
                continue
            cols = [
                "market_date", "generated_at", "rank", "ticker", "company",
                "decision", "confidence", "ai_score", "risk", "reference_price",
                "entry_low", "entry_high", "stop_loss", "target_1", "target_2",
                "ihsg_regime", "reason", "source_phase",
            ]
            con.execute(
                """INSERT OR REPLACE INTO premarket_picks
                   (market_date,generated_at,rank,ticker,company,decision,confidence,ai_score,risk,
                    reference_price,entry_low,entry_high,stop_loss,target_1,target_2,ihsg_regime,reason,source_phase)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                [row.get(col) for col in cols],
            )
            written_picks += 1
'''
pb = pb[:body_start] + new_local + pb[body_end:]

old_local_return = '''            "runs": len(runs or []),
            "picks": len(picks or []),
'''
new_local_return = '''            "runs": len(accepted_run_dates),
            "picks": written_picks,
            "protected_final_dates": len(protected_final_dates),
'''
if old_local_return not in pb:
    raise SystemExit('local return-count anchor missing')
pb = pb.replace(old_local_return, new_local_return, 1)

# Filter PostgreSQL restore rows by authoritative run pick_count as defense-in-depth.
old_pg = '            cur.execute("SELECT payload FROM asi_premarket_picks ORDER BY market_date,rank")\n'
new_pg = '''            cur.execute(
                """SELECT p.payload FROM asi_premarket_picks p
                   JOIN asi_premarket_runs r ON r.market_date=p.market_date
                   WHERE p.rank BETWEEN 1 AND r.pick_count
                   ORDER BY p.market_date,p.rank"""
            )
'''
if old_pg not in pb:
    raise SystemExit('postgres restore pick-query anchor missing')
pb = pb.replace(old_pg, new_pg, 1)

pb_path.write_text(pb, encoding='utf-8')

api_path = Path('runtime_api.py')
api = api_path.read_text(encoding='utf-8')
api = api.replace('version="1.1"', 'version="1.2"')
old_api = '''                """SELECT market_date,rank,payload FROM asi_premarket_picks
                   WHERE market_date IN (
                     SELECT market_date FROM asi_premarket_runs ORDER BY market_date DESC LIMIT 20
                   ) ORDER BY market_date,rank"""
'''
new_api = '''                """SELECT p.market_date,p.rank,p.payload FROM asi_premarket_picks p
                   JOIN (
                     SELECT market_date,pick_count FROM asi_premarket_runs
                     ORDER BY market_date DESC LIMIT 20
                   ) r ON r.market_date=p.market_date
                   WHERE p.rank BETWEEN 1 AND r.pick_count
                   ORDER BY p.market_date,p.rank"""
'''
if old_api not in api:
    raise SystemExit('runtime API pick-query anchor missing')
api = api.replace(old_api, new_api, 1)
api_path.write_text(api, encoding='utf-8')
print('R24 PERSISTENCE EXACT-MIRROR PATCH APPLIED')
