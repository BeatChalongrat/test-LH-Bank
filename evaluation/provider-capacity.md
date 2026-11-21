# ทดสอบคิวเมื่อเรียกโมเดลผ่าน API

เส้นทางวัดคือ `POST /api/conversations/{id}/messages` ตั้งแต่กำหนดเวลาส่งจนแชตบันทึกคำตอบและคืนผลผ่าน HTTP ใช้ PostgreSQL และการยืนยันตัวตนจริง 5,000 บัญชี ที่ 18 คำถาม/วินาที แคช 60% กับคำถามใหม่ 40% นับ fallback เป็นคำตอบที่พลาดเมื่อคำถามมีเฉลย ระบบหลักยังมี [ผล 30 นาทีของโมเดลในเครื่อง](capacity.md) สำหรับอ้างอิง

## ขอบเขตของผล

HTTP fixture รอ 100 ms ที่ `/embeddings` และ 1,000 ms ที่ `/chat/completions` ให้เวกเตอร์คงที่และเลือกรายการแรกที่ retrieval ส่งมา ทุกคำถามใหม่ผ่านสอง HTTP endpoint จริงภายใน Docker การทดลองนี้วัดผลของจำนวนช่องเรียกพร้อมกันเมื่อทราบเวลาตอบ upstream ชุดคำถาม ภาษา และเอกสารเหมือนกันทั้งสองรอบ ต้องใช้ provider จริงเพื่อวัดคุณภาพโมเดล เวลาบนเครือข่ายภายนอก โควตา 429 และค่า token

## ทำซ้ำบน PowerShell

รันจากราก repo ด้วย Docker Compose ที่รองรับ `!override` และ Python 3.11+ สแตกทดสอบมีฐานข้อมูลแยก ใช้พอร์ต 18770 ไฟล์ผลและ trace ใช้ข้อมูลจำลอง ติดตั้งครั้งแรกด้วย `python scripts/configure.py` และ `python -m pip install -r requirements-dev.txt`

```powershell
$env:CAPACITY_PROJECT = 'provider-capacity'
$env:CAPACITY_STATE_DIR = './var/provider-capacity'
$env:LLM_BASE_URL = 'http://fixture:8080/v1'
$env:LLM_MODEL = 'fixture-first-candidate'
$env:EMBEDDING_MODEL = 'fixture-constant'
$stack = @('-p', $env:CAPACITY_PROJECT, '-f', 'compose.yaml', '-f', 'evaluation/capacity.compose.yaml', '-f', 'deploy/compose.provider.yaml', '-f', 'evaluation/provider-capacity.compose.yaml', '-f', 'evaluation/provider-fixture.compose.yaml')
foreach ($perWorker in 2, 12) {
    $env:INFERENCE_IN_FLIGHT = "$perWorker"
    docker compose @stack up -d --build --wait api
    if ($LASTEXITCODE -ne 0) { break }
    python -m evaluation.capacity --upstream http-fixture --chat --rps 18 --count 5000 --out "var/provider-capacity/queue-$perWorker.json"
}
docker compose @stack stop api db fixture
```

บน Linux ให้สร้างโฟลเดอร์สถานะด้วยบัญชีผู้ยิงโหลดก่อนรัน Compose และตั้ง `CAPACITY_UID_GID` เป็น UID:GID ของบัญชีนั้น เพื่อให้ไฟล์บัญชีทดสอบอ่านได้เฉพาะเจ้าของ

API ใช้ 2 worker จึงมี 4 หรือ 24 ช่องรวม กำหนดเวลารอคิว 350 ms และเพดานคนรอ 12 ต่อ worker ทั้งสองรอบ ตัววัดคืน exit code 1 หากพลาดเกณฑ์ และยังเขียนรายงานครบ เกณฑ์รวม p95 ≤3 วินาที เกณฑ์ cold ≤3 วินาที เกณฑ์แคช ≤250 ms พร้อมคำตอบและแหล่งอ้างอิงตรงเฉลยทุกข้อ แยกดูเหตุผลส่งต่อและ `verified_within_3s` เพราะการส่งต่อเร็วอาจยังทำให้ p95 ดูต่ำ

## วัดผู้ให้บริการจริง

ใช้ [workflow สำหรับ hosted provider](../.github/workflows/provider.yml) เลือก office-180, peak-5000 หรือ soak-32400 หลังตั้ง Environment ตาม [คู่มือติดตั้ง](../docs/provider.md) รอบ peak ส่ง 5,000 แชต รวมคำถามใหม่ 2,000 คำถามและงาน warm-up เพิ่ม ส่วน soak ใช้ 32,400 แชต ตรวจ budget และ quota ทั้ง embeddings และ chat ด้วยผล regression/validation และ token usage ของ deployment เปรียบเทียบ rollout ด้วย p95 รวม, cold, cache, จำนวนคำตอบถูกใน 3 วินาที และจำนวน fallback ทุกเหตุผล
