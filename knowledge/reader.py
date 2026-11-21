"""Extractive question answering with a learned no-answer score."""

import os
import re
from pathlib import Path

import numpy as np
import onnxruntime as ort
from tokenizers import Tokenizer

from .db import ROOT

# Broad procedural questions, exceptions and personal eligibility need the full selector.
FACT = re.compile(
    r"อะไร|ที่ไหน|ไหน|เมื่อไร|เมื่อไหร่|เท่าไหร่|เท่าไร|กี่|ใคร|how (?:much|many)|who|when|which",
    re.I,
)
COMPLEX = re.compile(
    r"ขั้นตอน|สรุป|ยกเว้น|(?<!ตั้ง)แต่|หรือไม่|ได้ไหม|ได้มั้ย|แน่นอน|ถ้าหาก|กรณีพิเศษ|เปรียบเทียบ|อธิบาย"
)


class Reader:
    revision = "xlmr-squad2-484112fa:margin4:rival-no-answer-v2"

    def __init__(self, directory):
        options = ort.SessionOptions()
        options.intra_op_num_threads = int(os.getenv("READER_THREADS", "2"))
        options.inter_op_num_threads = 1
        options.add_session_config_entry("session.intra_op.allow_spinning", "0")
        self.model = ort.InferenceSession(
            str(directory / "reader.onnx"), options, providers=["CPUExecutionProvider"]
        )
        self.tokenizer = Tokenizer.from_file(str(directory / "reader-tokenizer.json"))
        self.inputs = [i.name for i in self.model.get_inputs()]

    @classmethod
    def configured(cls):
        directory = Path(os.getenv("MODEL_DIR", ROOT / "models"))
        mode = os.getenv("EVIDENCE_READER", "auto")
        if mode not in {"auto", "on", "off"}:
            raise ValueError("EVIDENCE_READER must be auto, on or off")
        if mode == "off" or (
            mode == "auto"
            and not all(
                (directory / n).is_file()
                for n in ("reader.onnx", "reader-tokenizer.json")
            )
        ):
            return None
        return cls(directory)

    def verify(self, question, passage):
        pair = self.tokenizer.encode(question, passage)
        if len(pair.ids) > 384:
            return None
        feed = {
            "input_ids": np.array([pair.ids], dtype=np.int64),
            "attention_mask": np.array([pair.attention_mask], dtype=np.int64),
            "token_type_ids": np.array([pair.type_ids], dtype=np.int64),
        }
        start, end = self.model.run(
            ["start_logits", "end_logits"], {k: feed[k] for k in self.inputs}
        )
        context = np.flatnonzero([sid == 1 for sid in pair.sequence_ids])
        if not context.size:
            return None
        lengths = context[None, :] - context[:, None]
        scores = start[0, context][:, None] + end[0, context][None, :]
        valid = (lengths >= 0) & (lengths < 32)
        best = np.max(np.where(valid, scores, -np.inf))
        return float(best - start[0, 0] - end[0, 0])
