# EcoTrace Nexus

Waste collection, sorting, recycling and audit platform: live Leaflet map on real roads, hash-chained ledger
(optional Hedera anchoring), RAG assistant over live data, OpenAI waste classification, MCP server.

```
frontend/index.html   Leaflet map + dashboard (served by the API, responsive)
backend/api.py        FastAPI: REST API, static hosting, background fleet engine
backend/sim.py        Bins + trucks. Trucks follow real road geometry from OSRM
backend/core.py       SQLite-persisted SHA-256 hash-chain ledger, Hedera anchoring, anomaly maths
backend/rag.py        TF-IDF retrieval over live state + policy KB, answers via OpenAI (or Anthropic)
backend/vision.py     OpenAI vision classifier
backend/mcp_server.py MCP tools so Claude can operate a running instance
```

## Quick start

From the project root, the usual command is:
```bash
npm start
```

This runs the frontend dev server on `http://localhost:3000`.

Start the backend in a second terminal:
```bash
cd backend
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                                    # add OPENAI_API_KEY here (never commit it)
uvicorn api:app --reload --host 0.0.0.0 --port 8000
```

If you want to start the frontend manually instead:
```bash
cd frontend
npm install
npm start
```

The backend API stays on `http://localhost:8000`.
If you need the frontend to target a different API host, set `window.ECOTRACE_BACKEND_URL` before loading the page.

Needs internet for map tiles (CARTO/OpenStreetMap) and road routing (OSRM).
If OSRM is unreachable, trucks fall back to straight lines and the trace panel says so.

## What is real and what is simulated
| Part | Status |
|---|---|
| Map tiles, roads, truck routes | Real (OpenStreetMap data, OSRM routing) |
| Ledger, hashes, verification, RAG, vision | Real |
| Bin fill levels and truck dispatches | Simulated by default. Send real sensor data with `POST /api/telemetry` and that bin stops simulating |
| Regional "unrecorded tonnage" gaps | Seeded demo values (Garissa, Lodwar high) |
| Truck speed | 60 km/h x `SIM_SPEED` (400 default) so long hauls finish in about a minute |

## Connecting real devices
```bash
curl -X POST localhost:8000/api/telemetry -H "Content-Type: application/json" \
  -d '{"bin_id":"BIN-90001","fill":88,"lat":-1.286,"lon":36.817,"waste":"Plastic"}'
```
Set `ECOTRACE_API_KEY` in `.env` to require an `X-API-Key` header on telemetry and collection writes.

## AI features
- **Ask the system:** retrieves the most relevant bins, trucks, regions, events and policy text, then OpenAI answers using only that context and cites sources. Without a key it returns the best matching data.
- **Vision:** OpenAI vision classifies the uploaded image directly, with no local model weights to download.
- **Hedera:** set `HEDERA_OPERATOR_ID`, `HEDERA_OPERATOR_KEY`, `HEDERA_TOPIC_ID` to anchor every event hash to a consensus topic (testnet by default).

## MCP
With the API running: `python mcp_server.py`. Claude Desktop config:
```json
{"mcpServers":{"ecotrace":{"command":"python","args":["/path/to/backend/mcp_server.py"]}}}
```
Tools: system_summary, trace_bin, ask_system, dispatch_truck, verify_ledger, classify_waste_image.

## API
`GET /api/state` `GET /api/trucks/{id}/route` `POST /api/dispatch/{bin}` `POST /api/telemetry` `POST /api/collections`
`GET /api/ledger` `GET /api/ledger/verify` `POST /api/ask` `POST /api/vision/classify` `GET /api/health`

## Known limits
Fleet state is in memory (the ledger persists in SQLite, bins and trucks reset on restart). No user accounts yet, only the optional API key.
RAG uses TF-IDF; swap in OpenAI embeddings plus pgvector for large deployments. Run behind HTTPS and restrict CORS before exposing publicly.
