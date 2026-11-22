"""Text-only drafting and conversation titles using the selected provider."""

import json
import httpx


def stream_text(generator, instruction, content, limit):
    if generator.url == "https://unconfigured.invalid/v1":
        raise ValueError("ยังไม่ได้เชื่อม AI สำหรับเขียน · Connect a chat model in Admin")
    anthropic = generator.protocol == "anthropic"
    headers = {}
    if generator.key:
        headers = (
            {"x-api-key": generator.key, "anthropic-version": "2023-06-01"}
            if anthropic
            else {"Authorization": "Bearer " + generator.key}
        )
    messages = [{"role": "user", "content": content}]
    payload = {"model": generator.model, "messages": messages, "stream": True}
    if anthropic:
        payload.update(system=instruction, max_tokens=limit)
    else:
        messages.insert(0, {"role": "system", "content": instruction})
        payload[
            "max_tokens" if generator.protocol == "llama" else "max_completion_tokens"
        ] = limit
    endpoint = generator.url + ("/messages" if anthropic else "/chat/completions")
    generator.backoff.check()
    with generator.client.stream(
        "POST",
        endpoint,
        headers=headers,
        json=payload,
        timeout=httpx.Timeout(30, connect=4),
    ) as response:
        generator.backoff.observe(response)
        response.raise_for_status()
        complete = False
        for line in response.iter_lines():
            if not line.startswith("data:"):
                continue
            event = line[5:].strip()
            if event == "[DONE]":
                complete = True
                break
            data = json.loads(event)
            if data.get("error"):
                raise ValueError("Upstream generation failed")
            if anthropic:
                if data.get("type") == "message_stop":
                    complete = True
                    break
                delta = data.get("delta", {})
                text = delta.get("text") if delta.get("type") == "text_delta" else None
            else:
                choices = data.get("choices", [])
                text = choices[0].get("delta", {}).get("content") if choices else None
                if choices and choices[0].get("finish_reason"):
                    complete = True
            if isinstance(text, str) and text:
                yield text
        if not complete:
            raise ValueError("Incomplete provider response")
