"""RAG over the LIVE system: policy knowledge base + current bins, trucks, regions and ledger events.
Retrieval: TF-IDF (rebuilt per query, corpus is small). Generation: OpenAI when a key is set."""
import asyncio, os
from pathlib import Path

from dotenv import load_dotenv
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

load_dotenv(Path(__file__).resolve().with_name(".env"))

KB = [("Policy: segregation", "Organic food waste goes in the green bin for composting or biogas. Contamination above 5% downgrades the whole batch."),
      ("Policy: e-waste", "Batteries, phones and cables go in the purple bin or to a certified e-waste recycler. Lithium batteries are a fire risk and never belong in general waste."),
      ("Policy: hazardous", "Paint, solvents, sharps and chemicals go in the red bin and are collected only by licensed handlers with a manifest."),
      ("Policy: plastics", "PET (1), HDPE (2) and PP (5) are accepted. Rinse containers and keep film plastic out of the yellow bin."),
      ("Policy: weight variance", "A gap above 8% between collection and hub weight triggers an investigation. Check scale calibration before assuming misconduct."),
      ("Policy: illegal dumping", "Regions recording over 15% less tonnage than expected are HIGH risk and queued for field inspection."),
      ("Policy: chain of custody", "Every event is SHA-256 hashed and linked to the previous one; heads are anchored to Hedera Consensus Service.")]

def docs(snap, events):
    d = list(KB)
    for r in snap["regions"]: d.append((f"Region {r['name']}", f"Region {r['name']}: {r['tonnes']} tonnes recorded, gap {r['gap_pct']}% vs expected, dumping risk {r['risk']}."))
    for b in snap["bins"]: d.append((f"{b['id']}", f"{b['id']} in {b['region']} holds {b['waste']} waste, {b['fill']}% full, {'truck dispatched' if b['busy'] else 'awaiting pickup'}{', live sensor' if b['live'] else ''}."))
    for t in snap["trucks"]: d.append((f"{t['id']}", f"{t['id']} is carrying {t['bin']}, {'heading to the sorting hub' if t['stage'] == 0 else 'driving to the ' + t['dest'] + ' recycler'} at {t['lat']:.3f},{t['lon']:.3f}. Route: {t['source']}."))
    for e in events: d.append((f"Event {e['kind']}", f"{e['kind']} {e['data']} hash {e['hash'][:10]}"))
    d.append(("Totals", f"Collected {snap['kpi']['tonnes']} tonnes, diversion rate {snap['kpi']['diversion']}%, {snap['kpi']['trucks']} trucks active, material kg {snap['totals']}."))
    return d

def retrieve(q, d, k=8):
    v = TfidfVectorizer(stop_words="english").fit([t + " " + b for t, b in d] + [q])
    s = cosine_similarity(v.transform([q]), v.transform([t + " " + b for t, b in d]))[0]
    return [(d[i], float(s[i])) for i in s.argsort()[::-1][:k] if s[i] > 0]

SYS = "You are EcoTrace's operations analyst. Answer ONLY from the context. Cite sources in [brackets]. If the context lacks the answer, say so. Be concise."

def _llm(q, ctx):
    prompt = "Context:\n" + "\n".join(f"[{t}] {b}" for (t, b), _ in ctx) + f"\n\nQuestion: {q}"
    api_key = os.getenv("OPENAI_API_KEY")
    if api_key:
        from openai import OpenAI
        client = OpenAI(api_key=api_key)
        r = client.chat.completions.create(model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"), messages=[{"role": "system", "content": SYS}, {"role": "user", "content": prompt}])
        return r.choices[0].message.content, "openai"
    return None, "retrieval-only"

async def answer(q, snap, events):
    ctx = retrieve(q, docs(snap, events)); engine = "retrieval-only"
    if not ctx: return {"answer": "Nothing in the system matches that question.", "sources": [], "engine": engine}
    try: text, engine = await asyncio.to_thread(_llm, q, ctx)
    except Exception as e: text, engine = None, f"retrieval-only (LLM error: {type(e).__name__})"
    text = text or "No LLM key set, so here is the best matching data: " + " ".join(b for (_, b), _ in ctx[:3])
    return {"answer": text, "engine": engine, "sources": [{"title": t, "score": round(s, 3)} for (t, _), s in ctx]}
