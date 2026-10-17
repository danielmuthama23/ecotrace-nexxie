"""Core: SQLite-persisted hash-chained ledger (optional Hedera anchoring) + anomaly maths."""
import hashlib, json, os, sqlite3, threading, time
from dataclasses import dataclass, field, asdict

def canon(o) -> bytes: return json.dumps(o, sort_keys=True, separators=(",", ":")).encode()
def digest(k, d, p, t): return hashlib.sha256(canon({"k": k, "d": d, "p": p, "t": t})).hexdigest()

@dataclass
class Event:
    kind: str; data: dict; prev: str; ts: float = field(default_factory=time.time); hash: str = ""

class Ledger:
    def __init__(self, path=None):
        self.lock = threading.Lock()
        self.db = sqlite3.connect(path or os.getenv("ECOTRACE_DB", "ecotrace.db"), check_same_thread=False)
        self.db.execute("create table if not exists events(i integer primary key, kind text, data text, prev text, ts real, hash text)")
        self.events = [Event(k, json.loads(d), p, t, h) for k, d, p, t, h in
                       self.db.execute("select kind,data,prev,ts,hash from events order by i")]
    def append(self, kind, data):
        with self.lock:
            prev = self.events[-1].hash if self.events else "0" * 64
            ev = Event(kind, data, prev); ev.hash = digest(kind, data, prev, ev.ts)
            self.events.append(ev)
            self.db.execute("insert into events(kind,data,prev,ts,hash) values(?,?,?,?,?)",
                            (kind, json.dumps(data), prev, ev.ts, ev.hash)); self.db.commit()
        self.anchor(ev); return ev
    def verify(self):
        prev = "0" * 64
        for i, e in enumerate(self.events):
            if e.prev != prev or e.hash != digest(e.kind, e.data, e.prev, e.ts): return {"valid": False, "broken_at": i}
            prev = e.hash
        return {"valid": True, "length": len(self.events), "head": prev, "hedera": bool(os.getenv("HEDERA_TOPIC_ID"))}
    def tail(self, n=20): return [asdict(e) for e in self.events[-n:]][::-1]
    def anchor(self, ev):
        """Anchor the hash on Hedera Consensus Service (hiero-sdk-python) when credentials are configured."""
        if not (os.getenv("HEDERA_OPERATOR_ID") and os.getenv("HEDERA_TOPIC_ID")): return
        try:
            from hiero_sdk_python import Client, Network, AccountId, PrivateKey, TopicId, TopicMessageSubmitTransaction
            c = Client(Network(os.getenv("HEDERA_NETWORK", "testnet")))
            c.set_operator(AccountId.from_string(os.environ["HEDERA_OPERATOR_ID"]), PrivateKey.from_string(os.environ["HEDERA_OPERATOR_KEY"]))
            TopicMessageSubmitTransaction(topic_id=TopicId.from_string(os.environ["HEDERA_TOPIC_ID"]), message=ev.hash).freeze_with(c).sign(c.operator_private_key).execute(c)
        except Exception as e: print("hedera anchor skipped:", e)

ledger = Ledger()

def variance(collected, hub):
    if hub is None: return None
    pct = abs(collected - hub) / collected * 100
    return {"variance_pct": round(pct, 1), "status": "INVESTIGATE" if pct > 8 else "OK"}

def record_collection(customer, bin_id, waste, kg, collector, truck_kg=None, hub_kg=None):
    ev = ledger.append("COLLECTION", dict(customer=customer, bin=bin_id, waste=waste, kg=kg, collector=collector, truck_kg=truck_kg, hub_kg=hub_kg))
    return {"event": asdict(ev), "anomaly": variance(kg, hub_kg)}

def zone_risk(expected_t, recorded_t):
    gap = (expected_t - recorded_t) / max(expected_t, 1e-9) * 100
    return {"gap_pct": round(gap, 1), "risk": "HIGH" if gap > 15 else "MEDIUM" if gap > 7 else "LOW"}

def forecast_overflow(history, threshold=100.0):
    if len(history) < 2: return None
    slope = (history[-1] - history[0]) / (len(history) - 1)
    return None if slope <= 0 else round((threshold - history[-1]) / slope, 1)
