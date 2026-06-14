# Hugging Face Spaces (Docker SDK). HF routes traffic to port 7860.
FROM python:3.11-slim

# wamerican provides /usr/share/dict/words, used by the ligature-repair step.
RUN apt-get update && apt-get install -y --no-install-recommends wamerican \
    && rm -rf /var/lib/apt/lists/*

# HF Spaces run the container as a non-root user with UID 1000.
RUN useradd -m -u 1000 user
USER user
ENV PATH="/home/user/.local/bin:$PATH"

WORKDIR /app

COPY --chown=user requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

COPY --chown=user . .

ENV PORT=7860
EXPOSE 7860

# 2 workers to match the 2 vCPU free tier; long timeout so heavy PDFs finish
# instead of getting killed mid-conversion.
CMD ["sh", "-c", "gunicorn webapp:app --bind 0.0.0.0:${PORT} --timeout 600 --workers 2"]
