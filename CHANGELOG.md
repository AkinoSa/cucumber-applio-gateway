# Changelog — cucumber-applio-gateway

## 1.0.0 (2026-11-26)

首个正式公开发布版本（统一 **Applio v2 + RVC** 双引擎独立推理架构，配套插件 `astrbot_plugin_cucumber_applio_console`）。

### 功能

- **Applio v2 原生推理集成**：引入 `IAHispano/Applio` `VoiceConverter`（RMVPE / 自动音准 `f0_autotune` / 共振峰调节 `formant_shifting` / 音质净化 `cleanup_mode`），并支持与经典 RVC CLI 回退兼容
- **人声频段智能副歌裁剪**：采用 `300~3400Hz` 人声带通能量包络、结构先验及 `±2.5s` 句间换气谷吸附，默认提供 `10s` 候选缓冲区间（`GW_SEP_BUFFER_SECONDS=10`）并在分离后精挑最优完整乐句
- **人声伴奏分离**：支持 PyMSS (`3_HP-Vocal-UVR.pth`) 与 `.ckpt` / `.onnx` 分离模型动态扫描（`list_sep_models`），含静音检测与自动换段重试
- **TTS 语音换声**：`/tts_convert`（路径白名单、时长/大小上限、统一串行队列）
- **常驻推理 Worker**：`rvc_worker.py` 保持模型/特征提取器热加载（节省模型重载耗时），失败自动回退子进程，空闲自动退出
- **8 参数 FFT 卷积混响**：合成脉冲响应（IR）真实卷积 + 峰值防爆音保护
- **开箱即用 Docker 镜像**：统一独立 `Dockerfile`、`.env.example` 与 GitHub Actions 自动构建 `ghcr.io/akinosa/cucumber-applio-gateway:1.0.0`

### 已知行为

- 单实例设计（全局信号量不跨进程）
- 若在局域网/无 GPU 主机上使用，默认采用 CPU 推理并限制 `GW_OMP_THREADS=2` 防止 OOM
- TTS 输入路径限于 `/work/tts` 与 `/work/uploads` 白名单
