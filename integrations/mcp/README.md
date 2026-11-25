# Personal MCP connection

The stdio server connects an MCP host to the same portal API used by the employee website. Each process uses one employee identity. The host receives the passages and answers that employee can read. Use an approved host for the department's data.

## Installation

From the repository root on Windows, install the connector in a separate environment.

```powershell
python -m venv var/mcp-env
.\var\mcp-env\Scripts\python -m pip install -r integrations/mcp/requirements.txt
```

In the host's stdio server settings, set the command to the **absolute path** to that Python executable. Add one argument with the absolute path to `integrations/mcp/server.py`. On Linux or macOS use `var/mcp-env/bin/python`. Supply the following through the host's environment or secret manager.

| Setting | Value |
| --- | --- |
| `PORTAL_BASE_URL` | The portal's HTTPS origin. For local Compose the default is `http://127.0.0.1:8765`. |
| `PORTAL_ACCESS_TOKEN` | A personal portal API bearer token provisioned for the employee. For local acceptance testing use `PORTAL_INVITE_CODE` instead. |
| `PORTAL_INVITE_CODE` | Local loopback only. The server exchanges one fresh single-use invitation for a session when it starts. |

## Tools

| Tool | What the employee can do |
| --- | --- |
| `search_library(keyword)` | ค้นคลังด้วยคำสำคัญ ชื่อ หรือรหัส และอ่านผลได้สูงสุดห้ารายการตามสิทธิ์ |
| `ask_policy(question)` | ถามระเบียบพร้อมหลักฐาน เมื่อหลักฐานไม่พอ ระบบอาจเปิดเรื่องให้เจ้าหน้าที่ตามกระบวนการปกติ |
| `get_request(request_id)` | ดูสถานะ ผู้รับเรื่อง และคำตอบจากเจ้าหน้าที่ตามสิทธิ์ของบัญชี |
| `draft_policy_email(question)` | เปิดแชต ถามระเบียบ แล้วร่างอีเมลจากคำตอบที่มีเอกสารนโยบายอ้างอิง พนักงานตรวจและส่งอีเมลเอง หากหลักฐานไม่พอ เครื่องมือคืนผลจากการถามครั้งแรก |

## Verify

Start the local portal and run `python scripts/hr_demo.py setup` as described in the [setup guide](../../README.md). With Docker available, run the check from the repository root.

```powershell
.\var\mcp-env\Scripts\python scripts/check_mcp.py
```

The check opens independent HR and non-HR sessions, lists tools, compares library and chat access to the Orchid document, checks request ownership and creates a cited draft in the non-HR account. A question without evidence can create a support request in this fixture.
