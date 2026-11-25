"""Personal stdio tools backed by the portal's authenticated public APIs."""

import logging
import os
from typing import Any
from uuid import UUID, uuid4

import httpx
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError


def create_server(client: httpx.Client) -> MCPServer:
    server = MCPServer("Employee knowledge desk")

    def request(method, path, **kwargs):
        try:
            response = client.request(method, path, **kwargs)
        except httpx.RequestError as exc:
            raise ToolError(
                "The portal could not be reached. Check the connection."
            ) from exc
        if response.status_code in {401, 403, 404}:
            raise ToolError("Sign in with an account that can access this item.")
        if not response.is_success:
            raise ToolError(
                f"The portal could not complete this request (HTTP {response.status_code})."
            )
        return response.json()

    @server.tool()
    def search_library(keyword: str) -> list[dict[str, Any]]:
        """Find permitted library passages by keyword, document title or ID."""
        term = keyword.strip()
        if not 1 <= len(term) <= 160:
            raise ToolError("Enter a keyword of 1 to 160 characters.")
        return request("GET", "/api/sources", params={"q": term, "limit": 5})

    @server.tool()
    def ask_policy(question: str) -> dict[str, Any]:
        """Ask a work question using authorized evidence. Insufficient evidence can open a support request."""
        row = request("POST", "/api/ask", json={"question": question})
        return {
            k: row[k]
            for k in (
                "status",
                "answer",
                "sources",
                "attribution",
                "ticket_id",
                "trace_id",
            )
        }

    @server.tool()
    def get_request(request_id: UUID) -> dict[str, Any]:
        """Read one request, its current status and staff reply within the account's permissions."""
        row = request("GET", "/api/tickets/" + request_id.hex)
        return {k: row[k] for k in ("id", "question", "state", "resolution", "owner")}

    @server.tool()
    def draft_policy_email(question: str) -> dict[str, Any]:
        """Create a portal chat and a draft email from a cited policy answer. Missing evidence can open a support request."""
        thread, first = str(uuid4()), str(uuid4())
        path = f"/api/conversations/{thread}/messages"
        row = request(
            "POST",
            path,
            json={"question": question, "turn_id": first, "mode": "knowledge"},
        )
        if row["status"] == "answered" and row["sources"]:
            thai = any("\u0e01" <= char <= "\u0e5b" for char in question)
            row = request(
                "POST",
                path,
                json={
                    "turn_id": str(uuid4()),
                    "parent_id": first,
                    "question": "ช่วยร่างอีเมลจากคำตอบก่อนหน้า"
                    if thai
                    else "Draft an email from the previous answer",
                },
            )
        return {
            "conversation_id": thread,
            **{
                k: row[k]
                for k in (
                    "status",
                    "answer",
                    "sources",
                    "attribution",
                    "ticket_id",
                    "trace_id",
                )
            },
        }

    return server


def main():
    logging.getLogger("httpx").setLevel(logging.WARNING)
    url = httpx.URL(os.getenv("PORTAL_BASE_URL", "http://127.0.0.1:8765"))
    loopback = url.host in {"127.0.0.1", "localhost", "::1"}
    if (
        (url.scheme != "https" and not (loopback and url.scheme == "http"))
        or url.userinfo
        or url.path != "/"
        or url.query
        or url.fragment
    ):
        raise SystemExit(
            "Set PORTAL_BASE_URL to an HTTPS origin or a loopback HTTP origin."
        )
    token = os.environ.get("PORTAL_ACCESS_TOKEN")
    invite = os.environ.get("PORTAL_INVITE_CODE")
    if bool(token) == bool(invite) or (invite and not loopback):
        raise SystemExit(
            "Supply a personal PORTAL_ACCESS_TOKEN or a single-use local PORTAL_INVITE_CODE."
        )
    headers = {"Origin": str(url).rstrip("/"), "X-Desk-Request": "1"}
    if token:
        headers["Authorization"] = "Bearer " + token
    with httpx.Client(
        base_url=url, headers=headers, timeout=65, follow_redirects=False
    ) as client:
        if invite:
            client.post("/api/auth/invite", json={"code": invite}).raise_for_status()
        client.get("/api/me").raise_for_status()
        create_server(client).run(transport="stdio")


if __name__ == "__main__":
    main()
