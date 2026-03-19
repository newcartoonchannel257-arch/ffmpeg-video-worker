from flask import Flask, request, jsonify
import subprocess
import os
import uuid
import requests
import base64
import shutil
import json
import asyncio
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

async def generate_tts(text, voice, output_path):
    communicate = edge_tts.Communicate(text, voice)
    await communicate.save(output_path)

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "message": "FFmpeg worker + Edge-TTS running", "voices": list(VOICES.keys())})

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
        audio_b64   = data.get("audio_base64", "")

        if not image_urls:
            return jsonify({"error": "No image_urls provided"}), 400
        if not script_text and not audio_b64:
            return jsonify({"error": "Provide script_text or audio_base64"}), 400

        # 1. Generate audio via Edge-TTS
        audio_path = os.path.join(job_dir, "audio.mp3")
        if script_text and not audio_b64:
            voice = VOICES.get(language, VOICES["english"])
            asyncio.run(generate_tts(script_text, voice, audio_path))
        else:
            with open(audio_path, "wb") as f:
                f.write(base64.b64decode(audio_b64))

        # 2. Download images
        image_paths = []
        for i, url in enumerate(image_urls[:5]):
            img_path = os.path.join(job_dir, f"img_{i:02d}.jpg")
            r = requests.get(url, timeout=30)
            with open(img_path, "wb") as f:
                f.write(r.content)
            image_paths.append(img_path)

        # 3. Get audio duration
        probe      = subprocess.run(["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", audio_path], capture_output=True, text=True)
        probe_data = json.loads(probe.stdout)
        duration   = float(probe_data["format"]["duration"])

        # 4. Slideshow concat
        img_dur     = duration / len(image_paths)
        concat_file = os.path.join(job_dir, "concat.txt")
        with open(concat_file, "w") as f:
            for p in image_paths:
                f.write(f"file '{p}'\nduration {img_dur:.2f}\n")
            f.write(f"file '{image_paths[-1]}'\n")

        slideshow = os.path.join(job_dir, "slideshow.mp4")
        subprocess.run([
            "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", concat_file,
            "-vf", "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,fps=30",
            "-c:v", "libx264", "-preset", "fast", "-crf", "23", "-pix_fmt", "yuv420p",
            slideshow
        ], check=True, capture_output=True)

        # 5. Merge audio + video
        output = os.path.join(job_dir, "final.mp4")
        subprocess.run([
            "ffmpeg", "-y", "-i", slideshow, "-i", audio_path,
            "-c:v", "copy", "-c:a", "aac", "-shortest", "-movflags", "+faststart",
            output
        ], check=True, capture_output=True)

        # 6. Return as base64
        with open(output, "rb") as f:
            video_b64 = base64.b64encode(f.read()).decode()

        shutil.rmtree(job_dir, ignore_errors=True)
        return jsonify({"status": "success", "job_id": job_id, "video_base64": video_b64, "duration": duration})

    except subprocess.CalledProcessError as e:
        shutil.rmtree(job_dir, ignore_errors=True)
        return jsonify({"error": "FFmpeg failed", "details": e.stderr.decode()}), 500
    except Exception as e:
        shutil.rmtree(job_dir, ignore_errors=True)
        return jsonify({"error": str(e)}), 500

@app.route("/tts-only", methods=["POST"])
def tts_only():
    job_id  = str(uuid.uuid4())[:8]
    job_dir = os.path.join(WORK_DIR, job_id)
    os.makedirs(job_dir, exist_ok=True)
    try:
        data     = request.json
        text     = data.get("text", "")
        language = data.get("language", "english")
        voice    = VOICES.get(language, VOICES["english"])
        audio_path = os.path.join(job_dir, "audio.mp3")
        asyncio.run(generate_tts(text, voice, audio_path))
        with open(audio_path, "rb") as f:
            audio_b64 = base64.b64encode(f.read()).decode()
        shutil.rmtree(job_dir, ignore_errors=True)
        return jsonify({"status": "success", "audio_base64": audio_b64, "language": language, "voice": voice})
    except Exception as e:
        shutil.rmtree(job_dir, ignore_errors=True)
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
