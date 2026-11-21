"""A reproducible 60:40 hot/cold workload over the eight mock corporate documents."""

from datetime import date, timedelta

HOT = [
    ("ค่าแท็กซี่เบิกได้สูงสุดกี่บาทต่อเที่ยว", "FIN-01", "600 บาท"),
    ("ลาพักร้อนต้องยื่นล่วงหน้ากี่วัน", "HR-01", "3 วันทำงาน"),
    ("สถานะเคส CS-1042 คืออะไร", "CHAT-01", "รอตรวจเอกสาร"),
    ("ใบเสร็จหายใช้หลักฐานอะไรแทน", "FIN-02", "FIN-R02"),
    ("ลาป่วยกี่วันต้องแนบใบรับรองแพทย์", "HR-02", "3 วันทำงาน"),
]
EXECUTIVE = ("รอบทบทวนเงินเดือนผู้บริหารอยู่ในเดือนอะไร", "EX-01", "เมษายน")
SCENARIOS = [
    ("ค่าแท็กซี่ไป{place}วันที่ {day} เบิกได้สูงสุดกี่บาทต่อเที่ยว", "FIN-01", "600 บาท"),
    ("ไปทำงานที่{place}วันที่ {day} ต้องยื่นเบิกค่าเดินทางภายในกี่วัน", "FIN-01", "15 วันปฏิทิน"),
    ("ยื่นเบิกค่าเดินทางผ่านระบบอะไร สำหรับทริปไป{place}วันที่ {day}", "FIN-01", "Finance Portal"),
    ("ใบเสร็จค่าเดินทางไป{place}วันที่ {day} สูญหาย ใช้แบบฟอร์มอะไรแทน", "FIN-02", "FIN-R02"),
    ("ทำบิลค่าเดินทางหาย ยอด {amount} บาท วันที่ {day} ใครลงนามรับรอง", "FIN-02", "หัวหน้างาน"),
    (
        "ต้องแนบหลักฐานอะไรแทนใบเสร็จหาย รายการวันที่ {day} ยอด {amount} บาท",
        "FIN-02",
        "หลักฐานการชำระเงิน",
    ),
    ("จะลาพักร้อนวันที่ {day} ถึง {end} ต้องยื่นล่วงหน้ากี่วันทำงาน", "HR-01", "3 วันทำงาน"),
    ("ขอสรุปขั้นตอนลาพักร้อนช่วง {day} ถึง {end} ต้องยื่นในระบบไหน", "HR-01", "People Portal"),
    ("จะหยุดพักร้อนตั้งแต่ {day} ถึง {end} ต้องให้ใครอนุมัติคำขอ", "HR-01", "หัวหน้างาน"),
    (
        "ลาป่วยตั้งแต่ {day} ถึง {end} ตามระเบียบลาป่วยกี่วันจึงต้องแนบใบรับรองแพทย์",
        "HR-02",
        "ใบรับรองแพทย์",
    ),
    (
        "เริ่มลาป่วยวันที่ {day} กลับมาทำงานวันที่ {end} ต้องกรอกคำขอลาป่วยในระบบเมื่อไร",
        "HR-02",
        "วันที่กลับมาทำงาน",
    ),
    (
        "ขอเบิกค่าแท็กซี่วันที่ {day} ยอด {amount} บาท ต้องแนบหลักฐานอะไรใน Finance Portal",
        "FIN-01",
        "ใบเสร็จ",
    ),
]
PLACES = [
    "เชียงใหม่",
    "ระยอง",
    "สงขลา",
    "ชลบุรี",
    "ขอนแก่น",
    "นครปฐม",
    "ภูเก็ต",
    "สระบุรี",
    "ลำพูน",
    "ลพบุรี",
    "น่าน",
    "ตรัง",
]


def cold_case(index):
    family, variant = index % len(SCENARIOS), index // len(SCENARIOS)
    template, doc, fact = SCENARIOS[family]
    # Pair real scenario fields (travel date/destination, amount or leave interval).
    # Each family has 4,380 distinct combinations. No random cache-busting IDs.
    day_index = (variant * 71 + family * 23) % 365
    group = (variant // 365 + day_index) % 12
    begin = date(2026, 1, 1) + timedelta(days=day_index)
    end = begin + timedelta(days=group + 1)
    question = template.format(
        day=begin.strftime("%d/%m/%Y"),
        end=end.strftime("%d/%m/%Y"),
        amount=100 + group * 35 + day_index % 25,
        place=PLACES[group],
    )
    return question, doc, fact
