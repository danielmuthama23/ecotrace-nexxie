"""Fleet engine. Bins fill, trucks drive REAL road geometry fetched from OSRM, every step is written to the ledger.
Bins can be real: POST /api/telemetry from IoT devices overrides simulated fill levels."""
import asyncio, math, os, random, time, httpx

try:
    from . import core
except ImportError:
    import core

REG = [("Nairobi", -1.29, 36.82, 3), ("Mombasa", -4.04, 39.67, 6), ("Kisumu", -0.09, 34.77, 5), ("Nakuru", -0.30, 36.07, 4),
       ("Eldoret", 0.52, 35.27, 7), ("Garissa", -0.45, 39.65, 19), ("Nyeri", -0.42, 37.0, 5), ("Malindi", -3.2, 40.12, 9), ("Lodwar", 3.12, 35.6, 13)]
DEST = [0, 1, 2, 0, 3, 0, 0, 1, 4]   # recycler each region ships to (long hauls follow real highways)
WASTE = ["Plastic", "Paper", "Glass", "Organic", "E-waste", "General"]
SPEED = float(os.getenv("SIM_SPEED", "400"))    # sim-time multiplier: trucks drive 60 km/h x SPEED
OSRM = os.getenv("OSRM_URL", "https://router.project-osrm.org")
MAX_TRUCKS = int(os.getenv("MAX_TRUCKS", "12"))
CACHE: dict = {}

def hav(a, b):
    p = math.pi / 180; x = math.sin((b[0] - a[0]) * p / 2) ** 2 + math.cos(a[0] * p) * math.cos(b[0] * p) * math.sin((b[1] - a[1]) * p / 2) ** 2
    return 12742 * math.asin(math.sqrt(x))

async def route(a, b):
    key = tuple(round(v, 3) for v in (*a, *b))
    if key in CACHE: return CACHE[key]
    try:
        async with httpx.AsyncClient(timeout=8) as c:
            r = (await c.get(f"{OSRM}/route/v1/driving/{a[1]},{a[0]};{b[1]},{b[0]}?overview=simplified&geometries=geojson")).json()
        res = ([(la, lo) for lo, la in r["routes"][0]["geometry"]["coordinates"]], "osrm")
        CACHE[key] = res; return res
    except Exception:
        return ([a, b], "straight-line fallback (OSRM unreachable)")

class Sim:
    def __init__(s):
        s.regions = [dict(name=n, lat=la, lon=lo, hub=(la + .04, lo - .04), fac=(la - .05, lo + .05), leak=l / 100, kg=k * 1000.0, exp=k * 1000.0 / (1 - l / 100))
                     for (n, la, lo, l), k in zip(REG, [82, 41, 22, 18, 16, 9, 8, 7, 4])]
        s.bins, s.trucks, s.n = {}, {}, 0
        s.tot = {w: v * 1000.0 for w, v in zip(WASTE, [21, 18, 9, 34, 3, 15])}
        for ri in range(len(REG)):
            for _ in range(9): s.add_bin(ri)
    def add_bin(s, ri, bid=None, lat=None, lon=None, waste=None, fill=None):
        r = s.regions[ri]; bid = bid or f"BIN-{random.randint(10000, 99999)}"
        s.bins[bid] = dict(id=bid, region=ri, lat=lat if lat is not None else r["lat"] + random.uniform(-.3, .3), lon=lon if lon is not None else r["lon"] + random.uniform(-.3, .3),
                           fill=fill if fill is not None else random.uniform(10, 80), rate=random.uniform(.15, .7), waste=waste or random.choice(WASTE), busy=False, live=False)
        return s.bins[bid]
    def nearest_region(s, la, lo): return min(range(len(s.regions)), key=lambda i: hav((la, lo), (s.regions[i]["lat"], s.regions[i]["lon"])))
    def ingest(s, bid, fill, lat=None, lon=None, waste=None):
        b = s.bins.get(bid) or s.add_bin(s.nearest_region(lat or 0, lon or 37), bid, lat, lon, waste)
        b.update(fill=fill, live=True)
        if lat is not None: b.update(lat=lat, lon=lon)
        if waste: b["waste"] = waste
        return b
    async def dispatch(s, bid):
        b = s.bins.get(bid)
        if not b or b["busy"] or len(s.trucks) >= MAX_TRUCKS: return None
        b["busy"] = True; s.n += 1; r = s.regions[b["region"]]
        path, src = await route((b["lat"], b["lon"]), r["hub"])
        t = dict(id=f"TRK-{s.n:03d}", bin=bid, stage=0, src=src, dest=DEST[b["region"]]); s.setpath(t, path)
        s.trucks[t["id"]] = t
        core.ledger.append("COLLECTED", dict(bin=bid, truck=t["id"], waste=b["waste"], kg=round(b["fill"] * 1.2, 1), region=r["name"], route=src))
        return t
    def setpath(s, t, path):
        cum = [0.0]
        for i in range(1, len(path)): cum.append(cum[-1] + hav(path[i - 1], path[i]))
        t.update(path=path, cum=cum, d=0.0)
    def pos(s, t):
        d, cum, p = min(t["d"], t["cum"][-1]), t["cum"], t["path"]; i = 1
        while i < len(cum) - 1 and cum[i] < d: i += 1
        u = (d - cum[i - 1]) / ((cum[i] - cum[i - 1]) or 1)
        return p[i - 1][0] + (p[i][0] - p[i - 1][0]) * u, p[i - 1][1] + (p[i][1] - p[i - 1][1]) * u
    async def step(s, t, dt):
        t["d"] += 60 / 3600 * SPEED * dt
        if t["d"] < t["cum"][-1]: return
        b = s.bins[t["bin"]]; r = s.regions[b["region"]]; d = s.regions[t["dest"]]
        if t["stage"] == 0:
            path, t["src"] = await route(r["hub"], d["fac"]); s.setpath(t, path); t["stage"] = 1
            core.ledger.append("SORTED", dict(bin=b["id"], truck=t["id"], hub=r["name"], to=d["name"], route=t["src"]))
        else:
            kg = round(b["fill"] * 1.2, 1); r["kg"] += kg; r["exp"] += kg / (1 - r["leak"]) * random.uniform(.97, 1.03); s.tot[b["waste"]] += kg
            core.ledger.append("RECYCLED", dict(bin=b["id"], truck=t["id"], kg=kg, recycler=d["name"], certificate=f"CERT-{int(time.time())}-{t['id']}"))
            b.update(fill=3.0, busy=False); s.trucks.pop(t["id"], None)
    async def run(s):
        last = time.time()
        while True:
            await asyncio.sleep(.5); now = time.time(); dt, last = now - last, now
            for b in s.bins.values():
                if not b["busy"] and not b["live"]: b["fill"] = min(100.0, b["fill"] + b["rate"] * dt)
            for b in [b for b in s.bins.values() if b["fill"] >= 90 and not b["busy"]][:2]: await s.dispatch(b["id"])
            for t in list(s.trucks.values()): await s.step(t, dt)
    def snapshot(s):
        tt = sum(s.tot.values())
        regs = [dict(name=r["name"], lat=r["lat"], lon=r["lon"], hub=r["hub"], fac=r["fac"], tonnes=round(r["kg"] / 1000, 1), **core.zone_risk(r["exp"], r["kg"])) for r in s.regions]
        trucks = []
        for t in s.trucks.values():
            la, lo = s.pos(t); trucks.append(dict(id=t["id"], bin=t["bin"], lat=la, lon=lo, stage=t["stage"], dest=s.regions[t["dest"]]["name"], source=t["src"]))
        return dict(ts=time.time(), regions=regs, trucks=trucks, totals={k: round(v) for k, v in s.tot.items()},
                    kpi=dict(tonnes=round(sum(r["kg"] for r in s.regions) / 1000, 1), diversion=round(100 - s.tot["General"] / tt * 100, 1), bins=len(s.bins), trucks=len(s.trucks)),
                    bins=[dict(id=b["id"], lat=b["lat"], lon=b["lon"], fill=round(b["fill"], 1), waste=b["waste"], busy=b["busy"], live=b["live"], region=s.regions[b["region"]]["name"]) for b in s.bins.values()])
