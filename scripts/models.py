"""Fetch pinned public model files and verify their content before activation."""

import argparse
import hashlib
import os
import shutil
import tempfile
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
E5 = "https://huggingface.co/Xenova/multilingual-e5-small/resolve/761b726dd34fb83930e26aab4e9ac3899aa1fa78/"
QWEN = "https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct-GGUF/resolve/91cad51170dc346986eccefdc2dd33a9da36ead9/"
READER = "https://huggingface.co/onnx-community/xlm-roberta-base-squad2-distilled-ONNX/resolve/484112fae76dde6ad01b640192d559cbc2d488e1/"
FILES = {
    "reader": (
        READER + "onnx/model_int8.onnx",
        "reader.onnx",
        "7e9eb6a4b524a076b137eb976d0457378216e195b218a4d63fd6f4f694bc26cc",
    ),
    "reader_tokenizer": (
        READER + "tokenizer.json",
        "reader-tokenizer.json",
        "3a56def25aa40facc030ea8b0b87f3688e4b3c39eb8b45d5702b3a1300fe2a20",
    ),
    "encoder": (
        E5 + "onnx/model_quantized.onnx",
        "encoder.onnx",
        "f80102d3f2a1229f387d3c81909990d8945513e347b0eab049f7de3c6f98c193",
    ),
    "tokenizer": (
        E5 + "tokenizer.json",
        "tokenizer.json",
        "0b44a9d7b51c3c62626640cda0e2c2f70fdacdc25bbbd68038369d14ebdf4c39",
    ),
    "llm": (
        QWEN + "qwen2.5-1.5b-instruct-q4_k_m.gguf",
        "qwen.gguf",
        "6a1a2eb6d15622bf3c96857206351ba97e1af16c30d7a74ee38970e434e9407e",
    ),
    "windows_server": (
        "https://github.com/ggml-org/llama.cpp/releases/download/b12606/llama-b12606-bin-win-cpu-x64.zip",
        "llama.zip",
        "425eeb2a94a9600dadf369a7c9c7dd0141948921efc0fa9ec7872970fffc845d",
    ),
    "windows_vulkan": (
        "https://github.com/ggml-org/llama.cpp/releases/download/b12606/llama-b12606-bin-win-vulkan-x64.zip",
        "vulkan.zip",
        "76e3c7df27b892bff158bf46f30c3a01aff7ff20a05830d6f492721f9ebc136b",
    ),
}


def digest(path):
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def fetch(key, directory):
    url, name, expected = FILES[key]
    target = directory / name
    if not target.exists() or digest(target) != expected:
        with tempfile.NamedTemporaryFile(dir=directory, delete=False) as tmp:
            temp = Path(tmp.name)
        try:
            print("Downloading", name, flush=True)
            with (
                urllib.request.urlopen(url, timeout=120) as src,
                temp.open("wb") as dst,
            ):
                shutil.copyfileobj(src, dst, 1024 * 1024)
            if digest(temp) != expected:
                raise ValueError(f"Checksum mismatch for {name}")
            os.replace(temp, target)
        finally:
            temp.unlink(missing_ok=True)
    # The non-root API and indexer read these public weights through a bind mount.
    if os.name == "posix":
        target.chmod(0o644)
    if key in {"windows_server", "windows_vulkan"}:
        with zipfile.ZipFile(target) as archive:
            archive.extractall(directory / ("vulkan" if key == "windows_vulkan" else "llama"))
    print("Verified", name, flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--llm", action="store_true")
    parser.add_argument("--windows-server", action="store_true")
    parser.add_argument("--windows-vulkan", action="store_true")
    args = parser.parse_args()
    directory = Path(os.getenv("MODEL_DIR", ROOT / "models"))
    directory.mkdir(parents=True, exist_ok=True)
    for key in (
        ["encoder", "tokenizer"]
        + (["llm", "reader", "reader_tokenizer"] if args.llm else [])
        + (["windows_server"] if args.windows_server else [])
        + (["windows_vulkan"] if args.windows_vulkan else [])
    ):
        fetch(key, directory)
