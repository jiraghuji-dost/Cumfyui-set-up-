"""Mobile-friendly Kaggle batch controller using the official Kaggle CLI."""

import json
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path

import gradio as gr


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


def update_dataset_and_push(dataset_slug, kernel_slug, kernel_dir, prompt_text,
                            negative_prompt, starting_seed, positive_node,
                            negative_node, seed_node, prefix_node):
    dataset_slug = dataset_slug.strip()
    kernel_slug = kernel_slug.strip()
    if not kernel_dir.strip():
        raise ValueError("Select the local kernel source folder.")
    kernel_path = Path(kernel_dir).expanduser()
    if not dataset_slug or "/" not in dataset_slug:
        raise ValueError("Dataset slug must use the form username/dataset-name.")
    if not kernel_slug or "/" not in kernel_slug:
        raise ValueError("Kernel slug must use the form username/kernel-name.")
    if not kernel_path.is_dir() or not (kernel_path / "kernel-metadata.json").is_file():
        raise ValueError("Kernel folder must exist and contain kernel-metadata.json.")
    metadata = json.loads((kernel_path / "kernel-metadata.json").read_text(encoding="utf-8"))
    if not isinstance(metadata, dict):
        raise ValueError("kernel-metadata.json must contain a JSON object.")
    if metadata.get("id") != kernel_slug:
        raise ValueError(
            f"Kernel metadata id must match the entered kernel slug ({kernel_slug})."
        )

    prompts = parse_prompts(prompt_text)
    try:
        seed = int(starting_seed) if starting_seed.strip() else None
    except ValueError as error:
        raise ValueError("Starting seed must be a whole number or blank.") from error
    workflow_nodes = {}
    for key, value in (
        ("positive_prompt", positive_node),
        ("negative_prompt", negative_node),
        ("seed", seed_node),
        ("filename_prefix", prefix_node),
    ):
        if value.strip():
            workflow_nodes[key] = value.strip()
    if "positive_prompt" not in workflow_nodes:
        raise ValueError("Positive prompt node ID is required for the selected API workflow.")
    if negative_prompt.strip() and "negative_prompt" not in workflow_nodes:
        raise ValueError("Enter the negative prompt node ID, or leave the negative prompt blank.")
    if seed is not None and "seed" not in workflow_nodes:
        raise ValueError("Enter the seed node ID, or leave the starting seed blank.")

    queue = {
        "workflow_nodes": workflow_nodes,
        "prompts": [
            {"prompt": prompt, **({"seed": seed + index} if seed is not None else {})}
            for index, prompt in enumerate(prompts)
        ],
    }
    if negative_prompt.strip():
        queue["defaults"] = {"negative_prompt": negative_prompt}

    with tempfile.TemporaryDirectory(prefix="comfyui-kaggle-") as temp_dir:
        staging = Path(temp_dir)
        run_kaggle(
            ["datasets", "metadata", "-p", str(staging), dataset_slug],
            timeout=180,
        )
        metadata_path = staging / "dataset-metadata.json"
        if not metadata_path.is_file():
            raise RuntimeError("Kaggle did not return dataset metadata; refusing to create a version.")
        dataset_meta = json.loads(metadata_path.read_text(encoding="utf-8"))
        if (
            not isinstance(dataset_meta, dict)
            or dataset_meta.get("id") != dataset_slug
            or not dataset_meta.get("licenses")
        ):
            raise RuntimeError(
                "Downloaded Kaggle dataset metadata is incomplete or has a different dataset id."
            )
        run_kaggle(
            ["datasets", "download", "-d", dataset_slug, "-p", str(staging), "--unzip"],
            timeout=6 * 60 * 60,
        )
        (staging / "prompts_queue.json").write_text(
            json.dumps(queue, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        dataset_result = run_kaggle([
            "datasets", "version", "-p", str(staging),
            "-m", "Update ComfyUI prompt queue",
        ])

    kernel_result = run_kaggle(["kernels", "push", "-p", str(kernel_path)])
    return (
        f"Dataset version submitted ({len(prompts)} prompts).\n{dataset_result}\n\n"
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
    output_dir = Path.home() / "comfyui_gallery"
    output_dir.mkdir(parents=True, exist_ok=True)
    result = run_kaggle([
        "kernels", "output", kernel_slug.strip(), "-p", str(output_dir), "-q",
    ], timeout=1800)
    files = sorted(
        str(path) for path in output_dir.rglob("*")
        if path.is_file() and path.suffix.lower() in {
            ".png", ".jpg", ".jpeg", ".webp", ".gif", ".mp4", ".webm"
        }
    )
    return result + f"\nSaved to: {output_dir}", files


CSS = """
footer { visibility: hidden; }
.gradio-container { max-width: 760px !important; margin: auto; }
"""

with gr.Blocks(theme=gr.themes.Soft(), css=CSS, title="ComfyUI Batch Controller") as app:
    gr.Markdown("# ComfyUI Batch Controller\nQueue prompts and control your private Kaggle kernel.")
    with gr.Accordion("Kaggle configuration", open=True):
        dataset = gr.Textbox(label="Private dataset slug", placeholder="username/comfyui-assets")
        kernel = gr.Textbox(label="Kernel slug", placeholder="username/comfyui-batch")
        kernel_folder = gr.Textbox(
            label="Local kernel source folder",
            placeholder="/path/to/kernel-folder (contains kernel-metadata.json)",
        )
        gr.Markdown(
            "Configure Kaggle API credentials on this device before use. "
            "Never paste your Kaggle API token here."
        )
    prompts = gr.Textbox(
        label="Prompts (one per line)", lines=7,
        placeholder="A cinematic mountain landscape\nA quiet forest in autumn",
    )
    negative = gr.Textbox(label="Negative prompt (optional)", lines=2)
    seed = gr.Textbox(label="Starting seed (optional; increments per prompt)", placeholder="1234")
    with gr.Accordion("API workflow node IDs", open=True):
        positive_node = gr.Textbox(label="Positive prompt node ID (required)", placeholder="6")
        negative_node = gr.Textbox(label="Negative prompt node ID (optional)", placeholder="7")
        seed_node = gr.Textbox(label="Seed node ID (optional)", placeholder="3")
        prefix_node = gr.Textbox(label="Filename prefix node ID (optional)", placeholder="9")
    submit = gr.Button("Update dataset + push kernel", variant="primary")
    status_text = gr.Textbox(label="Activity", lines=8, interactive=False)
    with gr.Row():
        status_button = gr.Button("Check status")
        monitor_button = gr.Button("Monitor run")
        download_button = gr.Button("Download outputs")
    gallery = gr.Gallery(label="Downloaded outputs", columns=2, height="auto")

    submit.click(
        update_dataset_and_push,
        inputs=[dataset, kernel, kernel_folder, prompts, negative, seed, positive_node,
                negative_node, seed_node, prefix_node],
        outputs=status_text,
    )
    status_button.click(check_status, inputs=kernel, outputs=status_text)
    monitor_button.click(monitor_status, inputs=kernel, outputs=status_text)
    download_button.click(download_outputs, inputs=kernel, outputs=[status_text, gallery])

if __name__ == "__main__":
    app.queue().launch(server_name="127.0.0.1", share=False)
