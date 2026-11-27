#!/bin/bash
set -e

COMFY_DIR="/opt/ComfyUI"
DATASET_DIR="/kaggle/input/comfyui-assets"
JOBS_DIR="/kaggle/input/comfyui-jobs"
OUTPUT_DIR="/kaggle/working/outputs"

mkdir -p "$OUTPUT_DIR"

if [ -d "$DATASET_DIR/models" ]; then
    echo "[INFO] Linking persistent models from Kaggle Dataset..."
    for folder in checkpoints vae loras controlnet animatediff_models upscale_models diffusion_models text_encoders clip; do
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

for workflow in workflow_api.json workflow_image_to_image_api.json workflow_image_to_video_api.json workflow_text_to_video_api.json; do
    if [ -f "$DATASET_DIR/$workflow" ]; then
        cp "$DATASET_DIR/$workflow" "$COMFY_DIR/$workflow"
    fi
done
if [ -f "$JOBS_DIR/prompts_queue.json" ]; then
    cp "$JOBS_DIR/prompts_queue.json" "$COMFY_DIR/prompts_queue.json"
elif [ -f "$DATASET_DIR/prompts_queue.json" ]; then
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
