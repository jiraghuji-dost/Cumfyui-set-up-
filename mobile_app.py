"""Mobile-friendly Kaggle batch controller using the official Kaggle CLI."""

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

import gradio as gr

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
OUTPUT_EXTENSIONS = IMAGE_EXTENSIONS | {".gif", ".mp4", ".webm"}
MAX_SOURCE_IMAGE_BYTES = 25 * 1024 * 1024
GENERATION_MODES = {
    "text-to-image", "image-to-image", "image-to-video", "text-to-video"
}
KERNEL_TEMPLATE_ID = "YOUR_KAGGLE_USERNAME/comfyui-batch"
NODE_MAPPING_KEYS = {
    "positive_prompt", "negative_prompt", "seed", "filename_prefix",
    "source_image", "width", "height", "frames", "fps",
    "positive_input", "negative_input", "seed_input", "filename_prefix_input",
    "source_image_input", "width_input", "height_input", "frames_input", "fps_input",
}


def kaggle_env():
    env = os.environ.copy()
    config_dir = env.get("KAGGLE_CONFIG_DIR")
    if config_dir:
        credentials = Path(config_dir).expanduser() / "kaggle.json"
    else:
        credentials = Path.home() / ".kaggle" / "kaggle.json"
    if not credentials.is_file():
        raise RuntimeError(
            f"Kaggle credentials not found at {credentials}. "
            "Configure kaggle.json locally; do not enter credentials in this app."
        )
    return env


def run_kaggle(args, timeout=600):
    env = kaggle_env()
    try:
        result = subprocess.run(
            ["kaggle"] + args,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
    except FileNotFoundError as error:
        raise RuntimeError("Kaggle CLI is missing. Install it with: pip install kaggle") from error
    except subprocess.TimeoutExpired as error:
        raise RuntimeError(f"Kaggle command timed out after {timeout} seconds.") from error
    output = "\n".join(part for part in (result.stdout.strip(), result.stderr.strip()) if part)
    if result.returncode:
        raise RuntimeError(output or f"Kaggle CLI exited with status {result.returncode}.")
    return output or "Kaggle command completed."


def parse_prompts(prompt_text):
    prompts = [line.strip() for line in prompt_text.splitlines() if line.strip()]
    if not prompts:
        raise ValueError("Enter at least one non-empty prompt.")
    return prompts


def parse_node_mappings(node_mappings_text):
    try:
        mappings = json.loads(node_mappings_text)
    except json.JSONDecodeError as error:
        raise ValueError(f"Workflow node mappings must be valid JSON: {error}") from error
    if not isinstance(mappings, dict):
        raise ValueError("Workflow node mappings must be a JSON object keyed by generation mode.")
    normalized = {}
    for mode, mapping in mappings.items():
        if mode not in GENERATION_MODES:
            raise ValueError(f"Unsupported workflow mapping mode: {mode!r}.")
        if not isinstance(mapping, dict):
            raise ValueError(f"Workflow mapping for {mode} must be a JSON object.")
        normalized_mapping = {}
        for key, value in mapping.items():
            if key not in NODE_MAPPING_KEYS:
                raise ValueError(f"Unsupported workflow node mapping: {key!r}.")
            if key.endswith("_input"):
                if not isinstance(value, str) or not value.strip():
                    raise ValueError(f"Workflow input name for {key} must be a non-empty string.")
                normalized_mapping[key] = value.strip()
            elif isinstance(value, (str, int)) and not isinstance(value, bool):
                node_id = str(value).strip()
                if not node_id:
                    raise ValueError(f"Workflow node ID for {key} must not be empty.")
                normalized_mapping[key] = node_id
            else:
                raise ValueError(f"Workflow node ID for {key} must be a string or integer.")
        normalized[mode] = normalized_mapping
    return normalized


def update_dataset_and_push(assets_dataset_slug, jobs_dataset_slug, kernel_slug,
                            kernel_dir, mode, prompt_text, negative_prompt,
                            starting_seed, source_image, width, height, frames,
                            node_mappings_text):
    assets_dataset_slug = assets_dataset_slug.strip()
    jobs_dataset_slug = jobs_dataset_slug.strip()
    kernel_slug = kernel_slug.strip()
    if mode not in GENERATION_MODES:
        raise ValueError("Select a supported generation mode.")
    if not kernel_dir.strip():
        raise ValueError("Select the local kernel source folder.")
    kernel_path = Path(kernel_dir).expanduser()
    for label, slug in (
        ("Model assets dataset", assets_dataset_slug),
        ("Job inputs dataset", jobs_dataset_slug),
    ):
        if not slug or "/" not in slug:
            raise ValueError(f"{label} slug must use the form username/dataset-name.")
    if assets_dataset_slug == jobs_dataset_slug:
        raise ValueError("Use a separate small jobs dataset; do not version the model assets dataset.")
    if not kernel_slug or "/" not in kernel_slug:
        raise ValueError("Kernel slug must use the form username/kernel-name.")
    if not kernel_path.is_dir() or not (kernel_path / "kernel-metadata.json").is_file():
        raise ValueError("Kernel folder must exist and contain kernel-metadata.json.")
    metadata = json.loads((kernel_path / "kernel-metadata.json").read_text(encoding="utf-8"))
    if not isinstance(metadata, dict):
        raise ValueError("kernel-metadata.json must contain a JSON object.")
    if metadata.get("id") not in (kernel_slug, KERNEL_TEMPLATE_ID):
        raise ValueError(
            "Kernel metadata id must match the entered kernel slug or use the included template."
        )
    code_file = metadata.get("code_file")
    if not isinstance(code_file, str) or not code_file.strip():
        raise ValueError("kernel-metadata.json must set code_file.")
    if not (kernel_path / code_file).is_file():
        raise ValueError(f"Kernel code file does not exist: {code_file}")

    prompts = parse_prompts(prompt_text)
    if mode in ("image-to-image", "image-to-video") and not source_image:
        raise ValueError(f"{mode} requires a source image.")
    source_path = Path(source_image).expanduser() if source_image else None
    if source_path:
        if source_path.suffix.lower() not in IMAGE_EXTENSIONS:
            raise ValueError("Source image must be PNG, JPG, JPEG, or WEBP.")
        if not source_path.is_file():
            raise ValueError("Selected source image could not be found.")
        if source_path.stat().st_size > MAX_SOURCE_IMAGE_BYTES:
            raise ValueError("Source image must be smaller than 25 MB.")
    try:
        numeric_values = [float(value) for value in (width, height, frames)]
    except (TypeError, ValueError) as error:
        raise ValueError("Width, height, and video frame count must be whole numbers.") from error
    if any(not value.is_integer() for value in numeric_values):
        raise ValueError("Width, height, and video frame count must be whole numbers.")
    width, height, frames = (int(value) for value in numeric_values)
    if not 64 <= width <= 1024 or not 64 <= height <= 1024:
        raise ValueError("Width and height must be between 64 and 1024 pixels.")
    if mode in ("image-to-video", "text-to-video") and (width % 32 or height % 32):
        raise ValueError("LTX-Video width and height must be multiples of 32.")
    if width % 8 or height % 8:
        raise ValueError("Width and height must be multiples of 8.")
    if mode in ("image-to-video", "text-to-video") and (
        not 9 <= frames <= 49 or (frames - 1) % 8
    ):
        raise ValueError("LTX-Video frame count must be 9, 17, 25, 33, 41, or 49.")
    try:
        seed = int(starting_seed) if starting_seed.strip() else None
    except ValueError as error:
        raise ValueError("Starting seed must be a whole number or blank.") from error
    workflow_nodes_by_mode = parse_node_mappings(node_mappings_text)
    mode_nodes = workflow_nodes_by_mode.get(mode, {})
    if "positive_prompt" not in mode_nodes:
        raise ValueError(f"Positive prompt node ID is required for the {mode} workflow.")
    if "width" not in mode_nodes or "height" not in mode_nodes:
        raise ValueError(f"Width and height node IDs are required for the {mode} workflow.")
    if mode in ("image-to-video", "text-to-video") and "frames" not in mode_nodes:
        raise ValueError(f"A video frame-count node ID is required for the {mode} workflow.")
    if negative_prompt.strip() and "negative_prompt" not in mode_nodes:
        raise ValueError(f"Negative prompt node ID is required for the {mode} workflow.")
    if seed is not None and "seed" not in mode_nodes:
        raise ValueError(f"Seed node ID is required for the {mode} workflow.")
    if source_path and "source_image" not in mode_nodes:
        raise ValueError(f"Source image node ID is required for the {mode} workflow.")
    queue = {
        "workflow_nodes_by_mode": workflow_nodes_by_mode,
        "defaults": {
            "width": width,
            "height": height,
            "frames": frames,
        },
        "prompts": [
            {
                "mode": mode,
                "prompt": prompt,
                **({"seed": seed + index} if seed is not None else {}),
            }
            for index, prompt in enumerate(prompts)
        ],
    }
    if negative_prompt.strip():
        queue["defaults"]["negative_prompt"] = negative_prompt
    if source_path:
        input_name = f"source_{uuid.uuid4().hex}{source_path.suffix.lower()}"
        for scene in queue["prompts"]:
            scene["input_image"] = f"inputs/{input_name}"

    with tempfile.TemporaryDirectory(prefix="comfyui-kaggle-") as temp_dir:
        staging = Path(temp_dir)
        yield "Checking the private jobs dataset metadata..."
        run_kaggle(
            ["datasets", "metadata", "-p", str(staging), jobs_dataset_slug],
            timeout=180,
        )
        metadata_path = staging / "dataset-metadata.json"
        if not metadata_path.is_file():
            raise RuntimeError("Kaggle did not return dataset metadata; refusing to create a version.")
        dataset_meta = json.loads(metadata_path.read_text(encoding="utf-8"))
        if (
            not isinstance(dataset_meta, dict)
            or dataset_meta.get("id") != jobs_dataset_slug
            or not dataset_meta.get("licenses")
        ):
            raise RuntimeError(
                "Downloaded Kaggle dataset metadata is incomplete or has a different dataset id."
            )
        if dataset_meta.get("isPrivate") is not True:
            raise RuntimeError(
                "The jobs dataset is not confirmed private. Make it private before uploading inputs."
            )
        yield "Downloading the existing jobs dataset..."
        run_kaggle(
            ["datasets", "download", "-d", jobs_dataset_slug, "-p", str(staging), "--unzip"],
            timeout=6 * 60 * 60,
        )
        inputs_dir = staging / "inputs"
        if inputs_dir.exists():
            shutil.rmtree(inputs_dir)
        if source_path:
            inputs_dir.mkdir(parents=True)
            shutil.copyfile(source_path, inputs_dir / input_name)
        (staging / "prompts_queue.json").write_text(
            json.dumps(queue, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        yield "Uploading this job's prompt queue and source image..."
        dataset_result = run_kaggle([
            "datasets", "version", "-p", str(staging),
            "-m", f"Submit {mode} job ({len(prompts)} prompt(s))",
        ])

    with tempfile.TemporaryDirectory(prefix="comfyui-kernel-") as temp_dir:
        push_path = Path(temp_dir) / "kernel"
        shutil.copytree(kernel_path, push_path)
        push_metadata_path = push_path / "kernel-metadata.json"
        push_metadata = json.loads(push_metadata_path.read_text(encoding="utf-8"))
        push_metadata["id"] = kernel_slug
        data_sources = push_metadata.get("dataset_sources", [])
        if not isinstance(data_sources, list):
            raise ValueError("kernel-metadata.json dataset_sources must be a list.")
        for dataset_source in (assets_dataset_slug, jobs_dataset_slug):
            if dataset_source not in data_sources:
                data_sources.append(dataset_source)
        push_metadata["dataset_sources"] = data_sources
        push_metadata["is_private"] = True
        push_metadata["enable_gpu"] = True
        push_metadata_path.write_text(
            json.dumps(push_metadata, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        yield "Pushing the private GPU kernel..."
        kernel_result = run_kaggle(["kernels", "push", "-p", str(push_path)])
    yield (
        f"Job inputs uploaded ({len(prompts)} prompt(s), {mode}).\n{dataset_result}\n\n"
        f"Kernel push submitted.\n{kernel_result}"
    )


def check_status(kernel_slug):
    if "/" not in kernel_slug.strip():
        raise ValueError("Kernel slug must use the form username/kernel-name.")
    return run_kaggle(["kernels", "status", kernel_slug.strip()], timeout=180)


def monitor_status(kernel_slug):
    kernel_slug = kernel_slug.strip()
    if "/" not in kernel_slug:
        raise ValueError("Kernel slug must use the form username/kernel-name.")
    deadline = time.monotonic() + 6 * 60 * 60
    yield "Monitoring Kaggle kernel status..."
    while time.monotonic() < deadline:
        status = run_kaggle(["kernels", "status", kernel_slug], timeout=180)
        yield status
        match = re.search(r"\b(RUNNING|COMPLETE|ERROR|CANCELLED)\b", status, re.IGNORECASE)
        if match and match.group(1).upper() != "RUNNING":
            return
        time.sleep(15)
    raise TimeoutError("Stopped monitoring after six hours; check the kernel in Kaggle.")


def download_outputs(kernel_slug):
    if "/" not in kernel_slug.strip():
        raise ValueError("Kernel slug must use the form username/kernel-name.")
    output_dir = Path.home() / "comfyui_gallery" / (
        time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    yield "Downloading generated files from Kaggle..."
    result = run_kaggle([
        "kernels", "output", kernel_slug.strip(), "-p", str(output_dir), "-q",
    ], timeout=1800)
    files = sorted(
        str(path) for path in output_dir.rglob("*")
        if path.is_file() and path.suffix.lower() in OUTPUT_EXTENSIONS
    )
    if not files:
        raise RuntimeError(
            "Kaggle reported success but no supported output files were downloaded. "
            "Check the kernel run and its SaveImage/SaveVideo workflow nodes."
        )
    images = [path for path in files if Path(path).suffix.lower() in IMAGE_EXTENSIONS | {".gif"}]
    yield result + f"\nSaved to: {output_dir}", images, files


CSS = """
footer { visibility: hidden; }
.gradio-container { max-width: 760px !important; margin: auto; }
"""

with gr.Blocks(title="My AIAgent · Kaggle") as app:
    gr.Markdown(
        "# My AIAgent · Kaggle\n"
        "Send image/video jobs to your private Kaggle GPU kernel and download the outputs here."
    )
    with gr.Accordion("Kaggle configuration", open=True):
        dataset = gr.Textbox(label="Private model assets dataset", placeholder="username/comfyui-assets")
        jobs_dataset = gr.Textbox(
            label="Small private jobs dataset (separate from model files)",
            placeholder="username/comfyui-jobs",
        )
        kernel = gr.Textbox(label="Kernel slug", placeholder="username/comfyui-batch")
        kernel_folder = gr.Textbox(
            label="Local kernel source folder",
            value=str(Path(__file__).resolve().parent / "kaggle_kernel"),
        )
        gr.Markdown(
            "Configure Kaggle API credentials on this device once. The model dataset is never "
            "downloaded or re-uploaded for each job; only the small jobs dataset is versioned. "
            "The bundled kernel uses the ComfyUI Docker image selected in Kaggle. Never paste "
            "your Kaggle API token here."
        )
    mode = gr.Dropdown(
        choices=[
            ("Text to Image", "text-to-image"),
            ("Image to Image", "image-to-image"),
            ("Image to Video", "image-to-video"),
            ("Text to Video", "text-to-video"),
        ],
        value="text-to-image",
        label="Generation mode",
    )
    prompts = gr.Textbox(
        label="Prompt (one per line for multiple images)", lines=5,
        placeholder="A cinematic mountain landscape",
    )
    negative = gr.Textbox(label="Negative prompt (optional)", lines=2)
    seed = gr.Textbox(label="Starting seed (optional; increments per prompt)", placeholder="1234")
    source_image = gr.File(
        label="Source image (required for Image to Image / Image to Video)",
        file_types=["image"],
        type="filepath",
    )
    with gr.Row():
        width = gr.Number(label="Width", value=512, precision=0)
        height = gr.Number(label="Height", value=512, precision=0)
        frames = gr.Number(label="Video frames (LTX-Video)", value=17, precision=0)
    with gr.Accordion("API workflow node IDs (configure once per workflow)", open=False):
        node_mappings = gr.Textbox(
            label="Node mappings (JSON; replace the example IDs with IDs from each API workflow)",
            lines=13,
            value=json.dumps({
                "text-to-image": {
                    "positive_prompt": "6", "negative_prompt": "7", "seed": "3",
                    "filename_prefix": "9", "width": "5", "height": "5",
                },
                "image-to-image": {
                    "positive_prompt": "6", "negative_prompt": "7", "seed": "3",
                    "filename_prefix": "9", "source_image": "10",
                    "width": "5", "height": "5",
                },
                "image-to-video": {
                    "positive_prompt": "6", "negative_prompt": "7", "seed": "72",
                    "seed_input": "noise_seed", "filename_prefix": "81",
                    "source_image": "78", "width": "77", "height": "77", "frames": "77",
                },
                "text-to-video": {
                    "positive_prompt": "6", "negative_prompt": "7", "seed": "72",
                    "seed_input": "noise_seed", "filename_prefix": "79",
                    "width": "70", "height": "70", "frames": "70",
                },
            }, indent=2),
        )
        gr.Markdown(
            "Workflows belong in the private model assets dataset: `workflow_api.json`, "
            "`workflow_image_to_image_api.json`, `workflow_image_to_video_api.json`, and "
            "`workflow_text_to_video_api.json`. "
            "Input names default to ComfyUI's `text`, `image`, `width`, `height`, and `length`."
        )
    submit = gr.Button("Generate on Kaggle", variant="primary")
    status_text = gr.Textbox(label="Activity", lines=8, interactive=False)
    with gr.Row():
        status_button = gr.Button("Check status")
        monitor_button = gr.Button("Monitor run")
        download_button = gr.Button("Get images/videos on phone")
    gallery = gr.Gallery(label="Downloaded images", columns=2, height="auto")
    output_files = gr.File(label="Save generated files to phone", file_count="multiple")

    submit.click(
        update_dataset_and_push,
        inputs=[dataset, jobs_dataset, kernel, kernel_folder, mode, prompts, negative, seed,
                source_image, width, height, frames, node_mappings],
        outputs=status_text,
    )
    status_button.click(check_status, inputs=kernel, outputs=status_text)
    monitor_button.click(monitor_status, inputs=kernel, outputs=status_text)
    download_button.click(
        download_outputs, inputs=kernel, outputs=[status_text, gallery, output_files]
    )

if __name__ == "__main__":
    app.queue().launch(
        server_name="127.0.0.1", share=False, theme=gr.themes.Soft(), css=CSS
    )
