# ใช้งานผ่าน Model API

โหมดนี้เรียก API ของ gateway เดียวกันสำหรับสร้างเวกเตอร์และเลือกหลักฐาน พอร์ทัลใช้ PostgreSQL/pgvector, OCR และ worker จัดทำดัชนีในระบบเดิม การติดตั้งใหม่ข้ามขั้นตอนดาวน์โหลดน้ำหนักโมเดลได้ทั้งหมด การจัดทำดัชนีจะส่งข้อความเอกสารรวมถึงเอกสาร HR ไปที่ embedding endpoint ส่วนการตอบแชตจะส่งคำถามและเฉพาะหลักฐานที่ผู้ถามมีสิทธิ์ไปที่ chat endpoint ใช้ deployment ที่องค์กรอนุมัติให้ประมวลผลเอกสารประเภทนั้น

## ติดตั้งใหม่

1. รัน `python scripts/configure.py` เพื่อสร้าง secrets ของฐานข้อมูลและเว็บ
2. เพิ่มค่าด้านล่างใน `.env` ที่สร้างขึ้น ตั้งชื่อ deployment ให้ตรงกับบัญชีของคุณ และใส่ bearer key ทางเครื่องหรือ secret manager ตัวอย่างใช้ endpoint ของ OpenAI รองรับ gateway อื่นที่มีสัญญา API เดียวกัน
3. รันคำสั่งติดตั้ง แล้วตรวจสิทธิ์ HR กับนอก HR ผ่านเว็บ

```dotenv
COMPOSE_PATH_SEPARATOR=|
COMPOSE_FILE=compose.yaml|deploy/compose.provider.yaml
LLM_BASE_URL=https://api.openai.com/v1
LLM_MODEL=gpt-4.1-mini
EMBEDDING_MODEL=text-embedding-3-small
LLM_API_KEY=
```

```shell
docker compose config --services
docker compose up -d --build --wait api indexer
python scripts/hr_demo.py setup
python scripts/hr_demo.py verify
python scripts/hr_demo.py hr
python scripts/hr_demo.py non-hr
```

รายการ active services ควรมี db, seed, api และ indexer เท่านั้นเมื่อปิด monitoring และสามารถเปิด admin ด้วย `python scripts/hr_demo.py admin` ตัวอย่างข้างต้นให้ทั้งสอง endpoint ใช้คีย์เดียวกันบน host เดียวกัน หากใช้ alias ที่เปลี่ยนโมเดลหลังบ้านได้ ให้กำหนด `LLM_REVISION` และ `EMBEDDING_REVISION` ตามรุ่น deployment

Chat ต้องรองรับ `POST /chat/completions` พร้อม JSON schema แบบ strict และเลือก evidence ID หรือ NONE Embeddings ต้องรองรับ `POST /embeddings` ซึ่งรับข้อความเป็น batch และคืนเวกเตอร์ชนิด float ที่ 384 มิติ ระบบตรวจจำนวน ลำดับ มิติ และ normalize ก่อนใช้ คะแนน cosine ของโมเดลแต่ละรุ่นมีการกระจายต่างกัน ให้รันชุด regression และ validation เพื่อตั้ง threshold ก่อนอนุญาตคำตอบอัตโนมัติ [Chat structured output](https://developers.openai.com/api/docs/guides/structured-outputs) · [Embeddings API](https://developers.openai.com/api/reference/resources/embeddings/methods/create)

## เปลี่ยนระบบเดิมพร้อมเก็บบัญชี เอกสาร และสิทธิ์

ทำในช่วง maintenance หลังยืนยันปลายทางและงบสำหรับการส่งเอกสารทั้งคลัง รวมประวัติรุ่นเอกสาร คำสั่ง reembed สร้างเวกเตอร์ใหม่ในธุรกรรมเดียวและเปลี่ยนรุ่นแคชเมื่อสำเร็จ หาก upstream ขัดข้อง ระบบ rollback กลับไปยังดัชนีก่อนเริ่มงาน

1. ขณะยังใช้ Compose เดิม ให้สำรอง DB ด้วย `python scripts/backup.py` เก็บค่า deployment เดิมไว้ย้อนกลับ แล้วรัน `docker compose stop api indexer llm` ให้ db ทำงานต่อ
2. ตั้งค่า provider ใน `.env` ตามด้านบน ใช้ชื่อ Compose project และ DB volume เดิม
3. รัน `docker compose build seed` แล้ว `docker compose run --rm --no-deps -T seed python -m knowledge reembed` ขั้นนี้คงสิทธิ์และต้นฉบับของไฟล์ที่อัปโหลดไว้
4. หลัง reembed สำเร็จ ให้รัน `docker compose up -d --wait api indexer` และตรวจสองบัญชีอีกครั้ง หากกลับไปใช้ encoder เดิมหลังแปลงสำเร็จ ให้หยุด API/indexer แล้ว reembed กลับด้วย configuration เดิม หรือ restore backup ตามคู่มือ D2 [คู่มือ D2](operations.md)

## คิวและการทดสอบ

ค่าเริ่มต้นมี 2 API worker แต่ละ worker ทำ lookup พร้อมกันได้ 12 งานและเรียก chat ได้พร้อมกัน 12 งาน เมื่อช่อง chat เต็มจะรอสูงสุด 350 ms และอนุญาตให้รอได้ 12 งานต่อ worker ระบบปล่อยช่องทันทีที่ upstream ตอบ ก่อนบันทึกคำตอบและเคส เมื่อ provider ตอบ 429 จะเคารพ Retry-After ภายใน worker และแยกสาเหตุเป็น `provider_rate_limited` ติดตามคิวในระบบด้วย `inference_overloaded` เมื่อ embedding provider ตอบ 429 ใช้ `embedding_rate_limited` และพักตาม Retry-After เช่นกัน ข้อขัดข้องฝั่ง embeddings อื่นใช้ `embedding_unavailable` หากคิว lookup ถึงเพดานใช้ `retrieval_overloaded` แคชและ guardrail ยังคงทำงานโดยไม่เรียก provider

ตั้ง `INFERENCE_IN_FLIGHT`, `INFERENCE_MAX_WAITERS`, `INFERENCE_QUEUE_TIMEOUT`, `RETRIEVAL_IN_FLIGHT`, `API_WORKERS` ตาม quota และ latency ที่วัด ผลคูณจำนวน worker และจำนวน instance คือจำนวนช่องเรียกพร้อมกันทั้งระบบ ตรวจทั้ง requests/min และ tokens/min สำหรับ 18 คำถาม/วินาทีที่ cache hit 60% คาดราว 432 คำขอ embed และ 432 คำขอ chat ต่อนาทีเมื่อทุก cold query ผ่านถึง generation ยังต้องเผื่อ ingestion และพีกระยะสั้น

ทวนคุณภาพกับ provider จริงด้วยสองคำสั่งด้านล่าง ทั้งสองคำสั่งเรียก API ตามค่าที่ตั้งและอาจมีค่าบริการ

```shell
docker compose run --rm --no-deps -T seed python -m evaluation.run --out /state/provider-regression.json
docker compose run --rm --no-deps -T seed python -m evaluation.run --cases evaluation/validation.json --out /state/provider-validation.json
```

[ผลโหลดและการทำซ้ำ](../evaluation/provider-capacity.md)

## รันกับ provider จริงบน GitHub Actions

ตั้ง GitHub Environment ชื่อ `model-api` โดยใส่ variables `LLM_BASE_URL`, `LLM_MODEL`, `EMBEDDING_MODEL` และ secret `LLM_API_KEY` เลือก Actions → Hosted provider acceptance → Run workflow ค่าเริ่มต้น `quality-only` ตรวจ HR/นอก HR และชุดเฉลยจริงผ่าน provider ด้วยข้อมูลจำลองใน repo หากต้องการโหลดให้เลือก office-180, peak-5000 หรือ soak-32400 ตามโควตาและงบที่อนุมัติ Workflow เก็บผลเป็น artifact แม้ค่าโหลดจะพลาดเกณฑ์ รายงานนี้ใช้ตรวจผลก่อนนำผลที่วัดได้มาเผยแพร่
