#!/bin/bash
set -e

COMFY_DIR="/opt/ComfyUI"
DATASET_DIR="/kaggle/input/comfyui-assets"
OUTPUT_DIR="/kaggle/working/outputs"

mkdir -p "$OUTPUT_DIR"

if [ -d "$DATASET_DIR/models" ]; then
    echo "[INFO] Linking persistent models from Kaggle Dataset..."
    for folder in checkpoints vae loras controlnet animatediff_models upscale_models; do
        source="$DATASET_DIR/models/$folder"
        target="$COMFY_DIR/models/$folder"
        if [ -d "$source" ]; then
            if [ -e "$target" ] || [ -L "$target" ]; then
                rm -rf "$target"
            fi
            ln -s "$source" "$target"
        fi
    done
fi

if [ -f "$DATASET_DIR/workflow_api.json" ]; then
    cp "$DATASET_DIR/workflow_api.json" "$COMFY_DIR/workflow_api.json"
fi
if [ -f "$DATASET_DIR/prompts_queue.json" ]; then
    cp "$DATASET_DIR/prompts_queue.json" "$COMFY_DIR/prompts_queue.json"
fi

echo "[INFO] Starting ComfyUI server on localhost..."
python3 "$COMFY_DIR/main.py" --listen 127.0.0.1 --port 8188 --output-directory "$OUTPUT_DIR" &
COMFY_PID=$!

cleanup() {
    kill "$COMFY_PID" 2>/dev/null || true
    wait "$COMFY_PID" 2>/dev/null || true
}
trap cleanup EXIT

set +e
python3 "$COMFY_DIR/kaggle_runner.py"
RUNNER_STATUS=$?
set -e

echo "[INFO] Batch processor finished with status $RUNNER_STATUS; stopping ComfyUI."
exit "$RUNNER_STATUS"
