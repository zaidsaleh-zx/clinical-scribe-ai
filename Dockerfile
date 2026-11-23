FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt ./requirements.txt
COPY requirements-audio.txt requirements-llm.txt ./
ARG INSTALL_AUDIO=false
ARG INSTALL_LLM=false
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt && \
    if [ "$INSTALL_AUDIO" = "true" ]; then pip install --no-cache-dir -r requirements-audio.txt; fi && \
    if [ "$INSTALL_LLM" = "true" ]; then pip install --no-cache-dir -r requirements-llm.txt; fi

COPY . .

ENV PYTHONUNBUFFERED=1 \
    PORT=8000

EXPOSE 8000

CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
