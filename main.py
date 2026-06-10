"""
Unified Auto-Uploader: Instagram Reels + YouTube Shorts
---------------------------------------------------------
Flow for each video:
  1. List & sort .mp4 files from Google Drive (Reel project credentials)
  2. Download the first file locally
  3. Convert to 9:16 (1080x1920) with blurred background via FFmpeg
  4. Upload converted file to Cloudinary
  5. Publish to Instagram Reels via Cloudinary URL
  6. Upload converted file to YouTube Shorts (separate YouTube project credentials)
  7. Delete from Cloudinary
  8. Move original Drive file to trash
  9. Write full session log to logs/upload_YYYY-MM-DD_HH-MM-SS.txt

Auth notes:
  - Drive + Instagram → GOOGLE_TOKEN env var  (Reel project)
  - YouTube           → YOUTUBE_TOKEN env var  (YouTube project, separate Google Console project)
  - Both tokens are the full JSON content of their respective token.json files
"""

import os
import re
import time
import json
import shutil
import tempfile
import subprocess
import requests
import cloudinary
import cloudinary.uploader
from datetime import datetime

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload, MediaFileUpload

# ══════════════════════════════════════════════════════════
#  LOGGER — every print also writes to a session log file
# ══════════════════════════════════════════════════════════

SESSION_TIME = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
LOG_DIR      = "logs"
LOG_FILE     = os.path.join(LOG_DIR, f"upload_{SESSION_TIME}.txt")

os.makedirs(LOG_DIR, exist_ok=True)

_log_handle = open(LOG_FILE, "w", encoding="utf-8")

def log(msg=""):
    """Print to console AND write to session log file."""
    timestamp = datetime.now().strftime("%H:%M:%S")
    line = f"[{timestamp}] {msg}"
    print(line)
    _log_handle.write(line + "\n")
    _log_handle.flush()

def close_log():
    _log_handle.close()
    
# ══════════════════════════════════════════════════════════
#  LOGGER UPLOAD — upload log file to google drive
# ══════════════════════════════════════════════════════════

def upload_log_to_drive(drive):
    """Upload session log file to Google Drive logs folder."""
    print("☁️ Uploading log file to Google Drive...")

    LOGS_FOLDER_ID = "1bniekPJ8HPIGOHAJK602KuhmQ5XmWKfB"

    file_metadata = {
        "name": os.path.basename(LOG_FILE),
        "parents": [LOGS_FOLDER_ID]
    }

    media = MediaFileUpload(
        LOG_FILE,
        mimetype="text/plain"
    )

    uploaded = drive.files().create(
        body=file_metadata,
        media_body=media,
        fields="id,name"
    ).execute()

    print(f"✅ Log uploaded to Drive: {uploaded['name']}")


# ══════════════════════════════════════════════════════════
#  CONFIGURATION
# ══════════════════════════════════════════════════════════

# ── Google Drive ──────────────────────────────────────────
DRIVE_FOLDER_ID = "1SkQgsJRR9G3lRYQlFzyR3wXz8gyjg4l3"

# Drive + Instagram scopes (Reel project)
DRIVE_IG_SCOPES = ["https://www.googleapis.com/auth/drive"]

# YouTube scopes (YouTube project — separate Google Console project)
YOUTUBE_SCOPES  = ["https://www.googleapis.com/auth/youtube.upload"]

# ── Instagram ─────────────────────────────────────────────
IG_ACCESS_TOKEN = os.environ["IG_ACCESS_TOKEN"]
INSTAGRAM_ID    = os.environ["IG_ID"]


IG_CAPTION= """What starts as justice slowly turns into obsession… and that’s what makes Death Note one of the greatest psychological thriller anime of all time.

The battle between Light Yagami and L isn’t just about intelligence — it’s a war of ideology, ego, manipulation, and power. Every episode keeps raising the tension, every move feels like a chess match, and every scene reminds us why Death Note became a legendary anime worldwide.

This scene perfectly captures the dark atmosphere, genius writing, intense mind games, and iconic character development that made Death Note a masterpiece for anime fans.

🔥 Follow for more anime edits, viral anime moments, and legendary scenes.

#DeathNote #DeathNoteEdit #LightYagami #LLawliet #Kira #Ryuk #Anime #AnimeEdit #AnimeReels #AnimeScene #PsychologicalAnime #ThrillerAnime #AnimeFans #Otaku #Weeb #Manga #AnimeLover #AnimeCommunity #AnimeClips #AnimeMoments #JapaneseAnime #DarkAnime #MindGames #AnimeTrending #ViralAnime #AnimeAesthetic #AnimeShorts #AnimeVideo #AnimeContent #AnimeWorld"""


# ── YouTube ───────────────────────────────────────────────
YT_TITLE_SUFFIX   = " #Shorts"
YT_DESCRIPTION    = os.environ.get("YT_DESCRIPTION", (
    "Watch this Short!\n\n#Shorts #Short #Viral"
))
YT_TAGS           = ["Shorts", "Short", "Viral", "Trending"]
YT_CATEGORY_ID    = "22"           # 22 = People & Blogs
YT_PRIVACY        = "public"       # "public" | "private" | "unlisted"

# ── Cloudinary ────────────────────────────────────────────
cloudinary.config(
    cloud_name = os.environ["CLOUDINARY_CLOUD_NAME"],
    api_key    = os.environ["CLOUDINARY_API_KEY"],
    api_secret = os.environ["CLOUDINARY_API_SECRET"],
)


# ══════════════════════════════════════════════════════════
#  FFMPEG CHECK
# ══════════════════════════════════════════════════════════

def check_ffmpeg():
    if shutil.which("ffmpeg") is None:
        raise EnvironmentError(
            "ffmpeg not found!\n"
            "  Windows : https://ffmpeg.org/download.html\n"
            "  Mac     : brew install ffmpeg\n"
            "  Linux   : sudo apt install ffmpeg"
        )
    log("✅ ffmpeg found.")


# ══════════════════════════════════════════════════════════
#  AUTH — two separate Google projects
# ══════════════════════════════════════════════════════════

def get_drive_ig_service():
    """
    Drive + Instagram auth.
    Uses GOOGLE_TOKEN env var (Reel project token.json content).
    This account owns the Drive folder and Instagram API access.
    """
    creds = Credentials.from_authorized_user_info(
        json.loads(os.environ["GOOGLE_TOKEN"]),
        DRIVE_IG_SCOPES
    )
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())

    drive = build("drive", "v3", credentials=creds)
    return drive


def get_youtube_service():
    """
    YouTube auth.
    Uses YOUTUBE_TOKEN env var (YouTube project token.json content).
    Separate Google Console project — does NOT have Drive access.
    """
    creds = Credentials.from_authorized_user_info(
        json.loads(os.environ["YOUTUBE_TOKEN"]),
        YOUTUBE_SCOPES
    )
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())

    youtube = build("youtube", "v3", credentials=creds)
    return youtube


# ══════════════════════════════════════════════════════════
#  STEP 1 — LIST FILES FROM DRIVE
# ══════════════════════════════════════════════════════════

def sort_key(filename):
    """Sort by numeric pattern: '1 (2)_clip3' → (1, 2, 3). Others go last."""
    match = re.match(r"(\d+)\s*\((\d+)\)_clip(\d+)", filename)
    if match:
        return (int(match.group(1)), int(match.group(2)), int(match.group(3)))
    return (999, 999, 999)


def fetch_drive_videos(drive):
    """
    Fetch all .mp4 files from the Drive folder (with pagination).
    Returns them sorted by filename pattern, picks the FIRST one.
    """
    log("📂 Fetching video list from Google Drive...")

    query      = f"'{DRIVE_FOLDER_ID}' in parents and trashed=false"
    all_files  = []
    page_token = None

    while True:
        params = {
            "q":      query,
            "fields": "nextPageToken, files(id, name, mimeType)",
            "pageSize": 1000,
        }
        if page_token:
            params["pageToken"] = page_token

        results    = drive.files().list(**params).execute()
        all_files += results.get("files", [])
        page_token = results.get("nextPageToken")
        if not page_token:
            break

    # Filter to .mp4 by name (covers any MIME edge cases)
    mp4_files = [f for f in all_files if f["name"].lower().endswith(".mp4")]

    if not mp4_files:
        log("⚠️  No .mp4 files found in Drive folder.")
        return None

    sorted_files = sorted(mp4_files, key=lambda f: sort_key(f["name"]))
    target       = sorted_files[0]

    log(f"   Found {len(mp4_files)} file(s). Processing first: {target['name']}")
    return target


# ══════════════════════════════════════════════════════════
#  STEP 2 — DOWNLOAD FROM DRIVE
# ══════════════════════════════════════════════════════════

def download_from_drive(drive, file_id, file_name):
    """Download Drive file to a local temp .mp4. Returns temp file path."""
    log(f"⬇️  Downloading: {file_name}")
    request = drive.files().get_media(fileId=file_id)

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".mp4")
    downloader = MediaIoBaseDownload(tmp, request)

    done = False
    while not done:
        status, done = downloader.next_chunk()
        if status:
            log(f"   Download: {int(status.progress() * 100)}%")

    tmp.close()
    log(f"✅ Downloaded → {tmp.name}")
    return tmp.name


# ══════════════════════════════════════════════════════════
#  STEP 3 — FFMPEG: CONVERT TO 9:16 BLURRED BACKGROUND
# ══════════════════════════════════════════════════════════

def convert_to_vertical(input_path):
    """
    Convert video to 1080x1920 (9:16) with blurred background.

    Layout:
    ┌──────────────────┐
    │  blurred enlarged│  ← bg: original scaled to cover + gblur
    │  ┌────────────┐  │
    │  │  original  │  │  ← fg: original scaled to fit, centered
    │  │   video    │  │
    │  └────────────┘  │
    │  blurred enlarged│
    └──────────────────┘
    """
    log("🎨 Converting to 9:16 with blurred background...")

    out_tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".mp4")
    out_tmp.close()
    output_path = out_tmp.name

    W, H = 1080, 1920

    filtergraph = (
        f"[0:v]split=2[bg][fg];"
        # Background: scale to cover full canvas, then hard blur
        f"[bg]scale={W}:{H}:force_original_aspect_ratio=increase,"
        f"crop={W}:{H},"
        f"gblur=sigma=30[blurred];"
        # Foreground: scale to fit inside canvas, keep aspect ratio
        f"[fg]scale={W}:{H}:force_original_aspect_ratio=decrease[scaled];"
        # Overlay fg centered on blurred bg
        f"[blurred][scaled]overlay=(W-w)/2:(H-h)/2,"
        f"format=yuv420p[out]"
    )

    cmd = [
        "ffmpeg", "-y",
        "-i", input_path,
        "-filter_complex", filtergraph,
        "-map", "[out]",
        "-map", "0:a?",          # keep audio if present
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "23",
        "-c:a", "aac",
        "-b:a", "128k",
        "-movflags", "+faststart",
        output_path
    ]

    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    if result.returncode != 0:
        log(f"❌ FFmpeg failed:\n{result.stderr[-1500:]}")
        raise RuntimeError("FFmpeg conversion failed.")

    log(f"✅ Conversion done → {output_path}")
    return output_path


# ══════════════════════════════════════════════════════════
#  STEP 4 — UPLOAD TO CLOUDINARY
# ══════════════════════════════════════════════════════════

def upload_to_cloudinary(file_path):
    """Upload video to Cloudinary. Returns (secure_url, public_id)."""
    log("☁️  Uploading to Cloudinary...")

    result     = cloudinary.uploader.upload_large(file_path, resource_type="video")
    video_url  = result["secure_url"]
    public_id  = result["public_id"]

    log(f"✅ Cloudinary URL: {video_url}")
    return video_url, public_id


# ══════════════════════════════════════════════════════════
#  STEP 5 — PUBLISH TO INSTAGRAM REELS
# ══════════════════════════════════════════════════════════

def publish_instagram_reel(video_url):
    """
    Create an Instagram Reel container from Cloudinary URL,
    wait for processing, then publish. Returns post ID or None.
    """
    log("📸 Creating Instagram Reel container...")

    # Create container
    response = requests.post(
        f"https://graph.facebook.com/v20.0/{INSTAGRAM_ID}/media",
        data={
            "media_type":   "REELS",
            "video_url":    video_url,
            "caption":      IG_CAPTION,
            "access_token": IG_ACCESS_TOKEN,
        }
    )
    result      = response.json()
    creation_id = result.get("id")

    if not creation_id:
        log(f"❌ Instagram container creation failed: {result}")
        return None

    log(f"   Container ID: {creation_id}. Waiting for Instagram to process...")

    # Poll until ready
    for attempt in range(30):
        status_resp = requests.get(
            f"https://graph.facebook.com/v20.0/{creation_id}",
            params={"fields": "status_code", "access_token": IG_ACCESS_TOKEN}
        )
        status = status_resp.json().get("status_code")
        log(f"   Attempt {attempt + 1}/30: status = {status}")

        if status == "FINISHED":
            break
        elif status == "ERROR":
            log("❌ Instagram processing error.")
            return None

        time.sleep(10)
    else:
        log("❌ Instagram processing timed out (5 minutes).")
        return None

    # Publish
    log("📤 Publishing Reel...")
    pub_resp = requests.post(
        f"https://graph.facebook.com/v20.0/{INSTAGRAM_ID}/media_publish",
        data={"creation_id": creation_id, "access_token": IG_ACCESS_TOKEN}
    )
    pub_result = pub_resp.json()
    post_id    = pub_result.get("id")

    if post_id:
        log(f"✅ Instagram Reel published! Post ID: {post_id}")
    else:
        log(f"❌ Instagram publish failed: {pub_result}")

    return post_id


# ══════════════════════════════════════════════════════════
#  STEP 6 — UPLOAD TO YOUTUBE SHORTS
# ══════════════════════════════════════════════════════════

def publish_youtube_short(youtube, file_path, file_name):
    """Upload local converted video to YouTube as a Short. Returns video ID or None."""
    
    base_name = os.path.splitext(file_name)[0]

    # Extract episode number from brackets
    episode_match = re.search(r"\((\d+)\)", base_name)

    # Extract part number after 'clip'
    part_match = re.search(r"clip(\d+)", base_name)

    episode_no = episode_match.group(1) if episode_match else "0"
    part_no = part_match.group(1) if part_match else "0"

    title = f"Episode {episode_no} Part {part_no}{YT_TITLE_SUFFIX}"

    body = {
        "snippet": {
            "title":       title[:100],   # YouTube max title = 100 chars
            "description": YT_DESCRIPTION,
            "tags":        YT_TAGS,
            "categoryId":  YT_CATEGORY_ID,
        },
        "status": {
            "privacyStatus":          YT_PRIVACY,
            "selfDeclaredMadeForKids": False,
        },
    }

    media = MediaFileUpload(
        file_path,
        mimetype="video/mp4",
        resumable=True,
        chunksize=5 * 1024 * 1024,   # 5 MB chunks
    )

    log(f"📤 Uploading to YouTube Shorts: {title}")
    request  = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
    response = None

    while response is None:
        status, response = request.next_chunk()
        if status:
            log(f"   YouTube upload: {int(status.progress() * 100)}%")

    video_id = response.get("id")
    if video_id:
        log(f"✅ YouTube Short published! https://www.youtube.com/shorts/{video_id}")
    else:
        log(f"❌ YouTube upload failed: {response}")

    return video_id


# ══════════════════════════════════════════════════════════
#  STEP 7 — DELETE FROM CLOUDINARY
# ══════════════════════════════════════════════════════════

def delete_from_cloudinary(public_id):
    """Remove video from Cloudinary after both platforms are done."""
    log(f"🗑️  Deleting from Cloudinary: {public_id}")
    cloudinary.uploader.destroy(public_id, resource_type="video")
    log("✅ Cloudinary file deleted.")


# ══════════════════════════════════════════════════════════
#  STEP 8 — TRASH ON DRIVE
# ══════════════════════════════════════════════════════════

def trash_drive_file(drive, file_id, file_name):
    """Move the processed Drive file to trash."""
    drive.files().update(fileId=file_id, body={"trashed": True}).execute()
    log(f"🗑️  '{file_name}' moved to Drive trash.")


# ══════════════════════════════════════════════════════════
#  CLEANUP TEMP FILES
# ══════════════════════════════════════════════════════════

def cleanup(*paths):
    for path in paths:
        if path and os.path.exists(path):
            try:
                os.remove(path)
                log(f"🧹 Deleted temp file: {path}")
            except Exception as e:
                log(f"⚠️  Could not delete {path}: {e}")


# ══════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════

def main():
    log("=" * 55)
    log("  🎬 Unified Uploader: Instagram Reels + YouTube Shorts")
    log(f"  Session: {SESSION_TIME}")
    log("=" * 55)

    # ── Pre-flight checks ────────────────────────────────
    check_ffmpeg()

    # ── Authenticate both services ───────────────────────
    log("\n🔐 Authenticating Drive / Instagram (Reel project)...")
    drive = get_drive_ig_service()

    log("🔐 Authenticating YouTube (YouTube project)...")
    youtube = None
    try:
        youtube = get_youtube_service()
    except Exception as e:
        log(f"⚠️  YouTube auth failed — YouTube upload will be skipped. Reason: {e}")

    # ── Fetch first video from Drive ─────────────────────
    log("\n" + "─" * 55)
    target = fetch_drive_videos(drive)
    if not target:
        log("Nothing to do. Exiting.")
        _log_handle.flush()
        upload_log_to_drive(drive)
        close_log()
        return

    file_id   = target["id"]
    file_name = target["name"]

    # Tracking vars for cleanup
    raw_path       = None
    converted_path = None
    cloudinary_id  = None
    ig_post_id     = None
    yt_video_id    = None

    try:
        # ── Step 2: Download ──────────────────────────────
        # Critical — raises if fails, can't continue without the file
        log("\n" + "─" * 55)
        raw_path = download_from_drive(drive, file_id, file_name)

        # ── Step 3: FFmpeg convert ────────────────────────
        # Critical — raises if fails, can't upload without converted file
        log("\n" + "─" * 55)
        converted_path = convert_to_vertical(raw_path)

        # Raw download no longer needed after conversion
        cleanup(raw_path)
        raw_path = None

        # ── Step 4: Upload to Cloudinary ──────────────────
        # Instagram depends on this; YouTube does NOT (uses local file)
        log("\n" + "─" * 55)
        video_url     = None
        cloudinary_id = None
        try:
            video_url, cloudinary_id = upload_to_cloudinary(converted_path)
        except Exception as e:
            log(f"⚠️  Cloudinary upload failed — Instagram will be skipped. Reason: {e}")

        # ── Step 5: Publish Instagram Reel ────────────────
        # Only runs if Cloudinary succeeded; failure is isolated
        log("\n" + "─" * 55)
        if video_url:
            try:
                ig_post_id = publish_instagram_reel(video_url)
            except Exception as e:
                log(f"⚠️  Instagram upload failed — continuing. Reason: {e}")
                ig_post_id = None
        else:
            log("⏭️  Skipping Instagram — no Cloudinary URL available.")

        # ── Step 6: Upload YouTube Short ──────────────────
        # Fully independent — uses local file, not Cloudinary
        log("\n" + "─" * 55)
        if youtube is not None:
            try:
                yt_video_id = publish_youtube_short(youtube, converted_path, file_name)
            except Exception as e:
                log(f"⚠️  YouTube upload failed — continuing. Reason: {e}")
                yt_video_id = None
        else:
            log("⏭️  Skipping YouTube — auth was not available.")

        # ── Step 7: Delete from Cloudinary ────────────────
        # Runs regardless of social upload results
        log("\n" + "─" * 55)
        if cloudinary_id:
            try:
                delete_from_cloudinary(cloudinary_id)
                cloudinary_id = None
            except Exception as e:
                log(f"⚠️  Cloudinary delete failed — manual cleanup may be needed. Reason: {e}")

        # ── Step 8: Trash Drive file (only if at least one platform succeeded) ──
        log("\n" + "─" * 55)
        if ig_post_id or yt_video_id:
            try:
                trash_drive_file(drive, file_id, file_name)
            except Exception as e:
                log(f"⚠️  Could not trash Drive file — manual cleanup needed. Reason: {e}")
        else:
            log("⚠️  Both uploads failed — Drive file NOT trashed.")

    except Exception as e:
        log(f"\n❌ FATAL ERROR: {e}")
        import traceback
        log(traceback.format_exc())

    finally:
        # Always clean up temp files
        cleanup(raw_path, converted_path)
        # Best-effort Cloudinary cleanup if crash happened before Step 7
        if cloudinary_id:
            try:
                delete_from_cloudinary(cloudinary_id)
            except Exception:
                log("⚠️  Could not clean up Cloudinary after error.")

    # ── Summary ───────────────────────────────────────────
    log("\n" + "=" * 55)
    log("  📊 SESSION SUMMARY")
    log("=" * 55)
    log(f"  File processed : {file_name}")
    log(f"  Instagram Reel : {'✅ ' + str(ig_post_id) if ig_post_id else '❌ Failed'}")
    log(f"  YouTube Short  : {'✅ https://youtube.com/shorts/' + str(yt_video_id) if yt_video_id else '❌ Failed'}")
    log(f"  Drive file     : {'🗑️  Trashed' if ig_post_id or yt_video_id else '⚠️  Kept (both failed)'}")
    log(f"  Log saved to   : {LOG_FILE}")
    log("=" * 55)
    _log_handle.flush()
    upload_log_to_drive(drive)
    close_log()


if __name__ == "__main__":
    main()
