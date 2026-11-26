# Release Notes — cucumber-applio-gateway v1.0.0

首个正式公开发布版本（统一 **Applio v2 + RVC** 双引擎本地推理网关，配套 `astrbot_plugin_cucumber_applio_console` v1.0.0）。

## 功能亮点

- **歌曲翻唱流水线**：取音源（本地挂载 / HTTP(S) / 可选网易云节点）→ 严格裁剪校验 → 人声频段（`300~3400Hz`）FFmpeg 副歌裁剪与 10 秒缓冲寻优 → 人声伴奏分离 → Applio v2 / RVC 推理 → 淡入淡出 → 8 参数真实卷积混响
- **TTS 快速变声**：`/tts_convert`（路径白名单、时长/体积上限、全局串行排队）
- **常驻推理 Worker**：`rvc_worker.py` 模型热驻留，避免每次翻唱重复加载模型，失败自动回退子进程，空闲超时自动回收内存
- **模型动态发现**：实时扫描 `weights/` 与 `indices/` 自动配对，支持 `.pth` / `.ckpt` / `.onnx` 分离模型热更新
- **完整容灾检查**：启动路径与依赖模型自检 + Docker HEALTHCHECK

## 部署

使用 `Docker Compose + .env`（详见 `README.md`），模型资产通过只读卷挂载，镜像默认指向 `ghcr.io/akinosa/cucumber-applio-gateway:1.0.0`，亦支持从 `Dockerfile` 独立构建。

## 兼容性

| 网关 | 插件 | 说明 |
|---|---|---|
| 1.0.0 | `astrbot_plugin_cucumber_applio_console` 1.0.0+ | 完整支持 Applio 扩展参数、10s 缓冲、TTS 与 8 参数混响 |
| 1.0.0 | `astrbot_plugin_cucumber_rvc_console` 1.0.0+ | `/convert` 与 `/tts_convert` 保持向下兼容 |
