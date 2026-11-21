import os
import threading
from pathlib import Path

import numpy as np
import onnxruntime as ort
from tokenizers import Tokenizer

from .db import ROOT


class Encoder:
    def __init__(self):
        base = Path(os.getenv("MODEL_DIR", ROOT / "models"))
        options = ort.SessionOptions()
        options.intra_op_num_threads = int(os.getenv("ENCODER_THREADS", "2"))
        options.inter_op_num_threads = 1
        options.add_session_config_entry("session.intra_op.allow_spinning", "0")
        self.net = ort.InferenceSession(
            str(base / "encoder.onnx"), options, providers=["CPUExecutionProvider"]
        )
        self.tokenizer = Tokenizer.from_file(str(base / "tokenizer.json"))
        self.tokenizer.enable_truncation(max_length=512)
        self.tokenizer.enable_padding(pad_id=1, pad_token="<pad>")
        self.lock = threading.Lock()

    def encode(self, texts, prefix):
        with self.lock:
            batch = self.tokenizer.encode_batch([prefix + ": " + t for t in texts])
        ids = np.asarray([t.ids for t in batch], dtype=np.int64)
        mask = np.asarray([t.attention_mask for t in batch], dtype=np.int64)
        feed = {
            "input_ids": ids,
            "attention_mask": mask,
            "token_type_ids": np.zeros_like(ids),
        }
        hidden = self.net.run(
            None, {i.name: feed[i.name] for i in self.net.get_inputs()}
        )[0]
        pooled = (hidden * mask[..., None]).sum(axis=1) / mask.sum(axis=1)[:, None]
        return (pooled / np.linalg.norm(pooled, axis=1, keepdims=True)).astype(
            np.float32
        )
