# Production Dockerfile for AccessFix PDF Accessibility Checker & OCR Engine
# Target platform: Render / Docker Container Runtime

FROM python:3.12-slim-bookworm

# Prevent Python from writing bytecode and enable immediate log streaming
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive

WORKDIR /app

# Install native system dependencies required by AccessFix:
# - tesseract-ocr: Tesseract OCR binary engine
# - tesseract-ocr-eng: English language traineddata package
# - poppler-utils: pdftotext & pdfinfo for independent post-OCR text layer validation
# - ca-certificates: Secure SSL operations
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    tesseract-ocr-eng \
    poppler-utils \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Build-time verification: ensure Tesseract binary and English language data are installed
RUN tesseract --version && tesseract --list-langs

# Copy dependency requirements first to leverage Docker layer caching
COPY requirements.txt ./

# Install Python packages (Flask, pikepdf, pypdfium2, Pillow, numpy, gunicorn)
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source code and assets into the image
COPY . .

# Ensure storage directory exists and has full read/write permissions
RUN mkdir -p /app/data && chmod 777 /app/data

# Configure environment variables
# - PORT: Render dynamically injects $PORT (default 5050 for local runs)
# - TESSERACT_CMD: executable name in PATH
# - ACCESSFIX_DATA_DIR: persistence directory inside the container
ENV PORT=5050 \
    TESSERACT_CMD=tesseract \
    ACCESSFIX_DATA_DIR=/app/data

# Expose default application port
EXPOSE 5050

# Gunicorn startup command with Render dynamic $PORT binding
# Uses 1 worker with multi-threading to preserve in-memory JobManager state and progress polling
CMD ["sh", "-c", "exec gunicorn --bind 0.0.0.0:${PORT:-5050} --workers ${WEB_CONCURRENCY:-1} --threads ${GUNICORN_THREADS:-8} --timeout 180 run:app"]
