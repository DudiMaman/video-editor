"""ffmpeg/ffprobe command builders and runners.

All clips destined for concatenation are re-encoded to a single canonical
format (H.264 yuv420p at the source's resolution/fps, AAC 44.1kHz stereo),
which makes the final concat a lossless stream-copy.
"""
import json
import subprocess
from pathlib import Path


class PipelineError(Exception):
    pass


ENCODE_ARGS = [
    "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
    "-pix_fmt", "yuv420p",
    "-c:a", "aac", "-ar", "44100", "-ac", "2",
    "-movflags", "+faststart",
]


def run(cmd: list[str], timeout: int = 1800) -> subprocess.CompletedProcess:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError as e:
        raise PipelineError(f"{cmd[0]} is not installed or not on PATH") from e
    except subprocess.TimeoutExpired as e:
        raise PipelineError(f"{cmd[0]} timed out after {timeout}s") from e
    if proc.returncode != 0:
        tail = "\n".join((proc.stderr or "").splitlines()[-30:])
        raise PipelineError(f"{cmd[0]} failed (exit {proc.returncode}):\n{tail}")
    return proc


def _parse_fps(rate: str | None) -> float:
    if not rate:
        return 30.0
    try:
        num, _, den = rate.partition("/")
        num_f = float(num)
        den_f = float(den) if den else 1.0
        if num_f <= 0 or den_f <= 0:
            return 30.0
        return num_f / den_f
    except ValueError:
        return 30.0


def probe(path: Path) -> dict:
    """Return {width, height, fps, has_audio, duration} for a media file."""
    proc = run([
        "ffprobe", "-v", "error", "-print_format", "json",
        "-show_streams", "-show_format", str(path),
    ], timeout=120)
    info = json.loads(proc.stdout)
    video = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), None)
    if video is None:
        raise PipelineError(f"no video stream found in {path.name}")
    has_audio = any(s.get("codec_type") == "audio" for s in info.get("streams", []))
    duration = None
    try:
        duration = float(info.get("format", {}).get("duration"))
    except (TypeError, ValueError):
        pass
    width = int(video["width"])
    height = int(video["height"])
    return {
        "width": width - width % 2,
        "height": height - height % 2,
        "fps": _parse_fps(video.get("avg_frame_rate")) or 30.0,
        "has_audio": has_audio,
        "duration": duration,
    }


def _vf(spec: dict) -> str:
    w, h = spec["width"], spec["height"]
    return (
        f"fps={spec['fps']:.6f},"
        f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
        f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1"
    )


def encode_canonical(
    source: Path,
    out: Path,
    spec: dict,
    has_audio: bool,
    cut_seconds: float | None = None,
) -> None:
    """Re-encode `source` to the canonical format; optionally trim to `cut_seconds`.

    Used both for the frame-accurate trim of the main clip and for outro
    normalization (cut_seconds=None). Silent sources get a silent audio bed so
    concat audio stays consistent.
    """
    cmd = ["ffmpeg", "-y", "-i", str(source)]
    if has_audio:
        maps = ["-map", "0:v:0", "-map", "0:a:0"]
    else:
        cmd += ["-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo"]
        maps = ["-map", "0:v:0", "-map", "1:a:0"]
    cmd += maps + ["-vf", _vf(spec)] + ENCODE_ARGS + ["-shortest"]
    if cut_seconds is not None:
        cmd += ["-t", f"{cut_seconds:.3f}"]
    cmd.append(str(out))
    run(cmd)


def concat(parts: list[Path], out: Path) -> None:
    """Concatenate uniformly-encoded parts. Demuxer + stream copy is instant
    and lossless; fall back to a re-encoding concat filter on failure."""
    list_path = out.parent / "list.txt"
    list_path.write_text("".join(f"file '{p.resolve()}'\n" for p in parts))
    try:
        run([
            "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(list_path),
            "-c", "copy", "-movflags", "+faststart", str(out),
        ])
    except PipelineError:
        n = len(parts)
        inputs: list[str] = []
        for p in parts:
            inputs += ["-i", str(p)]
        streams = "".join(f"[{i}:v][{i}:a]" for i in range(n))
        run([
            "ffmpeg", "-y", *inputs,
            "-filter_complex", f"{streams}concat=n={n}:v=1:a=1[v][a]",
            "-map", "[v]", "-map", "[a]", *ENCODE_ARGS, str(out),
        ])


def extract_frames(video: Path, out_dir: Path, duration: float) -> list[Path]:
    """Extract a few representative JPEG frames spread across the clip."""
    out_dir.mkdir(parents=True, exist_ok=True)
    fractions = [0.10, 0.40, 0.70, 0.90] if duration >= 4 else [0.25, 0.75]
    frames = []
    for i, frac in enumerate(fractions):
        out = out_dir / f"frame_{i}.jpg"
        run([
            "ffmpeg", "-y", "-ss", f"{duration * frac:.3f}", "-i", str(video),
            "-frames:v", "1", "-q:v", "3",
            "-vf", "scale='min(1024,iw)':-2", str(out),
        ], timeout=120)
        if out.exists() and out.stat().st_size > 0:
            frames.append(out)
    if not frames:
        raise PipelineError("frame extraction produced no frames")
    return frames
