FROM python:3.8-slim

WORKDIR /workspace

# System dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    && rm -rf /var/lib/apt/lists/*

# Python analysis tools
RUN pip install --no-cache-dir \
    flake8==6.1.0 \
    pylint==3.0.3 \
    bandit==1.7.6 \
    pyflakes==3.1.0

COPY requirements.txt* ./
RUN pip install --no-cache-dir -r requirements.txt 2>/dev/null || true

COPY pipeline/stage1_static/ ./pipeline/stage1_static/

CMD ["python", "pipeline/stage1_static/carla_custom_rules.py", "--help"]
