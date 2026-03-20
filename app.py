from flask import Flask, request, jsonify
import subprocess, os, uuid, requests, base64, shutil, json

app = Flask(__name__)
WORK_DIR = "/tmp/videos"
os.makedirs(WORK_DIR, exist_ok=True)

def get_ff(name):
    for p in [f"/usr/bin/{name}", f"/usr/local/bin/{name}"]:
        if os.path.exists(p): return p
    return name

def google_tts(text, path):
    """Google TTS — free, no API key needed"""
    # Split text into chunks (Google TTS limit 200 chars)
    chunks = []
    words = text.split()
    chunk = ""
    for word in words:
        if len(chunk) + len(word) < 180:
            chunk += " " + word
        else:
            chunks.append(chunk.strip())
            chunk = word
    if chunk:
        chunks.append(chunk.strip())
    
    audio_parts = []
    for i, chunk in enumerate(chunks[:5]):  # max 5 chunks
        part_path = os.path.join(os.path.dirname(path), f"part_{i}.mp3")
        url = f"https://translate.google.com/translate_tts?ie=UTF-8&q={requests.utils.quote(chunk)}&tl=en&client=tw-ob"
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
        if r.status_code == 200:
            open(part_path, "wb").write(r.content)
            audio_parts.append(part_path)
    
    if not audio_parts:
        raise Exception("Google TTS failed — no audio generated")
    
    if len(audio_parts) == 1:
        shutil.copy(audio_parts[0], path)
    else:
        # Merge all parts
        ff = get_ff("ffmpeg")
        concat = path + "_concat.txt"
        with open(concat, "w") as f:
            for p in audio_parts:
                f.write(f"file '{p}'\n")
        subprocess.run([ff, "-y", "-f", "concat", "-safe", "0", "-i", concat, "-c", "copy", path], check=True, capture_output=True)

@app.route("/health")
def health():
    try:
        v = subprocess.run([get_ff("ffmpeg"), "-version"], capture_output=True, text=True).stdout.split('\n')[0]
    except:
        v = "not found"
    return {"status": "ok", "ffmpeg": v, "tts": "google"}

@app.route("/create-video", methods=["POST"])
def create_video():
    job_id  = str(uuid.uuid4())[:8]
    job_dir = os.path.join(WORK_DIR, job_id)
    os.makedirs(job_dir, exist_ok=True)
    try:
        data        = request.json
        image_urls  = data.get("image_urls", [])
        script_text = data.get("script_text", "")
        audio_b64   = data.get("audio_base64", "")

        if not image_urls:
            return {"error": "No image_urls"}, 400
        if not script_text and not audio_b64:
            return {"error": "Provide script_text or audio_base64"}, 400

        ff = get_ff("ffmpeg")
        fp = get_ff("ffprobe")

        # 1. Audio — Google TTS ya base64
        audio_path = os.path.join(job_dir, "audio.mp3")
        if audio_b64:
            open(audio_path, "wb").write(base64.b64decode(audio_b64))
        else:
            google_tts(script_text, audio_path)

        # 2. Download images
        image_paths = []
        for i, url in enumerate(image_urls[:5]):
            p = os.path.join(job_dir, f"img_{i:02d}.jpg")
            r = requests.get(url, timeout=30)
            open(p, "wb").write(r.content)
            image_paths.append(p)

        # 3. Audio duration
        probe    = subprocess.run([fp, "-v", "quiet", "-print_format", "json", "-show_format", audio_path], capture_output=True, text=True)
        duration = float(json.loads(probe.stdout)["format"]["duration"])

        # 4. Concat
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

        video_b64 = base64.b64encode(open(out,"rb").read()).decode()
        shutil.rmtree(job_dir, ignore_errors=True)
        return {"status": "success", "job_id": job_id, "video_base64": video_b64, "duration": duration}

    except subprocess.CalledProcessError as e:
        shutil.rmtree(job_dir, ignore_errors=True)
        return {"error": "FFmpeg failed", "details": e.stderr.decode()}, 500
    except Exception as e:
        shutil.rmtree(job_dir, ignore_errors=True)
        return {"error": str(e)}, 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
