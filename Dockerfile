# Use Python 3.10 slim image
FROM python:3.10-slim

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    POETRY_VERSION=1.7.1 \
    POETRY_HOME="/opt/poetry" \
    POETRY_NO_INTERACTION=1 \
    POETRY_VIRTUALENVS_CREATE=false

# Add Poetry to PATH
ENV PATH="$POETRY_HOME/bin:$PATH"

# Install system dependencies
RUN apt-get update && apt-get install -y \
    curl \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Poetry
RUN curl -sSL https://install.python-poetry.org | python3 -

# Set working directory
WORKDIR /app

# Copy dependency files
COPY pyproject.toml ./

# Install Python dependencies
RUN poetry install --no-root --only main

# Install Playwright browsers (for future scraping)
RUN poetry run playwright install chromium
RUN poetry run playwright install-deps chromium

# Copy application code
COPY src/ ./src/
COPY configs/ ./configs/

# Create directories for logs and data
RUN mkdir -p /app/logs /app/data

# Default command (override in docker-compose or when running)
CMD ["python", "-c", "from src.utils.logger import setup_logging; setup_logging(); print('Arbitr system initialized')"]
