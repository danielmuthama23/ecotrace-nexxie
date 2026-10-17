"""MCP server: lets Claude (or any MCP client) operate a running EcoTrace backend.
Start the API first, then:  python mcp_server.py   (env ECOTRACE_URL, default http://localhost:8000)"""
import os, httpx
from mcp.server.fastmcp import FastMCP

URL = os.getenv("ECOTRACE_URL", "http://localhost:8000") + "/api"
mcp = FastMCP("ecotrace-nexus")
def _get(p, **kw): return httpx.get(URL + p, timeout=30, **kw).json()

@mcp.tool()
def system_summary() -> dict:
    """KPIs, regional risk and active trucks right now."""
    s = _get("/state"); return {"kpi": s["kpi"], "regions": s["regions"], "trucks": s["trucks"]}
@mcp.tool()
def trace_bin(bin_id: str) -> dict:
    """Current state of one bin plus its recent chain-of-custody events."""
    s = _get("/state"); return {"bin": next((b for b in s["bins"] if b["id"] == bin_id), None),
                                "events": [e for e in _get("/ledger", params={"limit": 200}) if e["data"].get("bin") == bin_id]}
@mcp.tool()
def ask_system(question: str) -> dict:
    """Ask the RAG assistant a question about live operations or waste policy."""
    return httpx.post(URL + "/ask", json={"question": question}, timeout=60).json()
@mcp.tool()
def dispatch_truck(bin_id: str) -> dict:
    """Send a truck to collect a bin."""
    return httpx.post(f"{URL}/dispatch/{bin_id}", timeout=30).json()
@mcp.tool()
def verify_ledger() -> dict:
    """Verify the integrity of the whole ledger."""
    return _get("/ledger/verify")
@mcp.tool()
def classify_waste_image(image_path: str, bin_id: str | None = None) -> dict:
    """Classify a waste photo with OpenAI vision."""
    with open(image_path, "rb") as f:
        return httpx.post(URL + "/vision/classify", files={"file": f}, data={"bin_id": bin_id or ""}, timeout=120).json()

if __name__ == "__main__": mcp.run()
