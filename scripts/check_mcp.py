"""Verify the local HR fixture over MCP stdio and the authenticated portal API."""

import asyncio
import sys
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from mcp import Client
from mcp.client.stdio import StdioServerParameters

from hr_demo import DOC, invite, local_base


async def verify(role):
    params = StdioServerParameters(
        command=sys.executable,
        args=[str(Path(__file__).resolve().parents[1] / "integrations/mcp/server.py")],
        env={"PORTAL_BASE_URL": local_base(), "PORTAL_INVITE_CODE": invite(role)},
    )
    async with Client(params, read_timeout_seconds=90) as client:
        names = {tool.name for tool in (await client.list_tools()).tools}
        assert names == {
            "search_library",
            "ask_policy",
            "draft_policy_email",
            "get_request",
        }

        async def call(name, **arguments):
            result = await client.call_tool(name, arguments)
            assert not result.is_error, (name, result.content)
            value = result.structured_content
            return value.get("result", value)

        rows = await call("search_library", keyword="ออร์คิด")
        hr = role == "hr"
        assert any(row["doc_id"] == DOC for row in rows) == hr
        answer = await call(
            "ask_policy", question="โครงการตรวจสุขภาพออร์คิดเบิกได้สูงสุดกี่บาทต่อปี"
        )
        assert any(row["doc_id"] == DOC for row in answer["sources"]) == hr
        assert ("2750" in answer["answer"].replace(",", "")) == hr
        ticket_id = str(uuid5(NAMESPACE_URL, "local/demo.non-hr/TR-2187"))
        ticket = await client.call_tool("get_request", {"request_id": ticket_id})
        assert bool(ticket.is_error) == hr
        if not hr:
            email = await call("draft_policy_email", question="บิลค่าเดินทางหาย ทำไงดี")
            assert email["status"] == "answered" and "เรียนทีมงาน" in email["answer"]
            assert email["sources"][0]["doc_id"] == "FIN-02"
        print(
            f"PASS {role}: MCP discovery, library, policy, request ownership"
            + ("" if hr else ", cited email")
        )


async def main():
    for role in ("hr", "non-hr"):
        await verify(role)


if __name__ == "__main__":
    asyncio.run(main())
