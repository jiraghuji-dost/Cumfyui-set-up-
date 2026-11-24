#!/usr/bin/env python3
"""Submit a finite prompt queue to the local ComfyUI API and exit."""

import json
import logging
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

COMFY_URL = "http://127.0.0.1:8188"
COMFY_DIR = Path(__file__).resolve().parent
WORKFLOW_PATH = COMFY_DIR / "workflow_api.json"
QUEUE_PATH = COMFY_DIR / "prompts_queue.json"
STARTUP_TIMEOUT = 120
JOB_TIMEOUT = 60 * 60
POLL_INTERVAL = 2

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
LOGGER = logging.getLogger("kaggle_runner")


def request_json(path, payload=None, timeout=30):
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        COMFY_URL + path,
        data=data,
        headers={"Content-Type": "application/json"} if data is not None else {},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read()
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"ComfyUI returned HTTP {error.code} for {path}: {detail}") from error
    except urllib.error.URLError as error:
        raise RuntimeError(f"Could not reach ComfyUI at {COMFY_URL}: {error}") from error
    if not body:
        return {}
    try:
        return json.loads(body)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"ComfyUI returned invalid JSON for {path}") from error


def wait_for_server():
    deadline = time.monotonic() + STARTUP_TIMEOUT
    while time.monotonic() < deadline:
        try:
            request_json("/system_stats", timeout=5)
            LOGGER.info("ComfyUI is ready.")
            return
        except (RuntimeError, TimeoutError):
            time.sleep(2)
    raise TimeoutError(f"ComfyUI did not become ready within {STARTUP_TIMEOUT} seconds.")


def load_json(path):
    try:
        with path.open(encoding="utf-8") as file:
            return json.load(file)
    except FileNotFoundError as error:
        raise RuntimeError(f"Required file is missing: {path}") from error
    except json.JSONDecodeError as error:
        raise RuntimeError(f"Invalid JSON in {path}: {error}") from error


def normalize_queue(queue):
    if isinstance(queue, list):
        return {}, {}, queue
    if not isinstance(queue, dict):
        raise RuntimeError("prompts_queue.json must contain an object or a list.")
    prompts = queue.get("prompts")
    if not isinstance(prompts, list) or not prompts:
        raise RuntimeError("prompts_queue.json must contain a non-empty 'prompts' list.")
    defaults = queue.get("defaults", {})
    nodes = queue.get("workflow_nodes", {})
    if not isinstance(defaults, dict) or not isinstance(nodes, dict):
        raise RuntimeError("'defaults' and 'workflow_nodes' must be JSON objects.")
    return defaults, nodes, prompts


def set_node_input(workflow, node_id, input_name, value, setting):
    if node_id is None:
        return
    node = workflow.get(str(node_id))
    if not isinstance(node, dict):
        raise RuntimeError(f"Workflow node {node_id!r} for {setting} does not exist.")
    inputs = node.get("inputs")
    if not isinstance(inputs, dict) or input_name not in inputs:
        raise RuntimeError(
            f"Workflow node {node_id!r} has no {input_name!r} input for {setting}."
        )
    inputs[input_name] = value


def safe_prefix(text, index):
    slug = re.sub(r"[^A-Za-z0-9_-]+", "_", text).strip("_")[:48]
    return f"batch_{index:04d}_{slug or 'prompt'}"


def prepare_prompt(base_workflow, scene, defaults, default_nodes, index):
    if not isinstance(scene, dict):
        raise RuntimeError(f"Prompt entry {index} must be a JSON object.")
    prompt = scene.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        raise RuntimeError(f"Prompt entry {index} must have a non-empty 'prompt'.")

    workflow = json.loads(json.dumps(base_workflow))
    nodes = dict(default_nodes)
    scene_nodes = scene.get("workflow_nodes", {})
    if not isinstance(scene_nodes, dict):
        raise RuntimeError(f"workflow_nodes in prompt entry {index} must be an object.")
    nodes.update(scene_nodes)

    positive = nodes.get("positive_prompt")
    if positive is None:
        raise RuntimeError(
            "Set workflow_nodes.positive_prompt to the positive text node ID in prompts_queue.json."
        )
    set_node_input(workflow, positive, nodes.get("positive_input", "text"), prompt.strip(),
                   "positive_prompt")

    negative = scene.get("negative_prompt", defaults.get("negative_prompt"))
    if negative is not None:
        negative_node = nodes.get("negative_prompt")
        if negative_node is None:
            raise RuntimeError(
                "A negative prompt was provided but workflow_nodes.negative_prompt is not set."
            )
        set_node_input(workflow, negative_node, nodes.get("negative_input", "text"),
                       negative, "negative_prompt")

    seed = scene.get("seed", defaults.get("seed"))
    if seed is not None:
        seed_node = nodes.get("seed")
        if seed_node is None:
            raise RuntimeError("A seed was provided but workflow_nodes.seed is not set.")
        if isinstance(seed, bool) or not isinstance(seed, int):
            raise RuntimeError(f"Seed for prompt entry {index} must be an integer.")
        set_node_input(workflow, seed_node, nodes.get("seed_input", "seed"), seed, "seed")

    prefix_node = nodes.get("filename_prefix")
    if prefix_node is not None:
        set_node_input(
            workflow, prefix_node, nodes.get("filename_prefix_input", "filename_prefix"),
            safe_prefix(prompt, index), "filename_prefix",
        )
    return workflow


def wait_for_prompt(prompt_id, index):
    deadline = time.monotonic() + JOB_TIMEOUT
    while time.monotonic() < deadline:
        history = request_json(f"/history/{prompt_id}")
        result = history.get(prompt_id)
        if result is not None:
            status = result.get("status", {})
            if status.get("status_str") == "error":
                raise RuntimeError(f"ComfyUI reported an error for prompt {index}: {status}")
            LOGGER.info("Prompt %s completed.", index)
            return
        time.sleep(POLL_INTERVAL)
    raise TimeoutError(f"Prompt {index} ({prompt_id}) exceeded {JOB_TIMEOUT} seconds.")


def main():
    wait_for_server()
    base_workflow = load_json(WORKFLOW_PATH)
    if not isinstance(base_workflow, dict) or not base_workflow:
        raise RuntimeError("workflow_api.json must contain a non-empty API workflow object.")
    defaults, node_mapping, prompts = normalize_queue(load_json(QUEUE_PATH))

    LOGGER.info("Processing %s prompt(s) sequentially.", len(prompts))
    for index, scene in enumerate(prompts, start=1):
        workflow = prepare_prompt(base_workflow, scene, defaults, node_mapping, index)
        response = request_json("/prompt", {"prompt": workflow})
        prompt_id = response.get("prompt_id")
        if not prompt_id:
            raise RuntimeError(f"ComfyUI did not return a prompt_id: {response}")
        LOGGER.info("Submitted prompt %s/%s (%s).", index, len(prompts), prompt_id)
        wait_for_prompt(prompt_id, index)
    LOGGER.info("All queued prompts completed.")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        LOGGER.error("%s", error)
        sys.exit(1)
    sys.exit(0)
