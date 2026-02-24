# Build stage
FROM python:3.9-slim-bookworm as builder

# Install build dependencies
RUN apt-get update && apt-get install -y \
    --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Create and activate virtual environment
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy and install requirements
COPY requirements.txt .
RUN pip install --no-cache-dir wheel && \
    pip install --no-cache-dir -r requirements.txt

# Final stage
FROM python:3.9-slim-bookworm

# Copy virtual environment from builder
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Install only runtime dependencies
RUN apt-get update && apt-get install -y \
    --no-install-recommends \
    libfreetype6 \
    libharfbuzz0b \
    libfribidi0 \
    libcairo2 \
    libpango-1.0-0 \
    libpangoft2-1.0-0 \
    libpangocairo-1.0-0 \
    libgdk-pixbuf-2.0-0 \
    libffi8 \
    shared-mime-info \
    libpng16-16 \
    libjpeg62-turbo \
    fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

# Set up application directory
WORKDIR /litkeeper

# Copy only necessary files
COPY app app/
COPY run.py .

# Create data directories with correct permissions
RUN mkdir -p app/data/epubs app/data/logs && \
    chmod -R 777 app/data

# Set environment variables
ENV FLASK_APP=app
ENV FLASK_ENV=production
ENV PYTHONPATH=/litkeeper
ENV PYTHONUNBUFFERED=1

# Expose port
EXPOSE 5000

# Run the application with Flask development server
CMD ["flask", "run", "--host=0.0.0.0"]