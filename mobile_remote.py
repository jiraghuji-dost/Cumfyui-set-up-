#!/usr/bin/env python3
"""A small, token-protected mobile controller for a local ComfyUI server."""

import argparse
import email.policy
import hmac
import json
import select
import secrets
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


COMFY_URL = "http://127.0.0.1:8188"
PORTABLE_ROOT = Path(
    r"C:\Users\ADMIN\Desktop\ComfyUI_windows_portable_nvidia_cu126"
)
COMFY_ROOT = PORTABLE_ROOT / "ComfyUI"
COMFY_PYTHON = PORTABLE_ROOT / "python_embeded" / "python.exe"
COMFY_MAIN = COMFY_ROOT / "main.py"
COMFY_LOG = COMFY_ROOT / "user" / "mobile_remote.log"
TOKEN_FILE = COMFY_ROOT / "user" / "my_aiagent.token"
MAX_BODY = 8192
MAX_UPLOAD_BYTES = 25 * 1024 * 1024
MAX_DIMENSION = 768
MAX_PIXELS = MAX_DIMENSION * MAX_DIMENSION
MAX_STEPS = 30
AUTO_STOP_SECONDS = 5 * 60
_COMFY_PROCESS = None
_COMFY_LOCK = threading.Lock()
_ACTIVITY_LOCK = threading.RLock()
_GENERATION_SUBMITTING = 0
_IDLE_SINCE = None

APP_NAME = "My AIAgent"
MANIFEST = {
    "name": APP_NAME,
    "short_name": APP_NAME,
    "start_url": "/",
    "scope": "/",
    "display": "standalone",
    "background_color": "#111827",
    "theme_color": "#111827",
    "icons": [
        {"src": "/icon.svg", "sizes": "any", "type": "image/svg+xml", "purpose": "any maskable"}
    ],
}
ICON = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512">
<rect width="512" height="512" rx="112" fill="#111827"/>
<path d="M256 86 405 172v168l-149 86-149-86V172z" fill="#7c3aed"/>
<circle cx="256" cy="256" r="115" fill="#111827"/>
<path d="m197 317 59-122 59 122m-93-42h68" fill="none" stroke="#f9fafb" stroke-linecap="round" stroke-linejoin="round" stroke-width="24"/>
</svg>"""
SERVICE_WORKER = """
const CACHE = "my-aiagent-shell-v1";
self.addEventListener("install", event => {
  event.waitUntil(caches.open(CACHE).then(cache => cache.addAll(["/", "/manifest.webmanifest", "/icon.svg"])));
  self.skipWaiting();
});
self.addEventListener("activate", event => event.waitUntil(self.clients.claim()));
self.addEventListener("fetch", event => {
  const url = new URL(event.request.url);
  if (url.origin !== self.location.origin || url.pathname.startsWith("/api/")) return;
  event.respondWith(fetch(event.request).then(response => {
    if (response.ok) caches.open(CACHE).then(cache => cache.put(event.request, response.clone()));
    return response;
  }).catch(() => caches.match(event.request)));
});
"""

PAGE = r"""<!doctype html>
<html lang="hi-Latn">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#111827">
<meta name="application-name" content="My AIAgent">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-title" content="My AIAgent">
<link rel="manifest" href="/manifest.webmanifest">
<link rel="icon" href="/icon.svg" type="image/svg+xml">
<title>My AIAgent</title>
<style>
*{box-sizing:border-box}body{margin:0;background:#111827;color:#f3f4f6;font:16px system-ui,sans-serif}
main{width:min(100%,560px);margin:auto;padding:20px 16px 40px}.card{background:#1f2937;border:1px solid #374151;border-radius:16px;padding:18px;margin:14px 0}
h1{font-size:1.55rem;margin:6px 0}p{color:#cbd5e1}.field{display:block;margin:14px 0 6px;font-weight:600}
input,textarea,select,button{font:inherit;width:100%;border-radius:10px;padding:12px;border:1px solid #4b5563;background:#111827;color:#f9fafb}
textarea{min-height:110px;resize:vertical}button{border:0;background:#7c3aed;font-weight:700;margin-top:12px;min-height:48px}
button:disabled{opacity:.55}#status{white-space:pre-wrap;min-height:24px;color:#c4b5fd}
#install[hidden]{display:none}
#result img{display:block;width:100%;height:auto;border-radius:12px;margin-top:14px}
.download{display:inline-block;margin-top:10px;color:#c4b5fd;font-weight:700}
.row{display:grid;grid-template-columns:1fr 1fr;gap:12px}.hint{font-size:.88rem;color:#9ca3af}
#progress-area{margin-top:18px}.progress-track{height:16px;margin-top:12px;overflow:hidden;border-radius:999px;background:#374151}
.progress-fill{height:100%;width:0;background:#a78bfa;transition:width .25s ease}
.progress-track.indeterminate .progress-fill{width:35%;animation:progress 1.2s ease-in-out infinite alternate}
@keyframes progress{from{transform:translateX(-10%)}to{transform:translateX(210%)}}
</style>
</head>
<body><main>
<h1>My AIAgent</h1><p>Apne PC ke ComfyUI se image banayein.</p>
<section class="card">
<button id="install" hidden>My AIAgent install karein</button>
<label class="field" for="model">Model</label><select id="model"></select>
<label class="field" for="mode">Generation mode</label><select id="mode">
<option value="text-to-image">Text to Image</option>
<option value="image-to-image">Image to Image</option>
<option value="text-to-video" disabled>Text to Video (video setup deferred)</option>
<option value="image-to-video" disabled>Image to Video (video setup deferred)</option>
</select>
<div id="image-input" hidden><label class="field" for="source-image">Source image</label><input id="source-image" type="file" accept="image/*"><p class="hint">Image frame size ke mutabiq resize/crop hogi.</p></div>
<label class="field" for="prompt">Prompt (kya image chahiye?)</label><textarea id="prompt" maxlength="1000" placeholder="Image ka description likhein"></textarea>
<label class="field" for="negative">Negative prompt (optional)</label><input id="negative" maxlength="500" placeholder="blurry, low quality">
<label class="field" for="frame">Platform / frame size</label><select id="frame">
<option value="512x512">Instagram feed · square 1:1 (512 × 512)</option>
<option value="512x640">Instagram portrait · 4:5 (512 × 640)</option>
<option value="360x640">TikTok / Reels / Shorts · vertical 9:16 (360 × 640)</option>
<option value="768x432">YouTube · landscape 16:9 (768 × 432)</option>
<option value="custom">Custom dimensions</option>
</select>
<div class="row"><div><label class="field" for="width">Width</label><input id="width" type="number" min="256" max="768" step="8" value="512"></div>
<div><label class="field" for="height">Height</label><input id="height" type="number" min="256" max="768" step="8" value="512"></div></div>
<div class="row">
<div><label class="field" for="steps">Steps</label><select id="steps"><option selected>8</option><option>12</option><option>20</option><option>30</option></select></div></div>
<button id="generate">Image banayein</button>
<div id="progress-area" aria-live="polite">
<div id="progress-track" class="progress-track" role="progressbar" aria-label="Image generation progress" aria-valuemin="0" aria-valuemax="100" aria-valuenow="0"><div id="progress-fill" class="progress-fill"></div></div>
<p id="progress-label">Generate dabane par progress yahan dikhegi.</p></div>
<p id="status" role="status">ComfyUI se connect ho raha hai…</p><div id="result"></div>
</section>
<p class="hint">Generate dabane par ComfyUI start hota hai. Kaam na ho to 5 minute idle ke baad auto band ho jayega. Remote access ke liye Tailscale HTTPS use karein; access token private rakhein.</p>
</main>
<script>
let token=localStorage.getItem('myAIAgentToken')||'';
if(location.hash.length>1){token=decodeURIComponent(location.hash.slice(1));localStorage.setItem('myAIAgentToken',token);history.replaceState(null,'',location.pathname)}
const statusEl=document.querySelector('#status'),button=document.querySelector('#generate'),installButton=document.querySelector('#install'),modelSelect=document.querySelector('#model'),modeSelect=document.querySelector('#mode'),imageInput=document.querySelector('#source-image'),imageInputArea=document.querySelector('#image-input'),frameSelect=document.querySelector('#frame'),widthInput=document.querySelector('#width'),heightInput=document.querySelector('#height'),progressTrack=document.querySelector('#progress-track'),progressFill=document.querySelector('#progress-fill'),progressLabel=document.querySelector('#progress-label');
let generating=false,currentProgress=0;
async function api(path,options={}){options.headers={...(options.headers||{}),'X-Remote-Token':token};const r=await fetch(path,options);const data=await r.json();if(!r.ok)throw new Error(data.error||`HTTP ${r.status}`);return data}
function setProgress(value,label,indeterminate=false){progressLabel.textContent=label;progressTrack.classList.toggle('indeterminate',indeterminate);if(indeterminate){progressTrack.removeAttribute('aria-valuenow');progressFill.style.width='35%';return}currentProgress=Math.max(currentProgress,Math.min(100,Math.max(0,value)));progressTrack.setAttribute('aria-valuenow',String(Math.round(currentProgress)));progressFill.style.width=`${currentProgress}%`}
async function loadModels(){const data=await api('/api/state');const selected=modelSelect.value;if(!data.models.length)throw new Error('ComfyUI mein koi checkpoint model nahi mila.');modelSelect.replaceChildren(...data.models.map(name=>new Option(name,name)));if(data.models.includes(selected))modelSelect.value=selected;else modelSelect.value=data.models[0];return data.models.length}
async function refresh(){try{const state=await api('/api/status');button.disabled=generating;if(state.running){if(!modelSelect.options.length){try{const count=await loadModels();if(!generating)statusEl.textContent=`Ready · ${count} model(s) available`}catch(e){if(!generating)statusEl.textContent=`Model error: ${e.message}`}}}else if(!generating){modelSelect.replaceChildren(new Option('Generate dabane par engine start hoga',''));statusEl.textContent='Prompt likhkar Generate dabayein; ComfyUI tab start hoga.'}}catch(e){if(!generating)statusEl.textContent=`Status error: ${e.message}`;button.disabled=generating}}
async function waitForEngine(){let state=await api('/api/status');if(state.running){if(state.managed)await api('/api/start',{method:'POST'});await loadModels();return}setProgress(currentProgress,'ComfyUI start ho raha hai…',true);await api('/api/start',{method:'POST'});for(let i=0;i<120;i++){await new Promise(r=>setTimeout(r,3000));state=await api('/api/status');if(state.running){setProgress(10,'Model load ho raha hai…');await loadModels();return}if(state.error)throw new Error(state.error);setProgress(currentProgress,'Engine aur model load ho rahe hain…',true)}throw new Error('ComfyUI start timed out')}
async function uploadImage(file){if(!file||!file.type.startsWith('image/'))throw new Error('Image-to-Image ke liye image file chunein.');const form=new FormData();form.append('image',file,file.name);form.append('type','input');form.append('subfolder','');const response=await fetch('/api/upload',{method:'POST',headers:{'X-Remote-Token':token},body:form});const data=await response.json();if(!response.ok)throw new Error(data.error||`HTTP ${response.status}`);return data.image}
function openProgressSocket(clientId){return new Promise((resolve,reject)=>{const scheme=location.protocol==='https:'?'wss:':'ws:',socket=new WebSocket(`${scheme}//${location.host}/ws?clientId=${encodeURIComponent(clientId)}`,token),timeout=setTimeout(()=>{socket.close();reject(new Error('Live progress connection timed out'))},10000);socket.addEventListener('open',()=>{clearTimeout(timeout);resolve(socket)},{once:true});socket.addEventListener('error',()=>{clearTimeout(timeout);reject(new Error('Live progress connection failed'))},{once:true})})}
async function connectProgress(clientId){let lastError;for(let attempt=0;attempt<3;attempt++){try{return await openProgressSocket(clientId)}catch(error){lastError=error;if(attempt<2){setProgress(15,`Live progress reconnect ho raha hai… (${attempt+1}/2)`,true);await new Promise(resolve=>setTimeout(resolve,500*(attempt+1)))}}}throw lastError}
function trackProgress(socket){const state={promptId:null,pending:[],seen:false,disconnected:false};const apply=data=>{if(!state.promptId){state.pending.push(data);return}if(data.prompt_id!==state.promptId)return;const value=Number(data.value),max=Number(data.max);if(!Number.isFinite(value)||!Number.isFinite(max)||max<=0)return;state.seen=true;setProgress(20+70*Math.min(1,value/max),`Image generate ho rahi hai · ${Math.min(max,Math.max(0,value))}/${max} steps`)};socket.addEventListener('message',event=>{if(typeof event.data!=='string')return;let message;try{message=JSON.parse(event.data)}catch{return}if(message.type==='progress'&&message.data)apply(message.data)});socket.addEventListener('close',()=>{state.disconnected=true});socket.addEventListener('error',()=>{state.disconnected=true});state.setPromptId=id=>{state.promptId=id;for(const data of state.pending)apply(data);state.pending=[]};return state}
function applyFramePreset(){const preset=frameSelect.value;if(preset==='custom'){widthInput.disabled=false;heightInput.disabled=false;return}const [width,height]=preset.split('x').map(Number);widthInput.value=String(width);heightInput.value=String(height);widthInput.disabled=true;heightInput.disabled=true}
async function generate(){const prompt=document.querySelector('#prompt').value.trim(),mode=modeSelect.value;if(!prompt){statusEl.textContent='Pehle prompt likhein.';return}const sourceFile=imageInput.files[0];if(mode==='image-to-image'&&!sourceFile){statusEl.textContent='Image-to-Image ke liye source image chunein.';return}generating=true;button.disabled=true;currentProgress=0;setProgress(2,'Process shuru ho raha hai…',true);document.querySelector('#result').replaceChildren();let progressSocket=null;try{await waitForEngine();let imageName='';if(mode==='image-to-image'){setProgress(12,'Source image upload ho rahi hai…',true);imageName=await uploadImage(sourceFile)}const clientId=crypto.randomUUID();setProgress(15,'Live progress connect ho raha hai…',true);let progressState=null,progressWarning='';try{progressSocket=await connectProgress(clientId);progressState=trackProgress(progressSocket)}catch(error){progressWarning=error.message;setProgress(15,'Live progress unavailable; generation jaari rahegi.',true)}const body={mode,prompt,negative_prompt:document.querySelector('#negative').value,model:modelSelect.value,width:Number(widthInput.value),height:Number(heightInput.value),steps:Number(document.querySelector('#steps').value),client_id:clientId};if(imageName)body.image=imageName;const job=await api('/api/generate',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});if(progressState)progressState.setPromptId(job.prompt_id);setProgress(18,'Queue mein hai…',true);statusEl.textContent='Prompt queue mein add ho gaya.';await monitor(job.prompt_id,progressState,progressWarning)}catch(e){statusEl.textContent=`Error: ${e.message}`;setProgress(currentProgress,'Process ruk gaya. Error dekhein.',false)}finally{if(progressSocket)progressSocket.close();generating=false;button.disabled=false;await refresh()}}
async function monitor(id,progressState,progressWarning){for(let i=0;i<720;i++){await new Promise(r=>setTimeout(r,2000));const job=await api('/api/job?id='+encodeURIComponent(id));statusEl.textContent=job.status==='done'?'Image ready':`Generation ${job.status}…`;if(job.status==='queued')setProgress(currentProgress,'Queue mein intezar…',true);else if(job.status==='running'&&(!progressState||!progressState.seen||progressState.disconnected)){const message=progressWarning|| (progressState&&progressState.disconnected?'Live progress disconnect ho gayi; generation jaari hai.':'Image generate ho rahi hai…');setProgress(Math.max(currentProgress,20),message,true)}if(job.status==='error')throw new Error(job.error||'ComfyUI reported a generation error');if(job.status==='done'){setProgress(95,'Image final ho rahi hai…');const result=document.querySelector('#result');for(const image of job.images){const response=await fetch('/api/image?prompt_id='+encodeURIComponent(id)+'&filename='+encodeURIComponent(image),{headers:{'X-Remote-Token':token}});if(!response.ok)throw new Error('Could not load generated image');const url=URL.createObjectURL(await response.blob());const img=document.createElement('img');img.alt='Generated image';img.src=url;const download=document.createElement('a');download.className='download';download.href=url;download.download=image;download.textContent='Image phone par download karein';result.append(img,download)}setProgress(100,'Image taiyar hai · Download karein');return}}throw new Error('Timed out while waiting for ComfyUI')}
function syncMode(){imageInputArea.hidden=modeSelect.value!=='image-to-image'}
frameSelect.addEventListener('change',applyFramePreset);modeSelect.addEventListener('change',syncMode);applyFramePreset();syncMode();
let installPrompt;window.addEventListener('beforeinstallprompt',event=>{event.preventDefault();installPrompt=event;installButton.hidden=false});installButton.addEventListener('click',async()=>{if(!installPrompt)return;installPrompt.prompt();await installPrompt.userChoice;installPrompt=null;installButton.hidden=true});
if('serviceWorker' in navigator)navigator.serviceWorker.register('/service-worker.js').catch(error=>console.warn('App shell could not be cached:',error));
button.addEventListener('click',generate);refresh();setInterval(refresh,10000);
</script></body></html>"""


def comfy_json(path, payload=None):
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        COMFY_URL + path,
        data=data,
        headers={"Content-Type": "application/json"} if data is not None else {},
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            body = response.read()
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"ComfyUI returned HTTP {error.code}: {detail}") from error
    except urllib.error.URLError as error:
        raise RuntimeError(f"Cannot connect to ComfyUI at {COMFY_URL}: {error}") from error
    try:
        return json.loads(body)
    except json.JSONDecodeError as error:
        raise RuntimeError("ComfyUI returned invalid JSON.") from error


def available_models():
    info = comfy_json("/object_info/CheckpointLoaderSimple")
    try:
        models = info["CheckpointLoaderSimple"]["input"]["required"]["ckpt_name"][0]
    except (KeyError, IndexError, TypeError) as error:
        raise RuntimeError("Could not read the available checkpoint list from ComfyUI.") from error
    if not isinstance(models, list):
        raise RuntimeError("ComfyUI returned an invalid checkpoint list.")
    return models


def prompt_workflow(data, models):
    prompt = data.get("prompt")
    negative = data.get("negative_prompt", "")
    model = data.get("model")
    mode = data.get("mode", "text-to-image")
    size = data.get("size", 512)
    width = data.get("width", size)
    height = data.get("height", size)
    steps = data.get("steps", 8)
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 1000:
        raise ValueError("Prompt must contain 1–1000 characters.")
    if not isinstance(negative, str) or len(negative) > 500:
        raise ValueError("Negative prompt must contain at most 500 characters.")
    if model not in models:
        raise ValueError("Choose a checkpoint from the available list.")
    if mode not in ("text-to-image", "image-to-image"):
        raise ValueError("Video generation is unavailable until a video model is installed.")
    if (
        isinstance(width, bool)
        or not isinstance(width, int)
        or isinstance(height, bool)
        or not isinstance(height, int)
        or not 256 <= width <= MAX_DIMENSION
        or not 256 <= height <= MAX_DIMENSION
        or width % 8
        or height % 8
        or width * height > MAX_PIXELS
    ):
        raise ValueError("Frame dimensions must be multiples of 8, at least 256 pixels, and no larger than 768 × 768.")
    if isinstance(steps, bool) or not isinstance(steps, int) or not 1 <= steps <= MAX_STEPS:
        raise ValueError(f"Steps must be from 1 to {MAX_STEPS}.")
    workflow = {
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": model}},
        "2": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt.strip(), "clip": ["1", 1]}},
        "3": {"class_type": "CLIPTextEncode", "inputs": {"text": negative, "clip": ["1", 1]}},
    }
    if mode == "text-to-image":
        workflow["4"] = {
            "class_type": "EmptyLatentImage",
            "inputs": {"width": width, "height": height, "batch_size": 1},
        }
        latent = ["4", 0]
        sampler_id = "5"
        decode_id = "6"
        save_id = "7"
    else:
        image = data.get("image")
        if (
            not isinstance(image, str)
            or not image
            or image.startswith(("/", "\\"))
            or any(part in ("", ".", "..") for part in image.replace("\\", "/").split("/"))
        ):
            raise ValueError("Upload a valid source image for Image-to-Image.")
        workflow["4"] = {"class_type": "LoadImage", "inputs": {"image": image}}
        workflow["5"] = {
            "class_type": "ImageScale",
            "inputs": {
                "image": ["4", 0],
                "upscale_method": "lanczos",
                "width": width,
                "height": height,
                "crop": "center",
            },
        }
        workflow["6"] = {
            "class_type": "VAEEncode",
            "inputs": {"pixels": ["5", 0], "vae": ["1", 2]},
        }
        latent = ["6", 0]
        sampler_id = "7"
        decode_id = "8"
        save_id = "9"
    workflow[sampler_id] = {
            "class_type": "KSampler",
            "inputs": {
                "seed": secrets.randbits(32),
                "steps": steps,
                "cfg": 7.0,
                "sampler_name": "euler",
                "scheduler": "normal",
                "denoise": 1.0 if mode == "text-to-image" else 0.65,
                "model": ["1", 0],
                "positive": ["2", 0],
                "negative": ["3", 0],
                "latent_image": latent,
            },
        }
    workflow[decode_id] = {
        "class_type": "VAEDecode",
        "inputs": {"samples": [sampler_id, 0], "vae": ["1", 2]},
    }
    workflow[save_id] = {
        "class_type": "SaveImage",
        "inputs": {"filename_prefix": "mobile_remote", "images": [decode_id, 0]},
    }
    return workflow


def queue_ids():
    data = comfy_json("/queue")
    running = {entry[1] for entry in data.get("queue_running", []) if len(entry) > 1}
    pending = {entry[1] for entry in data.get("queue_pending", []) if len(entry) > 1}
    return running, pending


def idle_timeout_elapsed(idle_since, now, queue_busy, submitting):
    return (
        idle_since is not None
        and not queue_busy
        and not submitting
        and now - idle_since >= AUTO_STOP_SECONDS
    )


def job_data(prompt_id):
    try:
        prompt_id = str(uuid.UUID(prompt_id))
    except (ValueError, AttributeError) as error:
        raise ValueError("Invalid prompt ID.") from error
    history = comfy_json("/history/" + prompt_id)
    entry = history.get(prompt_id)
    if not entry:
        running, pending = queue_ids()
        if prompt_id in running:
            return {"status": "running", "images": []}
        if prompt_id in pending:
            return {"status": "queued", "images": []}
        return {"status": "unknown", "images": []}
    status = entry.get("status", {})
    if status.get("status_str") == "error":
        message = "Image generation failed."
        for item in status.get("messages", []):
            if item and item[0] == "execution_error" and isinstance(item[1], dict):
                message = item[1].get("exception_message", message)
                break
        return {"status": "error", "error": message, "images": []}
    images = []
    for node in entry.get("outputs", {}).values():
        for image in node.get("images", []):
            if image.get("type") == "output" and image.get("filename"):
                images.append(image["filename"])
    return {"status": "done" if images else "running", "images": images}


def comfy_status():
    global _COMFY_PROCESS
    process = _COMFY_PROCESS
    if process is not None and process.poll() is not None:
        exit_code = process.returncode
        _COMFY_PROCESS = None
        state = {"running": False, "starting": False, "managed": False}
        if exit_code:
            state["error"] = f"ComfyUI exited with code {exit_code}."
        return state
    if process is not None:
        try:
            comfy_json("/system_stats")
        except RuntimeError:
            return {"running": False, "starting": True, "managed": True, "pid": process.pid}
        return {"running": True, "starting": False, "managed": True, "pid": process.pid}
    try:
        comfy_json("/system_stats")
    except RuntimeError:
        return {"running": False, "starting": False, "managed": False}
    return {"running": True, "starting": False, "managed": False}


def start_comfy():
    global _COMFY_PROCESS
    global _IDLE_SINCE
    with _ACTIVITY_LOCK:
        _IDLE_SINCE = None
        with _COMFY_LOCK:
            state = comfy_status()
            if state["running"]:
                if state["managed"]:
                    return state
                raise RuntimeError("ComfyUI is already running outside the mobile controller.")
            if state["starting"]:
                return state
            for required_path in (COMFY_PYTHON, COMFY_MAIN):
                if not required_path.is_file():
                    raise RuntimeError(f"ComfyUI file not found: {required_path}")
            COMFY_LOG.parent.mkdir(parents=True, exist_ok=True)
            command = [
                str(COMFY_PYTHON),
                "-u",
                "-s",
                str(COMFY_MAIN),
                "--cpu",
                "--windows-standalone-build",
                "--listen",
                "127.0.0.1",
            ]
            try:
                with COMFY_LOG.open("ab") as log_file:
                    _COMFY_PROCESS = subprocess.Popen(
                        command,
                        cwd=str(PORTABLE_ROOT),
                        stdout=log_file,
                        stderr=subprocess.STDOUT,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    )
            except OSError as error:
                raise RuntimeError(f"Could not start ComfyUI: {error}") from error
            return {
                "running": False,
                "starting": True,
                "managed": True,
                "pid": _COMFY_PROCESS.pid,
            }


def stop_comfy():
    global _COMFY_PROCESS
    global _IDLE_SINCE
    with _ACTIVITY_LOCK:
        _IDLE_SINCE = None
        with _COMFY_LOCK:
            process = _COMFY_PROCESS
            if process is None:
                state = comfy_status()
                if state["running"]:
                    raise RuntimeError("ComfyUI was started outside the mobile controller.")
                return state
            if process.poll() is not None:
                _COMFY_PROCESS = None
                return {"running": False, "starting": False, "managed": False}

            interrupt = urllib.request.Request(
                COMFY_URL + "/interrupt",
                data=b"",
                method="POST",
            )
            try:
                with urllib.request.urlopen(interrupt, timeout=5):
                    pass
            except (urllib.error.HTTPError, urllib.error.URLError) as error:
                print(f"ComfyUI interrupt request failed before stop: {error}", flush=True)

            try:
                process.terminate()
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            except OSError as error:
                raise RuntimeError(f"Could not stop ComfyUI: {error}") from error
            _COMFY_PROCESS = None
            return {"running": False, "starting": False, "managed": False}


def auto_stop_worker():
    global _IDLE_SINCE
    while True:
        time.sleep(5)
        state = comfy_status()
        if not state["managed"] or not state["running"]:
            with _ACTIVITY_LOCK:
                _IDLE_SINCE = None
            continue
        try:
            running, pending = queue_ids()
        except RuntimeError as error:
            with _ACTIVITY_LOCK:
                _IDLE_SINCE = None
            print(f"ComfyUI idle check failed: {error}", flush=True)
            continue
        with _ACTIVITY_LOCK:
            if _GENERATION_SUBMITTING or running or pending:
                _IDLE_SINCE = None
                continue
            now = time.monotonic()
            if _IDLE_SINCE is None:
                _IDLE_SINCE = now
                continue
            if not idle_timeout_elapsed(
                _IDLE_SINCE,
                now,
                bool(running or pending),
                _GENERATION_SUBMITTING > 0,
            ):
                continue
            try:
                stop_comfy()
            except RuntimeError as error:
                print(f"Could not auto-stop idle ComfyUI: {error}", flush=True)
            else:
                print("ComfyUI stopped after five minutes idle.", flush=True)


def forward_image_upload(content_type, body):
    try:
        message = BytesParser(policy=email.policy.default).parsebytes(
            b"Content-Type: "
            + content_type.encode("ascii")
            + b"\r\nMIME-Version: 1.0\r\n\r\n"
            + body
        )
    except (UnicodeEncodeError, ValueError) as error:
        raise ValueError("Invalid image upload form.") from error
    if not message.is_multipart():
        raise ValueError("Image upload must use multipart form data.")

    image_parts = []
    fields = {}
    for part in message.iter_parts():
        field_name = part.get_param("name", header="content-disposition")
        if part.get_content_disposition() != "form-data":
            continue
        if field_name == "image" and part.get_filename():
            image_parts.append(part)
        elif field_name:
            fields[field_name] = (part.get_payload(decode=True) or b"").decode(
                "utf-8", errors="replace"
            )
    if len(image_parts) != 1 or not image_parts[0].get_content_type().startswith("image/"):
        raise ValueError("Upload exactly one supported image file.")
    if fields.get("type") != "input" or fields.get("subfolder", ""):
        raise ValueError("Image uploads must target the ComfyUI input folder.")

    request = urllib.request.Request(
        COMFY_URL + "/upload/image",
        data=body,
        headers={"Content-Type": content_type},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            result = json.loads(response.read())
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"ComfyUI image upload failed: {detail}") from error
    except urllib.error.URLError as error:
        raise RuntimeError(f"Could not upload image to ComfyUI: {error}") from error
    except json.JSONDecodeError as error:
        raise RuntimeError("ComfyUI returned invalid image upload data.") from error

    name = result.get("name")
    subfolder = result.get("subfolder", "")
    if not isinstance(name, str) or not name or not isinstance(subfolder, str):
        raise RuntimeError("ComfyUI returned an invalid uploaded image path.")
    image_ref = "/".join(part for part in (subfolder, name) if part)
    if (
        image_ref.startswith("/")
        or "\\" in image_ref
        or any(part in ("", ".", "..") for part in image_ref.split("/"))
    ):
        raise RuntimeError("ComfyUI returned an unsafe uploaded image path.")
    return image_ref


def load_access_token():
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    if TOKEN_FILE.exists():
        token = TOKEN_FILE.read_text(encoding="ascii").strip()
        if len(token) < 32 or not all(char.isalnum() or char in "_-" for char in token):
            raise RuntimeError(f"Invalid access token in {TOKEN_FILE}; remove it to create a new one.")
        return token
    token = secrets.token_urlsafe(32)
    with TOKEN_FILE.open("x", encoding="ascii") as token_file:
        token_file.write(token + "\n")
    return token


class MobileHandler(BaseHTTPRequestHandler):
    server_version = "MyAIAgent/1.0"

    def log_message(self, format_string, *args):
        print("%s - %s" % (self.address_string(), format_string % args))

    def send_json(self, status, value):
        body = json.dumps(value).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_static(self, content_type, body, headers=None):
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def authorized(self):
        supplied = self.headers.get("X-Remote-Token", "")
        if hmac.compare_digest(supplied, self.server.access_token):
            return True
        self.send_json(401, {"error": "Invalid or missing access token."})
        return False

    def proxy_websocket(self, parsed):
        protocols = [
            item.strip()
            for item in self.headers.get("Sec-WebSocket-Protocol", "").split(",")
        ]
        if not any(
            hmac.compare_digest(protocol, self.server.access_token)
            for protocol in protocols
        ):
            self.send_json(401, {"error": "Invalid or missing access token."})
            return
        client_id = urllib.parse.parse_qs(parsed.query).get("clientId", [""])[0]
        try:
            client_id = str(uuid.UUID(client_id))
        except (ValueError, AttributeError) as error:
            self.send_json(400, {"error": "Invalid WebSocket client ID."})
            return
        key = self.headers.get("Sec-WebSocket-Key")
        if self.headers.get("Upgrade", "").lower() != "websocket" or not key:
            self.send_json(400, {"error": "A WebSocket upgrade is required."})
            return

        upstream = None
        upgraded = False
        try:
            upstream = socket.create_connection(("127.0.0.1", 8188), timeout=10)
            request_headers = [
                f"GET /ws?clientId={client_id} HTTP/1.1",
                "Host: 127.0.0.1:8188",
                "Connection: Upgrade",
                "Upgrade: websocket",
                f"Sec-WebSocket-Key: {key}",
                "Sec-WebSocket-Version: 13",
            ]
            extensions = self.headers.get("Sec-WebSocket-Extensions")
            if extensions:
                request_headers.append(f"Sec-WebSocket-Extensions: {extensions}")
            upstream.sendall(("\r\n".join(request_headers) + "\r\n\r\n").encode("ascii"))

            response = bytearray()
            while b"\r\n\r\n" not in response:
                chunk = upstream.recv(4096)
                if not chunk:
                    raise OSError("ComfyUI closed the WebSocket handshake.")
                response.extend(chunk)
                if len(response) > 65536:
                    raise ValueError("ComfyUI WebSocket handshake was too large.")
            header_end = response.index(b"\r\n\r\n")
            response_head = bytes(response[:header_end])
            response_body = bytes(response[header_end + 4 :])
            response_lines = response_head.split(b"\r\n")
            if b" 101 " not in response_lines[0]:
                raise OSError("ComfyUI rejected the WebSocket handshake.")
            response_lines = [
                line
                for line in response_lines
                if not line.lower().startswith(b"sec-websocket-protocol:")
            ]
            response_lines.append(
                b"Sec-WebSocket-Protocol: "
                + self.server.access_token.encode("ascii")
            )
            self.connection.sendall(
                b"\r\n".join(response_lines) + b"\r\n\r\n" + response_body
            )
            upgraded = True
            upstream.settimeout(None)

            while True:
                readable, _, exceptional = select.select(
                    [self.connection, upstream],
                    [],
                    [self.connection, upstream],
                    30,
                )
                if exceptional:
                    return
                for source in readable:
                    data = source.recv(65536)
                    if not data:
                        return
                    destination = upstream if source is self.connection else self.connection
                    destination.sendall(data)
        except (OSError, ValueError, UnicodeEncodeError) as error:
            if upgraded:
                print(f"WebSocket proxy closed: {error}", flush=True)
            else:
                self.send_json(502, {"error": f"Could not connect progress stream: {error}"})
        finally:
            if upstream is not None:
                upstream.close()

    def do_GET(self):
        parsed = urllib.parse.urlsplit(self.path)
        if parsed.path == "/":
            self.send_static("text/html; charset=utf-8", PAGE.encode("utf-8"))
            return
        if parsed.path == "/manifest.webmanifest":
            body = json.dumps(MANIFEST).encode("utf-8")
            self.send_static("application/manifest+json", body)
            return
        if parsed.path == "/icon.svg":
            self.send_static("image/svg+xml", ICON.encode("utf-8"))
            return
        if parsed.path == "/service-worker.js":
            self.send_static(
                "application/javascript; charset=utf-8",
                SERVICE_WORKER.encode("utf-8"),
                {"Service-Worker-Allowed": "/"},
            )
            return
        if parsed.path == "/ws":
            self.proxy_websocket(parsed)
            return
        if not parsed.path.startswith("/api/"):
            self.send_json(404, {"error": "Not found."})
            return
        if not self.authorized():
            return
        try:
            query = urllib.parse.parse_qs(parsed.query)
            if parsed.path == "/api/status":
                self.send_json(200, comfy_status())
            elif parsed.path == "/api/state":
                state = comfy_status()
                if not state["running"]:
                    self.send_json(503, {"error": "ComfyUI is not ready yet."})
                    return
                self.send_json(200, {"models": available_models()})
            elif parsed.path == "/api/job":
                self.send_json(200, job_data(query.get("id", [""])[0]))
            elif parsed.path == "/api/image":
                prompt_id = query.get("prompt_id", [""])[0]
                filename = query.get("filename", [""])[0]
                allowed = job_data(prompt_id)["images"]
                if filename not in allowed or "/" in filename or "\\" in filename:
                    self.send_json(404, {"error": "Image not found."})
                    return
                url = COMFY_URL + "/view?" + urllib.parse.urlencode(
                    {"filename": filename, "type": "output"}
                )
                with urllib.request.urlopen(url, timeout=60) as response:
                    body = response.read()
                    content_type = response.headers.get("Content-Type", "application/octet-stream")
                if not content_type.startswith("image/"):
                    self.send_json(415, {"error": "ComfyUI returned a non-image file."})
                    return
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_json(404, {"error": "Not found."})
        except (ValueError, RuntimeError, urllib.error.URLError) as error:
            self.send_json(400, {"error": str(error)})

    def do_POST(self):
        global _GENERATION_SUBMITTING
        global _IDLE_SINCE
        path = urllib.parse.urlsplit(self.path).path
        if not self.authorized():
            return
        try:
            if path == "/api/upload":
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= MAX_UPLOAD_BYTES:
                    raise ValueError("Image upload must be smaller than 25 MB.")
                content_type = self.headers.get("Content-Type", "")
                if not content_type.lower().startswith("multipart/form-data; boundary="):
                    raise ValueError("Image upload must use multipart form data.")
                image = forward_image_upload(content_type, self.rfile.read(length))
                self.send_json(200, {"image": image})
                return
            if path == "/api/start":
                self.send_json(202, start_comfy())
                return
            if path == "/api/stop":
                self.send_json(200, stop_comfy())
                return
            if path != "/api/generate":
                self.send_json(404, {"error": "Not found."})
                return
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= MAX_BODY:
                raise ValueError("Request body must be smaller than 8 KB.")
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise ValueError("Request must be a JSON object.")
            client_id = data.get("client_id")
            if client_id is not None:
                if not isinstance(client_id, str):
                    raise ValueError("Invalid progress client ID.")
                try:
                    client_id = str(uuid.UUID(client_id))
                except (ValueError, AttributeError) as error:
                    raise ValueError("Invalid progress client ID.") from error
            with _ACTIVITY_LOCK:
                _GENERATION_SUBMITTING += 1
                _IDLE_SINCE = None
            try:
                workflow = prompt_workflow(data, available_models())
                payload = {"prompt": workflow}
                if client_id:
                    payload["client_id"] = client_id
                result = comfy_json("/prompt", payload)
            finally:
                with _ACTIVITY_LOCK:
                    _GENERATION_SUBMITTING -= 1
            prompt_id = result.get("prompt_id")
            if not prompt_id:
                raise RuntimeError(f"ComfyUI did not accept the prompt: {result}")
            self.send_json(202, {"prompt_id": prompt_id})
        except (ValueError, json.JSONDecodeError, RuntimeError) as error:
            self.send_json(400, {"error": str(error)})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Bind address; keep localhost when using Tailscale Serve.",
    )
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--public-url",
        help="HTTPS Tailscale Serve URL used to print the phone access link.",
    )
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("--port must be between 1024 and 65535.")
    if args.public_url:
        public = urllib.parse.urlsplit(args.public_url)
        if (
            public.scheme != "https"
            or not public.netloc
            or public.path not in ("", "/")
            or public.query
            or public.fragment
        ):
            parser.error("--public-url must be a base HTTPS URL without a path, query, or fragment.")

    token = load_access_token()
    server = ThreadingHTTPServer((args.host, args.port), MobileHandler)
    server.access_token = token
    threading.Thread(target=auto_stop_worker, name="comfyui-idle-stop", daemon=True).start()
    print(f"Mobile controller listening at http://{args.host}:{args.port}/", flush=True)
    if args.host == "127.0.0.1":
        print("Local-only listener; use Tailscale Serve for remote phone access.", flush=True)
    access_base = args.public_url.rstrip("/") if args.public_url else f"http://{args.host}:{args.port}"
    print(f"My AIAgent access URL: {access_base}/#{token}", flush=True)
    print(f"ComfyUI API: {COMFY_URL}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
