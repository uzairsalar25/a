"""
setup_check.py — "Doctor" — apni machine pe setup verify karein.

    python scripts/setup_check.py

Ye check karta hai: Python, zaroori packages, ffmpeg + ffprobe, NVENC (GPU),
folder structure aur .env — aur batata hai kya missing hai + kaise theek karein.
"""
from __future__ import annotations
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import ffmpeg_bin, ffprobe_bin, run, ROOT, ASSETS, INPUT  # noqa: E402

OK, WARN, BAD = "  ✅", "  ⚠️ ", "  ❌"


def check_python():
    v = sys.version_info
    ok = v >= (3, 9)
    print((OK if ok else BAD), f"Python {v.major}.{v.minor}.{v.micro}",
          "" if ok else "-> Python 3.9+ chahiye")
    return ok


def check_packages():
    all_ok = True
    for mod, pip_name, needed in [
        ("edge_tts", "edge-tts", True),
        ("faster_whisper", "faster-whisper", True),
        ("imageio_ffmpeg", "imageio-ffmpeg", False),
        ("requests", "requests", False),
    ]:
        try:
            __import__(mod)
            print(OK, f"package {pip_name}")
        except Exception:
            all_ok = all_ok and not needed
            print((BAD if needed else WARN), f"package {pip_name} missing",
                  f"-> pip install {pip_name}")
    return all_ok


def check_ffmpeg():
    ff = ffmpeg_bin()
    have = shutil.which("ffmpeg") is not None or "imageio" in ff
    if have:
        ver = run([ff, "-version"], check=False).stdout.splitlines()[0]
        print(OK, f"ffmpeg: {ver[:60]}")
    else:
        print(BAD, "ffmpeg missing -> install ffmpeg (see README)")
    fp = ffprobe_bin()
    if fp:
        print(OK, "ffprobe found")
    else:
        print(WARN, "ffprobe missing -> ffmpeg-parse fallback use hoga "
                    "(system ffmpeg install karna behtar hai)")
    return have


def check_nvenc():
    ff = ffmpeg_bin()
    enc = run([ff, "-hide_banner", "-encoders"], check=False).stdout or ""
    if "h264_nvenc" not in enc:
        print(WARN, "NVENC (h264_nvenc) is ffmpeg build me nahi — CPU libx264 "
                    "use hoga (slow). GPU chahiye to nvenc-enabled ffmpeg lein.")
        return False
    test = run([ff, "-hide_banner", "-f", "lavfi",
                "-i", "color=c=black:s=64x64:d=0.2",
                "-c:v", "h264_nvenc", "-f", "null", "-"], check=False)
    if test.returncode == 0:
        print(OK, "NVENC (NVIDIA GPU) working — fast render milega 🚀")
        return True
    print(WARN, "h264_nvenc listed hai par test encode fail — driver/CUDA "
                "issue? CPU libx264 pe fallback ho jaayega.")
    return False


def check_folders():
    ok = True
    for p in [INPUT, ASSETS / "member_clips", ASSETS / "backgrounds"]:
        if p.exists():
            print(OK, f"folder {p.relative_to(ROOT)}")
        else:
            ok = False
            print(BAD, f"folder missing: {p.relative_to(ROOT)}")
    # any input videos?
    vids = [f for f in INPUT.glob("*") if f.suffix.lower() in
            {".mp4", ".mov", ".mkv", ".webm", ".m4v"}]
    print((OK if vids else WARN),
          f"{len(vids)} input video(s) in input_videos/"
          + ("" if vids else " -> apni videos 01.mp4, 02.mp4 ... daalein"))
    # any broll?
    broll = list((ASSETS / "member_clips").rglob("*"))
    broll = [f for f in broll if f.is_file() and f.name != ".gitkeep"]
    print((OK if broll else WARN),
          f"{len(broll)} B-roll file(s) in assets/member_clips/"
          + ("" if broll else " -> member folders me photos/clips daalein"))
    return ok


def check_env():
    if (ROOT / ".env").exists():
        print(OK, ".env present")
    else:
        print(WARN, ".env missing -> cp .env.example .env (default sab FREE hai)")


def main():
    print("\n=== ROYAL NEWS EDITOR — SETUP CHECK ===\n")
    print("Core:")
    py = check_python()
    pk = check_packages()
    print("\nFFmpeg / GPU:")
    ff = check_ffmpeg()
    check_nvenc()
    print("\nProject:")
    check_folders()
    check_env()

    print("\n----------------------------------------")
    if py and pk and ff:
        print("  Sab core cheezein ready hain. Chalao:  python run_batch.py 1 5")
    else:
        print("  Kuch core cheezein missing hain (❌). Upar ke hints follow karein.")
    print("----------------------------------------\n")


if __name__ == "__main__":
    main()
