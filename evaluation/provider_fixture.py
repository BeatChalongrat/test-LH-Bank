"""Isolated HTTP timing fixture for the provider adapter. Contains no model or credentials."""

import asyncio
import json
import os

from fastapi import FastAPI

app = FastAPI()


@app.post("/v1/embeddings")
async def embed(payload: dict):
    await asyncio.sleep(float(os.getenv("FIXTURE_EMBED_SECONDS", ".10")))
    return {
        "data": [
            {"index": i, "embedding": [1.0] + [0.0] * 383}
            for i in range(len(payload["input"]))
        ]
    }


@app.post("/v1/chat/completions")
async def select(payload: dict):
    await asyncio.sleep(float(os.getenv("FIXTURE_CHAT_SECONDS", "1.0")))
    evidence = json.loads(payload["messages"][-1]["content"])["evidence"]
    content = json.dumps({"choice": evidence[0]["id"]})
    return {"choices": [{"message": {"content": content}}]}
