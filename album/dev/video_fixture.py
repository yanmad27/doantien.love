"""Chỉ dùng khi phát triển: dựng video thử H.264 + AAC (faststart) và poster JPEG tổng hợp bằng ffmpeg vào một thư mục tạm. Không commit nhị phân."""
import os
import subprocess


def make(dirpath, name="clip", secs=3, size="320x240"):
    mp4, jpg = os.path.join(dirpath, name + ".mp4"), os.path.join(dirpath, name + ".jpg")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"testsrc=size={size}:rate=15:duration={secs}", "-f", "lavfi", "-i", f"sine=frequency=440:duration={secs}",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-profile:v", "baseline", "-c:a", "aac", "-shortest", "-movflags", "+faststart", mp4], check=True)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", mp4, "-ss", "1", "-frames:v", "1", "-q:v", "4", jpg], check=True)
    return mp4, jpg


def make_big(dirpath, name="big"):
    """~15 MiB (nhiễu, crf thấp) để tải theo ≥ 2 mảnh 8 MiB."""
    mp4, jpg = os.path.join(dirpath, name + ".mp4"), os.path.join(dirpath, name + ".jpg")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=640x480:rate=15:duration=3,noise=alls=100:allf=t", "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-profile:v", "baseline", "-crf", "4", "-c:a", "aac", "-shortest", "-movflags", "+faststart", mp4], check=True)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", mp4, "-ss", "1", "-frames:v", "1", "-q:v", "4", jpg], check=True)
    return mp4, jpg
