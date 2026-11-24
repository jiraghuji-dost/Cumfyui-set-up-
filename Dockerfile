FROM pytorch/pytorch:2.4.0-cuda12.1-cudnn9-runtime

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_NO_CACHE_DIR=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        git \
        ffmpeg \
        libgl1 \
        libglib2.0-0 \
        wget \
        curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt
RUN git clone https://github.com/comfyanonymous/ComfyUI.git /opt/ComfyUI \
    && python -m pip install --no-cache-dir -r /opt/ComfyUI/requirements.txt

WORKDIR /opt/ComfyUI/custom_nodes
RUN git clone https://github.com/ltdrdata/ComfyUI-Manager.git \
    && git clone https://github.com/Kosinkadink/ComfyUI-AnimateDiff-Evolved.git \
    && git clone https://github.com/Kosinkadink/ComfyUI-VideoHelperSuite.git \
    && git clone https://github.com/cubiq/ComfyUI_essentials.git \
    && for node in /opt/ComfyUI/custom_nodes/*; do \
        if [ -f "$node/requirements.txt" ]; then \
            python -m pip install --no-cache-dir -r "$node/requirements.txt"; \
        fi; \
    done

COPY start.sh /opt/ComfyUI/start.sh
COPY kaggle_runner.py /opt/ComfyUI/kaggle_runner.py
RUN chmod +x /opt/ComfyUI/start.sh

WORKDIR /opt/ComfyUI
CMD ["/opt/ComfyUI/start.sh"]
