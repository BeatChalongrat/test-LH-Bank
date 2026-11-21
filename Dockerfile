FROM python:3.11-slim-bookworm
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 OMP_NUM_THREADS=2
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends poppler-utils tesseract-ocr tesseract-ocr-tha tesseract-ocr-eng && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY knowledge knowledge
COPY data data
COPY evaluation/*.py evaluation/cases.json evaluation/validation.json ./evaluation/
COPY static static
RUN useradd --no-create-home --uid 10001 desk
USER desk
CMD ["uvicorn","knowledge.api:app","--host","0.0.0.0","--port","8000","--no-access-log"]
