"""Append-only SQLite run records, transactional commitments, replay, and exports."""
from __future__ import annotations

import csv
import io
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from sumer.protocol import digest, state_hash


def stamp() -> str:
    return datetime.now(UTC).isoformat()


def encoded(value) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


class Store:
    def __init__(self, path: str | Path):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), timeout=10, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS runs (
                id TEXT PRIMARY KEY, created TEXT NOT NULL, manifest TEXT NOT NULL,
                status TEXT NOT NULL, human_assisted INTEGER NOT NULL DEFAULT 0, result TEXT);
            CREATE TABLE IF NOT EXISTS events (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL REFERENCES runs(id), kind TEXT NOT NULL,
                data TEXT NOT NULL, created TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS events_run ON events(run_id, sequence);
            CREATE TABLE IF NOT EXISTS proposals (
                hash TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id),
                id TEXT NOT NULL, data TEXT NOT NULL, UNIQUE(run_id,id));
            CREATE TABLE IF NOT EXISTS ballots (
                id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id),
                candidate_hash TEXT NOT NULL REFERENCES proposals(hash), data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS years (
                run_id TEXT NOT NULL REFERENCES runs(id), year INTEGER NOT NULL,
                data TEXT NOT NULL, PRIMARY KEY(run_id,year));
            CREATE TABLE IF NOT EXISTS human_resolutions (
                id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id), data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS model_usage (
                message_id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id), data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS spans (
                trace_id TEXT NOT NULL, span_id TEXT NOT NULL, run_id TEXT NOT NULL REFERENCES runs(id),
                data TEXT NOT NULL, PRIMARY KEY(trace_id,span_id));
            CREATE TABLE IF NOT EXISTS trace_imports (path TEXT PRIMARY KEY, offset INTEGER NOT NULL);
        """)
        for table in ("events", "proposals", "ballots", "years", "human_resolutions", "model_usage", "spans"):
            for action in ("UPDATE", "DELETE"):
                self.db.execute(
                    f"CREATE TRIGGER IF NOT EXISTS immutable_{table}_{action.lower()} "
                    f"BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT,'append-only record'); END"
                )
        self.db.execute("CREATE TRIGGER IF NOT EXISTS immutable_manifest BEFORE UPDATE OF "
                        "id,created,manifest ON runs BEGIN SELECT RAISE(ABORT,'immutable manifest'); END")

    def create_run(self, run_id: str, manifest: dict):
        with self.db:
            self.db.execute("INSERT INTO runs(id,created,manifest,status) VALUES(?,?,?,?)",
                            (run_id, stamp(), encoded(manifest), "running"))

    def run(self, run_id: str) -> dict:
        row = self.db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        if row is None:
            raise KeyError(run_id)
        data = dict(row)
        data["manifest"] = json.loads(data["manifest"])
        data["result"] = json.loads(data["result"]) if data["result"] else None
        data["human_assisted"] = bool(data["human_assisted"])
        return data

    def runs(self) -> list[dict]:
        return [self.run(r["id"]) for r in self.db.execute(
            "SELECT id FROM runs ORDER BY created DESC LIMIT 100").fetchall()]

    def status(self, run_id: str, status: str, result: dict | None = None):
        with self.db:
            self.db.execute("UPDATE runs SET status=?,result=COALESCE(?,result) WHERE id=?",
                            (status, encoded(result) if result is not None else None, run_id))

    def _event(self, run_id: str, kind: str, data: dict) -> dict:
        cursor = self.db.execute("INSERT INTO events(run_id,kind,data,created) VALUES(?,?,?,?)",
                                 (run_id, kind, encoded(data), stamp()))
        return {"sequence": cursor.lastrowid, "run_id": run_id, "kind": kind,
                "data": json.loads(encoded(data))}

    def append(self, run_id: str, kind: str, data: dict) -> dict:
        with self.db:
            if kind == "candidates":
                for candidate in data["candidates"]:
                    self.db.execute("INSERT INTO proposals VALUES(?,?,?,?)",
                        (digest(candidate), run_id, candidate["id"], encoded(candidate)))
            if kind == "ballots":
                for reply in data["ballots"]:
                    self.db.execute("INSERT INTO ballots VALUES(?,?,?,?)",
                        (reply["message_id"], run_id, reply["candidate_hash"], encoded(reply)))
            for reply in data.get("responses", data.get("ballots", [])):
                usage = reply.get("trace", {}).get("llm")
                if usage:
                    self.db.execute("INSERT OR IGNORE INTO model_usage VALUES(?,?,?)",
                        (reply["message_id"], run_id, encoded(usage)))
            return self._event(run_id, kind, data)

    def events(self, run_id: str, after: int = 0, year: int | None = None) -> list[dict]:
        self.run(run_id)
        rows = self.db.execute(
            "SELECT sequence,kind,data FROM events WHERE run_id=? AND sequence>? ORDER BY sequence",
            (run_id, after)).fetchall()
        values = [{"sequence": r["sequence"], "run_id": run_id, "kind": r["kind"],
                   "data": json.loads(r["data"])} for r in rows]
        return values if year is None else [e for e in values if e["data"].get("year") == year]

    def commit_year(self, run_id: str, data: dict) -> dict:
        """Atomic state check + unique year + event; duplicate delivery cannot advance twice."""
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            run = self.run(run_id)
            if run["status"] not in ("running", "deadlock"):
                raise ValueError("Run is not active.")
            last = self.db.execute("SELECT data FROM years WHERE run_id=? ORDER BY year DESC LIMIT 1",
                                   (run_id,)).fetchone()
            previous = json.loads(last["data"])["state"] if last else run["manifest"]["initial_state"]
            if state_hash(previous) != state_hash(data["start_state"]):
                raise ValueError("State has already advanced or does not match persisted state.")
            self.db.execute("INSERT INTO years VALUES(?,?,?)", (run_id, data["year"], encoded(data)))
            if data.get("human_resolution"):
                resolution = data["human_resolution"]
                self.db.execute("INSERT INTO human_resolutions VALUES(?,?,?)",
                                (resolution["id"], run_id, encoded(resolution)))
                self.db.execute("UPDATE runs SET human_assisted=1 WHERE id=?", (run_id,))
                self._event(run_id, "human_resolution", resolution)
            self.db.execute("UPDATE runs SET status='running' WHERE id=?", (run_id,))
            return self._event(run_id, "year", data)

    def import_traces(self, path: str | Path) -> int:
        """Incremental OTLP file-exporter import. A partially written tail waits for the next read."""
        path = Path(path)
        if not path.exists():
            return 0
        key = str(path.resolve())
        old = self.db.execute("SELECT offset FROM trace_imports WHERE path=?", (key,)).fetchone()
        offset = old["offset"] if old else 0
        if path.stat().st_size < offset:
            offset = 0
        count = 0
        with path.open("rb") as f, self.db:
            f.seek(offset)
            while True:
                start = f.tell()
                line = f.readline()
                if not line or not line.endswith(b"\n"):
                    f.seek(start)
                    break
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                for resource in record.get("resourceSpans", record.get("resource_spans", [])):
                    for scope in resource.get("scopeSpans", resource.get("scope_spans", [])):
                        for s in scope.get("spans", []):
                            attrs = {a["key"]: a["value"].get("stringValue", a["value"].get("intValue"))
                                     for a in s.get("attributes", [])}
                            rid = attrs.get("sumer.run_id")
                            if not rid or not self.db.execute("SELECT 1 FROM runs WHERE id=?", (rid,)).fetchone():
                                continue
                            trace_id = s.get("traceId", s.get("trace_id"))
                            span_id = s.get("spanId", s.get("span_id"))
                            if trace_id and span_id:
                                cur = self.db.execute("INSERT OR IGNORE INTO spans VALUES(?,?,?,?)",
                                    (trace_id, span_id, rid, encoded(s)))
                                count += cur.rowcount
                offset = f.tell()
            self.db.execute("INSERT INTO trace_imports VALUES(?,?) ON CONFLICT(path) "
                            "DO UPDATE SET offset=excluded.offset", (key, f.tell()))
        return count

    def traces(self, run_id: str) -> list[dict]:
        self.run(run_id)
        return [json.loads(r["data"]) for r in self.db.execute(
            "SELECT data FROM spans WHERE run_id=?", (run_id,)).fetchall()]

    def exports(self, run_id: str) -> dict[str, str]:
        run = self.run(run_id)
        events = self.events(run_id)
        years = [json.loads(r["data"]) for r in self.db.execute(
            "SELECT data FROM years WHERE run_id=? ORDER BY year", (run_id,)).fetchall()]
        output = io.StringIO()
        columns = ["year", "outcome", "land_delta", "feed", "plant", "yes_voters", "no_voters",
                   "starved", "plague_deaths", "population", "grain", "acres", "rounds"]
        writer = csv.DictWriter(output, fieldnames=columns)
        writer.writeheader()
        for y in years:
            writer.writerow({
                "year": y["year"], "outcome": y["outcome"], **y["plan"],
                "yes_voters": ";".join(y["yes_voters"]), "no_voters": ";".join(y["dissent"]),
                "starved": y["report"]["starved"], "plague_deaths": y["report"]["plague_deaths"],
                "population": y["state"]["population"], "grain": y["state"]["grain"],
                "acres": y["state"]["acres"], "rounds": y["rounds"],
            })
        usage = [json.loads(r["data"]) for r in self.db.execute(
            "SELECT data FROM model_usage WHERE run_id=?", (run_id,)).fetchall()]
        summary = {**run, "model_usage": usage,
                   "cost_usd": sum(u.get("cost_usd", 0) for u in usage)}
        report = [f"# Council run {run_id}", f"Status: {run['status']}",
                  f"Human-assisted: {run['human_assisted']}",
                  f"Recorded cost: {summary['cost_usd']:.6f} USD",
                  "## Manifest", json.dumps(run["manifest"], indent=2),
                  "## Final result", json.dumps(run["result"], indent=2),
                  "## Decision log"]
        for e in events:
            if e["kind"] in ("responses", "ballots", "year", "human_resolution",
                             "validation_error", "technical_failure", "escalate"):
                report.extend([f"### Event {e['sequence']}: {e['kind']}",
                               json.dumps(e["data"], indent=2)])
        jsonl = lambda values: "".join(encoded(v) + "\n" for v in values)
        return {"events.jsonl": jsonl(events), "decisions.csv": output.getvalue(),
                "summary.json": json.dumps(summary, indent=2),
                "traces.jsonl": jsonl(self.traces(run_id)), "report.md": "\n\n".join(report) + "\n"}
