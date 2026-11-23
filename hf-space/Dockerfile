# Docker image for Clinical Scribe AI Space
# Read the doc: https://huggingface.co/docs/hub/spaces-sdks-docker

FROM python:3.11

# Create non-root user
RUN useradd -m -u 1000 user
USER user
ENV PATH="/home/user/.local/bin:$PATH"

WORKDIR /app

# Copy requirements
COPY --chown=user ./requirements.txt ./requirements-llm.txt ./

# Install dependencies
RUN pip install --no-cache-dir --upgrade -r requirements.txt -r requirements-llm.txt

# Copy application
COPY --chown=user . /app

# Expose port 7860 for HF Spaces
EXPOSE 7860
ENV APP_ENV=production \
    SESSION_COOKIE_SECURE=true

# Run the application on port 7860
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "7860"]
