# ComfyUI Kaggle Batch Pipeline

This repository builds a ComfyUI image for finite, sequential batch jobs on Kaggle. ComfyUI listens only on `127.0.0.1`; there is no tunnel, reverse proxy, or public ComfyUI endpoint. The runner exits after its queue is complete, and the launcher then stops ComfyUI.

> Kaggle policy compliance depends on your use case and the current Kaggle terms; no image or script can guarantee that a particular use is allowed. Check Kaggle's current Acceptable Use Policy and notebook rules before running jobs. Do not use this setup to evade session limits or keep a session alive.

## Keep model downloads persistent

Docker contains ComfyUI and its runtime, not model weights. Put the model files in a private Kaggle dataset named `comfyui-assets`, then attach that same dataset to each notebook run. `start.sh` links the mounted model folders into ComfyUI; it does not download the weights again from Hugging Face. This separates the large, one-time model upload from the Docker image and the small per-run prompt/input dataset.

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
  workflow_api.json
  workflow_image_to_image_api.json
  workflow_image_to_video_api.json
  workflow_text_to_video_api.json
  models/
    checkpoints/
    vae/
    loras/
    controlnet/
    animatediff_models/
    upscale_models/
    diffusion_models/
    text_encoders/
    clip/
```

Only include model folders that your workflow needs. All workflow files must be ComfyUI API-format workflows (export using **Save (API Format)**), not regular UI workflow files.
The image-to-image/video workflow files are optional unless you use those modes. For the recommended LTX-Video 2B v0.9.5 workflows, install the checkpoint [ltx-video-2b-v0.9.5.safetensors](https://huggingface.co/Lightricks/LTX-Video/resolve/main/ltx-video-2b-v0.9.5.safetensors) (about 5.91 GB) in `models/checkpoints/` and [t5xxl_fp16.safetensors](https://huggingface.co/comfyanonymous/flux_text_encoders/resolve/main/t5xxl_fp16.safetensors) (about 9.12 GB) in `models/text_encoders/`. See the [official ComfyUI LTX-Video guide](https://docs.comfy.org/tutorials/video/ltxv) for model and workflow details. The files total about 15 GB on disk, and generation needs additional GPU memory; verify the available Kaggle GPU can load your chosen workflow before uploading/running. This is a one-time upload to the private Kaggle assets dataset; the mobile app does not download the model weights to the phone.

Create and publish an initial version of a second, lightweight **private** dataset for mobile job inputs, for example `YOUR_KAGGLE_USERNAME/comfyui-jobs`. Verify its privacy setting before uploading any personal source image. Keep it separate from model weights. The mobile app versions this jobs dataset for each request and adds `prompts_queue.json` plus any uploaded source image under `inputs/`; it does not download or re-upload the model assets dataset.

The jobs queue format is:

```json
{
  "workflow_nodes_by_mode": {
    "text-to-image": {"positive_prompt": "6", "negative_prompt": "7", "seed": "3", "filename_prefix": "9", "width": "5", "height": "5"},
    "image-to-image": {"positive_prompt": "6", "source_image": "10", "filename_prefix": "9", "width": "5", "height": "5"},
    "image-to-video": {"positive_prompt": "6", "negative_prompt": "7", "seed": "72", "seed_input": "noise_seed", "source_image": "78", "filename_prefix": "81", "width": "77", "height": "77", "frames": "77"},
    "text-to-video": {"positive_prompt": "6", "negative_prompt": "7", "seed": "72", "seed_input": "noise_seed", "filename_prefix": "79", "width": "70", "height": "70", "frames": "70"}
  },
  "defaults": {
    "negative_prompt": "",
    "seed": 1234,
    "width": 512,
    "height": 512,
    "frames": 17
  },
  "prompts": [
    {"mode": "text-to-image", "prompt": "A calm mountain lake at sunrise"},
    {"mode": "image-to-video", "prompt": "Gentle waves move on the lake", "input_image": "inputs/source.png"},
    {"mode": "text-to-video", "prompt": "A mountain lake at sunrise, soft clouds move slowly"}
  ]
}
```

Replace the example node IDs with IDs from each API workflow. Node mappings may also specify `<setting>_input` keys to override ComfyUI input names; defaults are `text`, `image`, `seed`, `filename_prefix`, `width`, `height`, and `length` for prompts, source images, seeds, prefixes, dimensions, and video frames respectively.

Kaggle mounts attached datasets read-only under `/kaggle/input`. Attach both private datasets to the kernel so their mount paths are `/kaggle/input/comfyui-assets` and `/kaggle/input/comfyui-jobs`.

## 3. Configure the Kaggle Notebook

Create a private Kaggle Notebook and attach both private datasets. In the Notebook settings:

1. Enable a GPU if your workflow requires it.
2. Select your pushed image (`YOUR_DOCKER_ID/kaggle-comfyui:latest`) in Kaggle's custom Docker image setting, where available for your account/runtime.
3. Use the bundled `kaggle_kernel/run.py` as the kernel entry point. It runs `/opt/ComfyUI/start.sh` in the selected image and expects both datasets at the paths above.
4. Keep Internet disabled unless your workload has a legitimate need and Kaggle permits it. The batch image does not download model weights.

Custom image settings are managed in Kaggle Notebook settings; a `kaggle kernels push` does not itself configure or guarantee that setting. Test the notebook once in Kaggle before relying on phone-triggered pushes.

## 4. Trigger and monitor from Android

In Pydroid 3 (or another Python environment), install the dependencies:

```sh
pip install gradio kaggle
```

Create a Kaggle **Legacy API Key** under **Account Settings → API Tokens → Legacy API Credentials**; this app currently expects the downloaded `kaggle.json` format. Store it on the device running the controller at `~/.kaggle/kaggle.json` (on Windows: `%USERPROFILE%\.kaggle\kaggle.json`; on Pydroid, use the Python home directory) or set `KAGGLE_CONFIG_DIR` to its containing directory. Keep this file private and never paste its contents into the app, chat, or repository.

Run `python mobile_app.py` in Pydroid 3 and open the local address it prints on the phone. Enter:

- Private model assets dataset slug, for example `YOUR_KAGGLE_USERNAME/comfyui-assets`
- Separate small jobs dataset slug, for example `YOUR_KAGGLE_USERNAME/comfyui-jobs`
- Kernel slug, for example `YOUR_KAGGLE_USERNAME/comfyui-batch`
- Keep the bundled `kaggle_kernel` folder selected, or point to a compatible kernel source folder
- One prompt per line, optional negative prompt and seed, plus a source image for image-to-image/video
- Workflow node IDs for each mode, output dimensions, and video frame count

Tap **Generate on Kaggle** to download and version only the jobs dataset, then push the private GPU kernel. The Activity panel reports each submission stage; **Monitor run** and output download also show status updates. Sampler-step progress is not streamed while Kaggle runs. The app adds both datasets to a temporary copy of the kernel metadata; it leaves the original kernel source and model assets dataset unchanged, and refuses to upload job inputs unless Kaggle confirms the jobs dataset is private. Use **Check status** or **Monitor run**, then **Get images/videos on phone** after the kernel completes. Supported PNG/JPG/WEBP/GIF/MP4/WEBM outputs are downloaded to `comfyui_gallery` and shown as phone-saveable links; an empty output is reported as an error rather than success.

Image-to-image needs `workflow_image_to_image_api.json`; LTX-Video I2V/T2V need `workflow_image_to_video_api.json` and `workflow_text_to_video_api.json` plus the two model files above. The app's LTXV node-map defaults match the official ComfyUI templates; export those templates in API format and adjust mappings if your workflow differs. For LTX-Video, choose frame counts 9, 17, 25, 33, 41, or 49 and dimensions divisible by 32. Before the first run, publish the Docker image, create a private GPU kernel, select the image in that kernel's settings, and attach both datasets. The bundled `kaggle_kernel` folder supplies the script and metadata; the mobile controller fills in your kernel slug and dataset attachments when pushing a run. Kaggle jobs are finite: images/videos are downloaded after the kernel finishes, not streamed from a notebook web server. The app does not expose a public Gradio share link.

## Output and troubleshooting

The runner writes generated images/videos to `/kaggle/working/outputs`. Kaggle makes files in `/kaggle/working` available as notebook output after a successful run. Check the kernel logs for a missing node mapping, unavailable model, invalid workflow, or custom-node startup error. A failed prompt causes a nonzero runner exit rather than being reported as a successful batch.

## My AIAgent mobile app

For Kaggle image, image-to-image, image-to-video, and text-to-video generation from Android, use the Pydroid `mobile_app.py` controller described above. It submits a finite Kaggle job and exposes the finished files for download on the phone.

`mobile_remote.py` serves an installable, phone-friendly app for the configured portable Windows ComfyUI. Generate starts ComfyUI when needed; a controller-managed engine stops after five minutes with no queued or running work. The app supports text-to-image and image-to-image, reports sampler steps in a live progress bar, and offers square, portrait, vertical, landscape, and custom frame dimensions up to 768 × 768. Image-to-video and text-to-video are shown as unavailable until a compatible video model and nodes are installed. ComfyUI itself stays bound to `127.0.0.1:8188`.

For access from cellular or another network, use a private Tailscale network rather than public port forwarding:

1. Install Tailscale on the PC and phone, sign both into the same tailnet, and enable HTTPS certificates for the tailnet.
2. On the PC, start the controller locally:

   ```powershell
   python mobile_remote.py
   ```

3. Configure Tailscale Serve to expose only this local controller to the tailnet:

   ```powershell
   tailscale serve --bg 8765
   tailscale serve status
   ```

   Copy the HTTPS tailnet URL printed by Serve. Do not use `tailscale funnel`.
4. Restart the controller with that URL so it prints the complete phone access link:

   ```powershell
   python mobile_remote.py --public-url https://your-device.your-tailnet.ts.net
   ```

5. On the phone, connect Tailscale, open the printed access link, and install **My AIAgent** from the browser menu or install prompt. The token is stored under the ComfyUI user folder so the installed app remains authorized after a controller restart; keep the link private.

The PC must remain powered on and connected to the internet. The controller listens on localhost; Tailscale Serve provides HTTPS only to authorized tailnet devices. Do not enable public port forwarding or Funnel. ComfyUI currently runs in CPU mode on this PC because CUDA was unavailable.
