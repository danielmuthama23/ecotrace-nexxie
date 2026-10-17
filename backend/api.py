"""FastAPI gateway + static frontend.   uvicorn api:app --reload   ->  http://localhost:8000"""
import asyncio, os
from contextlib import asynccontextmanager
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

from fastapi import FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

try:
    from . import core, rag
    from .sim import Sim
except ImportError:
    import core, rag
    from sim import Sim

sim = Sim()
@asynccontextmanager
async def lifespan(app):
    task = asyncio.create_task(sim.run()); yield; task.cancel()

app = FastAPI(title="EcoTrace Nexus", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

class Telemetry(BaseModel):
    bin_id: str; fill: float; lat: float | None = None; lon: float | None = None; waste: str | None = None
class Collection(BaseModel):
    customer: str; bin_id: str; waste: str; kg: float; collector: str; truck_kg: float | None = None; hub_kg: float | None = None
class Question(BaseModel):
    question: str

def guard(key):
    if os.getenv("ECOTRACE_API_KEY") and key != os.getenv("ECOTRACE_API_KEY"): raise HTTPException(401, "Invalid X-API-Key")

@app.get("/api/health")
def health(): return {"ok": True, "trucks": len(sim.trucks), "bins": len(sim.bins)}
@app.get("/api/config")
def config(): return {"openai": bool(os.getenv("OPENAI_API_KEY")), "hedera": bool(os.getenv("HEDERA_TOPIC_ID"))}
@app.get("/api/state")
def state(): return sim.snapshot()
@app.get("/api/trucks/{tid}/route")
def truck_route(tid: str):
    t = sim.trucks.get(tid)
    if not t: raise HTTPException(404, "Truck not active")
    return {"path": t["path"], "stage": t["stage"], "source": t["src"]}
@app.post("/api/dispatch/{bin_id}")
async def dispatch(bin_id: str):
    t = await sim.dispatch(bin_id)
    if not t: raise HTTPException(409, "Bin unknown, already collected, or fleet is full")
    return {"truck": t["id"]}
@app.post("/api/telemetry")
def telemetry(t: Telemetry, x_api_key: str | None = Header(None)):
    guard(x_api_key); b = sim.ingest(t.bin_id, t.fill, t.lat, t.lon, t.waste); return {"bin": b["id"], "fill": b["fill"]}
@app.post("/api/collections")
def collection(c: Collection, x_api_key: str | None = Header(None)):
    guard(x_api_key); return core.record_collection(c.customer, c.bin_id, c.waste, c.kg, c.collector, c.truck_kg, c.hub_kg)
@app.get("/api/ledger")
def ledger(limit: int = 20): return core.ledger.tail(min(limit, 200))
@app.get("/api/ledger/verify")
def verify(): return core.ledger.verify()
@app.post("/api/ask")
async def ask(q: Question): return await rag.answer(q.question, sim.snapshot(), core.ledger.tail(40))
@app.post("/api/vision/classify")
async def classify(file: UploadFile = File(...), bin_id: str | None = Form(None)):
    try: import vision
    except ImportError: raise HTTPException(501, "Install openai and pillow to enable vision")
    return await asyncio.to_thread(vision.classify, await file.read(), bin_id)

app.mount("/", StaticFiles(directory=Path(__file__).resolve().parent.parent / "frontend", html=True), name="web")
