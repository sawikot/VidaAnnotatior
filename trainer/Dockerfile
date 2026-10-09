# The trainer: the separate program that carries out the app's training runs (trainer.py).
# Built twice by .github/workflows/release.yml:
#   <image>-trainer:<version>       with GPU support (carries its own CUDA libraries, so the machine
#                                   only needs an NVIDIA driver and Docker's NVIDIA runtime)
#   <image>-trainer:<version>-cpu   for machines without an NVIDIA card (much smaller, much slower)
FROM python:3.13-slim
# Where PyTorch comes from: .../whl/cu128 (GPU) or .../whl/cpu.
ARG TORCH_INDEX=https://download.pytorch.org/whl/cu128
ARG APP_VERSION=dev
ENV APP_VERSION=$APP_VERSION \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    APP_URL=http://app:8088 \
    TRAINING_DIR=/data/training \
    TORCH_HOME=/cache/torch

RUN apt-get update && apt-get install -y --no-install-recommends libglib2.0-0 && rm -rf /var/lib/apt/lists/*
COPY trainer/requirements.txt /app/trainer/requirements.txt
RUN pip install --no-cache-dir torch torchvision --index-url $TORCH_INDEX \
    && pip install --no-cache-dir -r /app/trainer/requirements.txt

COPY trainer /app/trainer
WORKDIR /app/trainer
CMD ["python", "trainer.py"]
