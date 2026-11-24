# ComfyUI Kaggle Batch Pipeline

This repository builds a ComfyUI image for finite, sequential batch jobs on Kaggle. ComfyUI listens only on `127.0.0.1`; there is no tunnel, reverse proxy, or public ComfyUI endpoint. The runner exits after its queue is complete, and the launcher then stops ComfyUI.

> Kaggle policy compliance depends on your use case and the current Kaggle terms; no image or script can guarantee that a particular use is allowed. Check Kaggle's current Acceptable Use Policy and notebook rules before running jobs. Do not use this setup to evade session limits or keep a session alive.

## 1. Build the image

### Build locally

Install Docker, then run these commands from this repository:

```sh
docker build -t YOUR_DOCKER_ID/kaggle-comfyui:latest .
docker login -u YOUR_DOCKER_ID
docker push YOUR_DOCKER_ID/kaggle-comfyui:latest
```

`docker login` prompts for your Docker Hub PAT; enter it at the prompt, not in chat, source code, or shell history. The image contains ComfyUI and the listed custom nodes, but **no model weights**.

### Build with GitHub Actions (no local Docker installation)

In the GitHub repository, open **Settings → Secrets and variables → Actions** and add:

- `DOCKERHUB_USERNAME`: your Docker Hub ID
- `DOCKERHUB_TOKEN`: a Docker Hub access token with permission to push images

Do not put the token in repository files or chat. After pushing the pipeline files to GitHub and adding both secrets, start the workflow under the repository's **Actions** tab → **Build and publish ComfyUI image** → **Run workflow**. It will publish `YOUR_DOCKER_ID/kaggle-comfyui:latest` and a commit-specific tag. The workflow is manual-only so it will not try to publish before you have configured the secrets.

## 2. Prepare Kaggle assets

Create a **private** Kaggle dataset with the slug `YOUR_KAGGLE_USERNAME/comfyui-assets` (or change `DATASET_DIR` in `start.sh`). Include:

```text
comfyui-assets/
  dataset-metadata.json
  prompts_queue.json
  workflow_api.json
  models/
    checkpoints/
    vae/
    loras/
    controlnet/
    animatediff_models/
    upscale_models/
```

Only include model folders that your workflow needs. `workflow_api.json` must be a ComfyUI API-format workflow (export it using ComfyUI's **Save (API Format)** option), not the regular UI workflow format.

The queue format is:

```json
{
  "workflow_nodes": {
    "positive_prompt": "6",
    "negative_prompt": "7",
    "seed": "3",
    "filename_prefix": "9"
  },
  "defaults": {
    "negative_prompt": "",
    "seed": 1234
  },
  "prompts": [
    {"prompt": "A calm mountain lake at sunrise"},
    {"prompt": "A quiet forest in autumn", "seed": 5678}
  ]
}
```

Replace the example node IDs with IDs from your own API workflow. The positive prompt node is required. Add a mapping for any optional setting you want to change; for custom input names, use `positive_input`, `negative_input`, `seed_input`, or `filename_prefix_input` (the defaults are `text`, `text`, `seed`, and `filename_prefix`). A top-level JSON list of prompt objects is also accepted.

Kaggle mounts attached datasets read-only under `/kaggle/input`. Attach the private dataset to your private Notebook so its mount path is `/kaggle/input/comfyui-assets`. When updating dataset versions with the phone app, verify that your Kaggle dataset still contains all model files; keep a full backup of large assets before changing dataset versions.

## 3. Configure the Kaggle Notebook

Create a private Kaggle Notebook and attach the private dataset. In the Notebook settings:

1. Enable a GPU if your workflow requires it.
2. Select your pushed image (`YOUR_DOCKER_ID/kaggle-comfyui:latest`) in Kaggle's custom Docker image setting, where available for your account/runtime.
3. Ensure the notebook runs the image's default command (`/opt/ComfyUI/start.sh`) and has the attached dataset available at the path above.
4. Keep Internet disabled unless your workload has a legitimate need and Kaggle permits it. The batch image does not download model weights.

Custom image settings are managed in Kaggle Notebook settings; a `kaggle kernels push` does not itself configure or guarantee that setting. Test the notebook once in Kaggle before relying on phone-triggered pushes.

## 4. Trigger and monitor from Android

In Pydroid 3 (or another Python environment), install the dependencies:

```sh
pip install gradio kaggle
```

Create Kaggle API credentials in Kaggle **Account Settings → API** and store `kaggle.json` at `~/.kaggle/kaggle.json` (or set `KAGGLE_CONFIG_DIR` to its containing directory). Keep this file private and never paste its contents into the app or chat.

Run `python mobile_app.py`, open the local address it prints on the phone, and enter:

- Dataset slug, for example `YOUR_KAGGLE_USERNAME/comfyui-assets`
- Kernel slug, for example `YOUR_KAGGLE_USERNAME/comfyui-batch`
- A local folder containing that kernel's `kernel-metadata.json` and source file
- One prompt per line, plus optional negative prompt and starting seed

Tap **Update dataset + push kernel** to download the existing private dataset, preserve its files, replace the prompt queue, create a new dataset version, and push the configured kernel using Kaggle's official CLI. This preserves model files but may require downloading and uploading the entire dataset; ensure the phone has enough free storage and bandwidth. The app does not create credentials or expose a public Gradio share link. Use **Check status** or **Monitor run** to inspect the kernel and **Download outputs** to save available outputs to the phone's `comfyui_gallery` folder.

The kernel source directory must already be configured for your notebook (including its attached dataset and GPU settings). On Android, the Kaggle CLI/API and large downloads require adequate storage and network access. The mobile app runs on the same device; it is not a remote access tunnel to ComfyUI.

## Output and troubleshooting

The runner writes generated images/videos to `/kaggle/working/outputs`. Kaggle makes files in `/kaggle/working` available as notebook output after a successful run. Check the kernel logs for a missing node mapping, unavailable model, invalid workflow, or custom-node startup error. A failed prompt causes a nonzero runner exit rather than being reported as a successful batch.
