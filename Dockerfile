# cucumber-applio-gateway · Applio / RVC 控制台网关
#
# 从公开基础镜像独立构建，不依赖任何私有镜像或特定宿主机路径。
#
# 架构说明:
#   * 系统 Python (/usr/local/bin/python): RVC + Applio v2 双引擎推理 + PyMSS 分离
#   * /opt/gwvenv/bin/python: Gateway Gradio 6.31.0 服务 (与推理环境隔离)
#   * 两者通过子进程调用衔接 (gateway.py -> Applio VoiceConverter / infer/cli.py / pymss.cli / rvc_worker.py)
#
# 模型权重、index、用户音频等不打包进镜像，通过卷挂载提供 (见 compose.yaml)。

FROM python:3.12-slim-trixie

ENV PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive \
    GRADIO_ANALYTICS_ENABLED=False

# ---- 系统依赖 ----
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    git \
    build-essential \
    libsndfile1 \
    libsndfile1-dev \
    && rm -rf /var/lib/apt/lists/*

# ---- RVC + Applio 基础推理依赖 (系统 Python) ----
COPY build/requirements-rvc.txt /tmp/requirements-rvc.txt
RUN pip install --no-cache-dir -r /tmp/requirements-rvc.txt \
    && pip install --no-cache-dir stftpitchshift torchcrepe soxr \
    && rm /tmp/requirements-rvc.txt

# ---- RVC 推理底层 (固定 commit) ----
ARG RVC_COMMIT=81eed5e8f68b6bed1789f682fe78cdd324495afc
RUN git clone https://github.com/RVC-Project/Retrieval-based-Voice-Conversion-WebUI.git /app \
    && cd /app && git checkout ${RVC_COMMIT}

# ---- Applio v2 原生推理引擎 (固定 commit) ----
ARG APPLIO_COMMIT=445b0f628ad2481415871325b7012d01cc9b52bc
RUN git clone https://github.com/IAHispano/Applio.git /app/applio \
    && cd /app/applio && git checkout ${APPLIO_COMMIT} \
    && rm -rf /app/applio/.git \
    && mkdir -p /app/applio/rvc/models/predictors \
                /app/applio/rvc/models/embedders/contentvec \
                /app/applio/assets/weights \
                /app/applio/assets/indices

# ---- Gateway Gradio 隔离环境 ----
COPY build/requirements-gateway.txt /tmp/requirements-gateway.txt
RUN python -m venv --system-site-packages /opt/gwvenv \
    && /opt/gwvenv/bin/pip install --no-cache-dir -r /tmp/requirements-gateway.txt \
    && rm /tmp/requirements-gateway.txt

# ---- Gateway 服务代码 ----
COPY gateway.py rvc_worker.py /gateway/

# ---- 环境变量 (宿主机映射路径通过 compose/.env 注入，不写死私有路径) ----
ENV PYTHONPATH=/app/applio:/app \
    GW_SYSTEM_PYTHON=/usr/local/bin/python \
    GW_APP_DIR=/app \
    GW_WEIGHTS_DIR=/app/assets/weights \
    GW_INDEX_DIR=/app/assets/indices \
    GW_PYMSS_MODEL_DIR=/app/assets/pymss_weights \
    GW_CONTAINER_WORK_DIR=/work \
    GW_HOST_WORK_DIR= \
    GW_CONTAINER_PLUGIN_DIR=/mnt/plugin_data \
    GW_HOST_PLUGIN_DIR= \
    GW_SEP_MODEL=3_HP-Vocal-UVR.pth \
    GW_MAX_CLIP_SECONDS=30 \
    GW_SEP_BUFFER_SECONDS=10 \
    GW_DEFAULT_CLIP_SECONDS=30 \
    GW_MAX_INPUT_SECONDS=900 \
    GW_MAX_INPUT_BYTES=83886080 \
    GW_DEFAULT_PITCH=0 \
    GW_DEFAULT_F0=rmvpe \
    GW_DEFAULT_INDEX_RATE=0.75 \
    GW_DEFAULT_PROTECT=0.33 \
    GW_DEFAULT_RESAMPLE_SR=0 \
    GW_DEFAULT_RMS_MIX_RATE=1.0 \
    GW_DEFAULT_FORMAT=wav \
    GW_FFMPEG_TIMEOUT=180 \
    GW_SEP_TIMEOUT=1200 \
    GW_RVC_TIMEOUT=1200 \
    GW_DOWNLOAD_TIMEOUT=90 \
    GW_QUEUE_MAX=4 \
    GW_OMP_THREADS=2 \
    GW_TTS_MAX_SECONDS=60 \
    GW_TTS_TTL=3600 \
    GW_WORKER_IDLE_SECONDS=600 \
    GW_SLOT_WAIT=180

# ---- 启动 ----
WORKDIR /app
EXPOSE 7865

HEALTHCHECK --interval=60s --timeout=10s --retries=3 --start-period=90s \
    CMD ["/opt/gwvenv/bin/python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:7865/', timeout=5)"]

CMD ["/opt/gwvenv/bin/python", "/gateway/gateway.py"]
