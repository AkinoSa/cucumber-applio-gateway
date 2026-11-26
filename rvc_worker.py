#!/usr/bin/env python
"""RVC 常驻推理 worker（CUSTOM-7.2）。

为什么存在：网关原本每次推理都新起 `infer/cli.py` 子进程，模型/HuBERT/RMVPE
每次都要重新加载（i5-6500 CPU 上固定 ~40s），TTS 这类短音频的加载成本占比极高。
本进程常驻、保持模型在内存，单次推理只剩实际计算（短音频 3~5s）。

协议（stdin/stdout，JSON Lines）：
    请求: {"id": "...", "model": "rym.pth", "input": "...", "output": "...",
           "pitch": 0, "f0_method": "rmvpe", "index": "/path/x.index",
           "index_rate": 0.75, "resample_sr": 0, "rms_mix_rate": 0.35,
           "protect": 0.33, "output_format": "wav", "speaker_id": 0}
    响应: {"id": "...", "ok": true, "elapsed": 3.2} 或 {"id": "...", "ok": false, "error": "..."}

约定：
    * 协议走**复制的 stdout**；进程真正的 stdout 被重定向到 stderr，
      防止 torch/transformers 的杂散 print 破坏 JSON 流。
    * 空闲 IDLE 秒后自动退出（网关检测到 EOF 会在下次请求时重新拉起），
      避免模型长期占用内存。
    * 推理/加载逻辑全部复用 /app/infer/cli.py 的现成函数，行为与子进程完全一致。
"""

import importlib.util
import json
import os
import select
import sys
import time

APP_DIR = os.environ.get("GW_APP_DIR", "/app")
WEIGHTS_DIR = os.environ.get("GW_WEIGHTS_DIR", os.path.join(APP_DIR, "assets", "weights"))
IDLE_EXIT_SECONDS = int(os.environ.get("GW_WORKER_IDLE_SECONDS", "600"))

os.chdir(APP_DIR)
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ["weight_root"] = WEIGHTS_DIR
os.environ.setdefault("outside_index_root", os.path.join(APP_DIR, "assets", "indices"))
os.environ.setdefault("rmvpe_root", os.path.join(APP_DIR, "assets", "rmvpe"))

# 协议通道：复制原 stdout；之后所有杂散输出都进 stderr
_protocol = os.fdopen(os.dup(1), "w", buffering=1)
sys.stdout = sys.stderr


def send(payload: dict) -> None:
    _protocol.write(json.dumps(payload, ensure_ascii=False) + "\n")
    _protocol.flush()


def load_cli_module():
    spec = importlib.util.spec_from_file_location(
        "rvc_cli", os.path.join(APP_DIR, "infer", "cli.py"))
    if not spec or not os.path.exists(os.path.join(APP_DIR, "infer", "cli.py")):
        return None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def ensure_applio_assets():
    """保证 Applio 推理所需的特征提取器与预测器软链接就绪。"""
    applio_dirs = [os.path.join(APP_DIR, "applio"), APP_DIR]
    app_root = next((d for d in applio_dirs if os.path.exists(os.path.join(d, "rvc"))), APP_DIR)
    
    # 1. RMVPE 软链接
    pred_dir = os.path.join(app_root, "rvc", "models", "predictors")
    os.makedirs(pred_dir, exist_ok=True)
    rmvpe_src = os.path.join(APP_DIR, "assets", "rmvpe", "rmvpe.pt")
    rmvpe_dst = os.path.join(pred_dir, "rmvpe.pt")
    if os.path.exists(rmvpe_src) and not os.path.exists(rmvpe_dst):
        try:
            os.symlink(rmvpe_src, rmvpe_dst)
        except Exception:
            pass

    # 2. ContentVec / HuBERT 软链接
    emb_dir = os.path.join(app_root, "rvc", "models", "embedders", "contentvec")
    os.makedirs(emb_dir, exist_ok=True)
    for fname in ("pytorch_model.bin", "config.json"):
        src = os.path.join(APP_DIR, "assets", "hubert_base", fname)
        dst = os.path.join(emb_dir, fname)
        if os.path.exists(src) and not os.path.exists(dst):
            try:
                os.symlink(src, dst)
            except Exception:
                pass


def get_engine():
    try:
        from rvc.infer.infer import VoiceConverter
        return "applio"
    except Exception:
        return "legacy_rvc"


def main() -> int:
    ensure_applio_assets()
    engine = get_engine()
    cli = load_cli_module() if engine == "legacy_rvc" else None
    
    if engine == "applio":
        from rvc.infer.infer import VoiceConverter
        vc = None
        print("[worker] 启动 Applio 原生高性能引擎模式")
    else:
        from infer.vc.modules import VC
        config = cli.create_config()
        vc = None
        print("[worker] 启动原生 RVC 兼容模式")

    cur_model = None
    cur_speaker = None
    send({"ok": True, "event": "ready", "engine": engine})

    stdin = sys.stdin.buffer
    while True:
        ready, _, _ = select.select([stdin], [], [], IDLE_EXIT_SECONDS)
        if not ready:
            send({"ok": True, "event": "idle_exit", "idle": IDLE_EXIT_SECONDS})
            return 0
        line = stdin.readline()
        if not line:
            return 0
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line.decode("utf-8"))
        except Exception as exc:
            send({"id": None, "ok": False, "error": f"bad request: {exc}"})
            continue

        rid = req.get("id")
        started = time.monotonic()
        try:
            model_name = str(req.get("model") or "")
            
            if engine == "applio":
                # Applio 引擎路径解析
                if not model_name.lower().endswith(".pth"):
                    model_file = f"{model_name}.pth"
                else:
                    model_file = model_name
                model_path = os.path.join(WEIGHTS_DIR, model_file)
                if not os.path.exists(model_path):
                    for root, _, files in os.walk(WEIGHTS_DIR):
                        if model_file in files or model_name in files:
                            hit = model_file if model_file in files else model_name
                            model_path = os.path.join(root, hit)
                            break
                
                # 特征索引自动检索与匹配
                index_path = str(req.get("index") or "").strip()
                index_rate = float(req.get("index_rate", 0.75) or 0.75)
                if index_rate > 0:
                    indices_dir = os.environ.get("GW_INDEX_DIR", os.path.join(APP_DIR, "assets", "indices"))
                    if not index_path or not os.path.exists(index_path):
                        stem = os.path.splitext(model_file)[0]
                        candidate = os.path.join(indices_dir, f"{stem}.index")
                        if os.path.exists(candidate):
                            index_path = candidate
                        elif os.path.exists(os.path.join(indices_dir, index_path)):
                            index_path = os.path.join(indices_dir, index_path)
                else:
                    index_path = ""
                
                if vc is None:
                    t0 = time.monotonic()
                    vc = VoiceConverter()
                    print(f"[worker:Applio] VoiceConverter 初始化完成 (耗时 {time.monotonic() - t0:.2f}s)")

                out_file = str(req.get("output"))
                f0_method = str(req.get("f0_method") or "rmvpe").lower()
                pitch = int(req.get("pitch", 0) or 0)
                protect = float(req.get("protect", 0.33) or 0.33)
                volume_envelope = float(req.get("rms_mix_rate", 1.0) or 1.0)
                
                # Applio 特色增强参数
                f0_autotune = bool(req.get("f0_autotune", False))
                f0_autotune_strength = float(req.get("f0_autotune_strength", 1.0) or 1.0)
                formant_shifting = bool(req.get("formant_shifting", False))
                formant_qfrency = float(req.get("formant_qfrency", 1.0) or 1.0)
                formant_timbre = float(req.get("formant_timbre", 1.0) or 1.0)
                cleanup_mode = str(req.get("cleanup_mode", "off") or "off")
                cleanup_strength = float(req.get("cleanup_strength", 12.0) or 12.0)
                export_fmt = str(req.get("output_format") or "wav").upper()

                vc.convert_audio(
                    audio_input_path=str(req.get("input")),
                    audio_output_path=out_file,
                    model_path=str(model_path),
                    index_path=index_path if index_rate > 0 and os.path.exists(index_path) else "",
                    pitch=pitch,
                    f0_method=f0_method,
                    index_rate=index_rate,
                    volume_envelope=volume_envelope,
                    protect=protect,
                    f0_autotune=f0_autotune,
                    f0_autotune_strength=f0_autotune_strength,
                    formant_shifting=formant_shifting,
                    formant_qfrency=formant_qfrency,
                    formant_timbre=formant_timbre,
                    cleanup_mode=cleanup_mode,
                    cleanup_strength=cleanup_strength,
                    export_format=export_fmt,
                )
                cur_model = model_name
                idx_name = os.path.basename(index_path) if index_path else "无"
                status = f"Applio 转换成功 [f0={f0_method}, pitch={pitch}, index={idx_name}, autotune={f0_autotune}, formant={formant_shifting}]"
            else:
                # 原生 RVC 引擎
                model_path = cli.resolve_model(model_name)
                os.environ["weight_root"] = str(model_path.parent)
                speaker_count, speakers = cli.load_model_metadata(model_path)
                speaker_id = cli.select_speaker(speaker_count, speakers, req.get("speaker_id"))

                index_rate = float(req.get("index_rate", 0.0) or 0.0)
                index_path = cli.resolve_index(
                    str(req.get("index") or ""), model_name, speaker_id, index_rate)

                # 模型变化才重新加载（同模型连续请求 0 加载成本）
                if vc is None or cur_model != model_name:
                    t0 = time.monotonic()
                    vc = VC(config)
                    vc.get_vc(model_path.name)
                    cur_model = model_name
                    print("[worker:RVC] 模型已加载: %s（%.1fs）" % (model_name, time.monotonic() - t0))

                status, result = vc.vc_single(
                    speaker_id,
                    str(req.get("input")),
                    int(req.get("pitch", 0) or 0),
                    str(req.get("f0_method") or "rmvpe"),
                    index_path,
                    index_rate,
                    int(req.get("resample_sr", 0) or 0),
                    float(req.get("rms_mix_rate", 1.0) or 1.0),
                    float(req.get("protect", 0.33) or 0.0),
                )
                if not result or result[0] is None or result[1] is None:
                    raise RuntimeError("推理未产出音频: %s" % status)
                cli.write_audio(
                    __import__("pathlib").Path(str(req.get("output"))),
                    result[1], result[0], str(req.get("output_format") or "wav"))

            send({"id": rid, "ok": True,
                  "elapsed": round(time.monotonic() - started, 2),
                  "model_loaded": cur_model == model_name,
                  "engine": engine,
                  "status": str(status)[:200]})
        except Exception as exc:  # noqa: BLE001 — 单请求失败不退出，保持 worker 存活
            send({"id": rid, "ok": False,
                  "error": "%s: %s" % (type(exc).__name__, exc)})
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
