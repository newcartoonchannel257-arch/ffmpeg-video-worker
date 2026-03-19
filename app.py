from flask import Flask, request, jsonify
import subprocess, os, uuid, requests, base64, shutil, json, asyncio
import edge_tts

app = Flask(__name__)
WORK_DIR = "/tmp/videos"
os.makedirs(WORK_DIR, exist_ok=True)

VOICES = {
    "english":    "en-US-AriaNeural",
    "hindi":      "hi-IN-SwaraNeural",
    "spanish":    "es-ES-ElviraNeural",
    "arabic":     "ar-EG-SalmaNeural",
    "french":     "fr-FR-DeniseNeural",
    "portuguese": "pt-BR-FranciscaNeural"
}

async def generate_tts(text, voice, path):
    await edge_tts.Communicate(text, voice).save(path)

def run_cmd(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise Exception(f"Command failed: {r.stderr}")
    return r.stdout

def get_ffmpeg():
    for p in ["/usr/bin/ffmpeg", "/usr/local/bin/ffmpeg"]:
        if os.path.exists(p):
            return p
    return "ffmpeg"

def get_ffprobe():
    for p in ["/usr/bin/ffprobe", "/usr/local/bin/ffprobe"]:
        if os.path.exists(p):
            return p
    return "ffprobe"

@app.route("/health")
def health():
    ff = get_ffmpeg()
    try:
        v = subprocess.run([ff, "-version"], capture_output=True, text=True).stdout.split('\n')[0]
    except:
        v = "not found"
    return {"status": "ok", "ffmpeg": v, "voices": list(VOICES.keys())}

@app.route("/create-video", methods=["POST"])
def create_video():
    job_id  = str(uuid.uuid4())[:8]
    job_dir = os.path.join(WORK_DIR, job_id)
    os.makedirs(job_dir, exist_ok=True)
    try:
        data        = request.json
        image_urls  = data.get("image_urls", [])
        script_text = data.get("script_text", "")
        language    = data.get("language", "english")

        if not image_urls:
            return {"error": "No image_urls"}, 400
        if not script_text:
            return {"error": "No script_text"}, 400

        ff = get_ffmpeg()
        fp = get_ffprobe()

        # 1. TTS audio
        audio_path = os.path.join(job_dir, "audio.mp3")
        voice = VOICES.get(language, VOICES["english"])
        asyncio.run(generate_tts(script_text, voice, audio_path))

        # 2. Download images
        image_paths = []
        for i, url in enumerate(image_urls[:5]):
            p = os.path.join(job_dir, f"img_{i:02d}.jpg")
            r = requests.get(url, timeout=30)
            open(p, "wb").write(r.content)
            image_paths.append(p)

        # 3. Audio duration
        probe = subprocess.run([fp, "-v", "quiet", "-print_format", "json", "-show_format", audio_path], capture_output=True, text=True)
        duration = float(json.loads(probe.stdout)["format"]["duration"])

        # 4. Concat file
        img_dur = duration / len(image_paths)
        concat  = os.path.join(job_dir, "concat.txt")
        with open(concat, "w") as f:
            for p in image_paths:
                f.write(f"file '{p}'\nduration {img_dur:.2f}\n")
            f.write(f"file '{image_paths[-1]}'\n")

        # 5. Slideshow
        slide = os.path.join(job_dir, "slide.mp4")
        subprocess.run([ff, "-y", "-f", "concat", "-safe", "0", "-i", concat,
            "-vf", "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,fps=30",
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28", "-pix_fmt", "yuv420p", slide
        ], check=True, capture_output=True)

        # 6. Merge
        out = os.path.join(job_dir, "final.mp4")
        subprocess.run([ff, "-y", "-i", slide, "-i", audio_path,
            "-c:v", "copy", "-c:a", "aac", "-shortest", "-movflags", "+faststart", out
        ], check=True, capture_output=True)

        video_b64 = base64.b64encode(open(out, "rb").read()).decode()
        shutil.rmtree(job_dir, ignore_errors=True)
        return {"status": "success", "job_id": job_id, "video_base64": video_b64, "duration": duration}

    except subprocess.CalledProcessError as e:
        shutil.rmtree(job_dir, ignore_errors=True)
        return {"error": "FFmpeg failed", "details": e.stderr.decode()}, 500
    except Exception as e:
        shutil.rmtree(job_dir, ignore_errors=True)
        return {"error": str(e)}, 500

@app.route("/tts-only", methods=["POST"])
def tts_only():
    job_id  = str(uuid.uuid4())[:8]
    job_dir = os.path.join(WORK_DIR, job_id)
    os.makedirs(job_dir, exist_ok=True)
    try:
        data  = request.json
        text  = data.get("text", "")
        lang  = data.get("language", "english")
        voice = VOICES.get(lang, VOICES["english"])
        path  = os.path.join(job_dir, "audio.mp3")
        asyncio.run(generate_tts(text, voice, path))
        b64 = base64.b64encode(open(path, "rb").read()).decode()
        shutil.rmtree(job_dir, ignore_errors=True)
        return {"status": "success", "audio_base64": b64, "language": lang, "voice": voice}
    except Exception as e:
        shutil.rmtree(job_dir, ignore_errors=True)
        return {"error": str(e)}, 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
