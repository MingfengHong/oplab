FROM python:3.13-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY pyproject.toml README.md ./
COPY packages ./packages
COPY services ./services
RUN pip install --no-cache-dir .

EXPOSE 8000
CMD ["uvicorn", "oplab_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
