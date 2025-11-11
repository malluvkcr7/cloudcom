FROM python:3.12-slim

# Create app directory
WORKDIR /app

# Install system deps needed by some python packages (e.g., httptools)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy source
COPY . /app

ENV PYTHONUNBUFFERED=1

# Default workdir is /app. Compose will pass the proper uvicorn command.
