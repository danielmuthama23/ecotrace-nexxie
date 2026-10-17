"""Waste image classification via OpenAI vision only.
This module uses a remote vision model and does not load any local image model."""
import base64, hashlib, io, json, os
from pathlib import Path

from dotenv import load_dotenv
from PIL import Image

load_dotenv(Path(__file__).resolve().with_name(".env"))

try:
    from . import core
except ImportError:
    import core

BINS = {"Plastic": "yellow", "Paper": "blue", "Glass": "orange", "Organic": "green", "E-waste": "purple", "Hazardous": "red", "General": "black"}


def run_openai(raw: bytes):
    from openai import OpenAI
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set")
    url = "data:image/jpeg;base64," + base64.b64encode(raw).decode()
    client = OpenAI(api_key=api_key)
    r = client.chat.completions.create(
        model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        response_format={"type": "json_object"},
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": f"Classify the waste in this photo into one of {list(BINS)}. "
                            'Reply JSON: {"category":str,"items":[str],"contaminated":bool,"confidence":0-1,"advice":str}'
                },
                {"type": "image_url", "image_url": {"url": url}},
            ],
        }],
    )
    return json.loads(r.choices[0].message.content)


def classify(raw: bytes, bin_id: str | None = None):
    if not os.getenv("OPENAI_API_KEY"):
        return {
            "detections": [],
            "engine": "openai-vision-disabled",
            "category": "General",
            "confidence": 0.0,
            "bin_colour": "black",
            "ledger_hash": None,
        }

    image = Image.open(io.BytesIO(raw)).convert("RGB")
    image.load()
    ai = run_openai(raw)
    category = ai.get("category", "General")
    confidence = float(ai.get("confidence", 0.0) or 0.0)
    res = {
        "detections": ai.get("items", []),
        "engine": "openai-vision",
        "category": category,
        "confidence": confidence,
        "openai": ai,
    }
    res["bin_colour"] = BINS.get(category, "black")
    res["ledger_hash"] = core.ledger.append(
        "CLASSIFICATION",
        {
            "bin": bin_id,
            "image_sha256": hashlib.sha256(raw).hexdigest(),
            "category": category,
            "confidence": confidence,
        },
    ).hash
    return res
