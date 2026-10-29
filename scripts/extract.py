# -*- coding: utf-8 -*-
"""幻灯片视频抽页。

按画面切换切页，丢掉翻页中的模糊帧，每页只留稳定后的一张。
不猜站点签名：下载交给本机已安装的 yt-dlp，失败就停，不在这里重写反爬。

用法:
  python extract.py --input <本地视频或公开链接> --out <目录>
  python extract.py --selftest
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

SCENE_THRESHOLD = 0.28
SETTLE_SEC = 0.35
TAIL_SEC = 0.12
MIN_WINDOW = 0.45
SHARP_FLOOR_RATIO = 0.35
DEDUP_MAD = 6.0
MAX_SLIDES = 80
GRAY_W, GRAY_H = 160, 90
# 口播视频的字幕条贴在画面顶部和底部。整帧一比较，字幕换一行就被当成翻页。
# 切页只看中间这一段；留存的帧仍是完整画面，字幕本身不裁掉。
CROP_TOP = 0.18
CROP_BOTTOM = 0.72


def run(cmd, timeout=600):
    return subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )


def tool(name):
    return shutil.which(name)


def require_ffmpeg():
    if tool("ffmpeg") and tool("ffprobe"):
        return
    sys.stderr.write("缺 ffmpeg/ffprobe。安装后重跑：winget install Gyan.FFmpeg\n")
    sys.exit(2)


def duration_of(path):
    r = run([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "csv=p=0", path,
    ])
    text = (r.stdout or b"").decode("utf-8", "replace").strip()
    try:
        return float(text)
    except ValueError:
        sys.stderr.write("ffprobe 读不到时长：%s\n%s\n" % (path, r.stderr.decode("utf-8", "replace")[:400]))
        sys.exit(2)


def scene_cuts(path, threshold):
    """返回画面突变的时间点（秒）。比较前先裁掉顶部和底部字幕带。"""
    vf = "crop=iw:ih*%.2f:0:ih*%.2f,select='gt(scene,%.4f)',showinfo" % (
        CROP_BOTTOM - CROP_TOP, CROP_TOP, threshold)
    r = run(["ffmpeg", "-hide_banner", "-i", path, "-vf", vf, "-fps_mode", "vfr", "-f", "null", "-"])
    err = (r.stderr or b"").decode("utf-8", "replace")
    times = []
    for m in re.finditer(r"pts_time:([0-9.]+)", err):
        t = float(m.group(1))
        if not times or t - times[-1] > 0.05:
            times.append(t)
    return times


def sample_times(duration, cuts, settle, tail):
    """每一段稳定画面取临近段尾的时间：翻页动画已经结束，本页内容已经齐。"""
    bounds = [0.0] + [c for c in cuts if 0 < c < duration] + [duration]
    picked = []
    for a, b in zip(bounds, bounds[1:]):
        if b - a < MIN_WINDOW:
            continue
        t = min(b - tail, b - 0.04)
        t = max(t, a + settle if a > 0 else 0.15)
        if t >= b:
            t = (a + b) / 2.0
        t = max(0.0, min(t, max(0.0, duration - 0.04)))
        picked.append(round(t, 3))
    if not picked and duration > 0:
        picked.append(round(min(0.5, duration / 2.0), 3))
    return picked


def grab(video, t, dest):
    r = run([
        "ffmpeg", "-hide_banner", "-y", "-i", video, "-ss", "%.3f" % t,
        "-frames:v", "1", "-q:v", "2", dest,
    ], timeout=120)
    return r.returncode == 0 and os.path.isfile(dest) and os.path.getsize(dest) > 0


def gray(path):
    """缩放成灰度，并裁掉字幕带。去重和清晰度都只看画面主体。"""
    r = run([
        "ffmpeg", "-hide_banner", "-i", path,
        "-vf", "crop=iw:ih*%.2f:0:ih*%.2f,scale=%d:%d,format=gray" % (
            CROP_BOTTOM - CROP_TOP, CROP_TOP, GRAY_W, GRAY_H),
        "-f", "rawvideo", "-v", "error", "-",
    ], timeout=60)
    buf = r.stdout or b""
    need = GRAY_W * GRAY_H
    if len(buf) < need:
        return None
    return buf[:need]


def sharpness(buf):
    """拉普拉斯方差。纯色图天然接近 0，文字和图表会高。"""
    w, h = GRAY_W, GRAY_H
    acc = 0.0
    n = 0
    for y in range(1, h - 1):
        row = y * w
        for x in range(1, w - 1):
            i = row + x
            v = (buf[i - w] + buf[i - 1] + buf[i + 1] + buf[i + w]) - 4 * buf[i]
            acc += v * v
            n += 1
    return acc / n if n else 0.0


def mad(a, b):
    return sum(abs(x - y) for x, y in zip(a, b)) / len(a)


def keep_frames(items):
    """items: [{t, path, sharp, gray}]。丢掉明显发虚的，同一页的重复只留更清晰的后一张。"""
    if not items:
        return [], []
    scores = sorted(it["sharp"] for it in items)
    median = scores[len(scores) // 2]
    floor = median * SHARP_FLOOR_RATIO
    dropped = []
    kept = []
    for it in items:
        if it["sharp"] < floor and it["sharp"] < median:
            it["why"] = "模糊（清晰度 %.0f < 中位 %.0f 的 %.0f%%）" % (
                it["sharp"], median, SHARP_FLOOR_RATIO * 100)
            dropped.append(it)
            continue
        if kept and mad(kept[-1]["gray"], it["gray"]) < DEDUP_MAD:
            if it["sharp"] >= kept[-1]["sharp"]:
                kept[-1]["why"] = "与后一张是同一页，留下更清晰的"
                dropped.append(kept[-1])
                kept[-1] = it
            else:
                it["why"] = "与前一张是同一页"
                dropped.append(it)
            continue
        kept.append(it)
    return kept, dropped


def newest_media(outdir, since):
    exts = (".mp4", ".mkv", ".webm", ".flv", ".mov")
    found = []
    for dirpath, _, names in os.walk(outdir):
        for name in names:
            if not name.lower().endswith(exts):
                continue
            path = os.path.join(dirpath, name)
            try:
                if os.path.getmtime(path) >= since - 1 and os.path.getsize(path) > 0:
                    found.append(path)
            except OSError:
                continue
    if not found:
        return None
    found.sort(key=os.path.getmtime)
    return found[-1]


def download_ytdlp(url, outdir):
    ytdlp = tool("yt-dlp") or [sys.executable, "-m", "yt_dlp"]
    if isinstance(ytdlp, str):
        ytdlp = [ytdlp]
    dest = os.path.join(outdir, "source.%(ext)s")
    r = run(ytdlp + [
        "--no-playlist", "--no-warnings",
        "-f", "bv*[height<=1080]+ba/b[height<=1080]/b",
        "--merge-output-format", "mp4",
        "-o", dest, url,
    ], timeout=1800)
    err = (r.stderr or b"").decode("utf-8", "replace")
    return r.returncode, err


def download_yutto(url, outdir):
    """B 站专用。不带登录参数：公开视频能下，会员和私密的下不了就停。"""
    cmd = [sys.executable, "-m", "yutto", "download", "--no-subtitle", "--no-danmaku",
           "--no-color", "--no-progress", "-d", outdir, url]
    r = run(cmd, timeout=1800)
    err = (r.stderr or b"").decode("utf-8", "replace")
    out = (r.stdout or b"").decode("utf-8", "replace")
    return r.returncode, err + "\n" + out


def download_douyin(url, outdir):
    """公开页面的 HTML 里就有播放地址。无头浏览器打开一次，不带登录状态。
    页面没给地址就返回 None，交给调用方停下来。"""
    chrome = "C:/Program Files/Google/Chrome/Application/chrome.exe"
    if not os.path.isfile(chrome):
        return None
    work = tempfile.mkdtemp(prefix="douyin-")
    html = os.path.join(work, "page.html")
    try:
        r = run([
            chrome, "--headless=new", "--disable-gpu", "--user-data-dir=" + work,
            "--virtual-time-budget=12000", "--dump-dom", url,
        ], timeout=60)
        text = (r.stdout or b"").decode("utf-8", "replace")
        io_open = open(html, "w", encoding="utf-8")
        io_open.write(text)
        io_open.close()
        found = re.findall(r"https?://[^\"']*douyinvod[^\"']*", text)
        if not found:
            return None
        src = found[0].replace("&amp;", "&")
        dest = os.path.join(outdir, "source.mp4")
        d = run([
            "curl", "-L", "--fail", "--retry", "2", "-A", "Mozilla/5.0",
            "-e", "https://www.douyin.com/", "-o", dest, src,
        ], timeout=300)
        if d.returncode == 0 and os.path.isfile(dest) and os.path.getsize(dest) > 0:
            return dest
        return None
    finally:
        shutil.rmtree(work, ignore_errors=True)


def download(url, outdir):
    started = os.path.getmtime(outdir) if os.path.isdir(outdir) else 0
    mark = max(started, 0)
    host = re.sub(r"^https?://", "", url).split("/")[0].lower()
    bilibili = "bilibili.com" in host or host.endswith("b23.tv")
    douyin = "douyin.com" in host
    if douyin:
        got = download_douyin(url, outdir)
        if got:
            return got
        sys.stderr.write("抖音公开页面没有给出播放地址。私密、要登录、已删除的视频停在这里。\n")
        sys.exit(2)
    code, err = download_ytdlp(url, outdir)
    got = newest_media(outdir, mark)
    if code == 0 and got:
        return got
    if bilibili:
        sys.stderr.write("yt-dlp 没拿到 B 站视频，改用 yutto。\n")
        code2, err2 = download_yutto(url, outdir)
        got = newest_media(outdir, mark)
        if code2 == 0 and got:
            return got
        sys.stderr.write("yutto 也失败。公开视频才会继续；会员、要登录、私密作品停在这里。\n%s\n" % err2[-800:])
        sys.exit(2)
    sys.stderr.write("下载失败，停。不改写站点签名，也不收登录 cookie。\n%s\n" % err[-800:])
    sys.exit(2)


def stamp(t):
    t = max(0, int(round(t)))
    return "%02d:%02d:%02d" % (t // 3600, (t % 3600) // 60, t % 60)


def transcribe(video, outdir):
    """平台没有字幕时才走这里。模型在本机，不把音频送出去。"""
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        sys.stderr.write("没有 faster-whisper，跳过口播转写。画面索引照常写。\n")
        return None
    model = WhisperModel("small", device="cpu", compute_type="int8")
    segments, info = model.transcribe(video, language="zh", vad_filter=True)
    rows = [{"start": s.start, "end": s.end, "text": s.text.strip()} for s in segments]
    lines = ["[%.1f-%.1f] %s" % (r["start"], r["end"], r["text"]) for r in rows]
    path = os.path.join(outdir, "speech.txt")
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")
    return {"path": path, "lines": len(lines), "language": info.language, "rows": rows}


def span_text(rows, t):
    """取离这一帧最近的那一段口播。"""
    if not rows:
        return ""
    hit = min(rows, key=lambda r: abs((r["start"] + r["end"]) / 2.0 - t))
    return hit["text"]


def write_index(path, meta, kept, dropped, rows=None):
    lines = [
        "# 幻灯片",
        "",
        "- 来源：%s" % meta["input"],
        "- 时长：%s" % stamp(meta["duration"]),
        "- 切页阈值：%s（ffmpeg scene）" % meta["threshold"],
        "- 保留 %d 张，丢掉 %d 张" % (len(kept), len(dropped)),
        "",
        "读图时按下面的顺序读 `frames/`，不要把同一页的过渡帧再算进去。",
        "",
    ]
    for i, it in enumerate(kept, 1):
        name = "slide_%03d.jpg" % i
        lines += [
            "## %02d  %s" % (i, stamp(it["t"])),
            "",
            "![%s](frames/%s)" % (name, name),
            "",
            "时间 %.3f 秒，清晰度 %.0f。" % (it["t"], it["sharp"]),
            "",
        ]
        said = span_text(rows, it["t"])
        if said:
            lines += ["口播：%s" % said, ""]
    if dropped:
        lines.append("## 丢掉的帧")
        lines.append("")
        for it in dropped:
            lines.append("- %.3f 秒：%s" % (it["t"], it.get("why") or "未保留"))
        lines.append("")
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines))


def speech_spans(segments, duration):
    """把连着说的句子合成一段。停顿超过 0.8 秒，或者一连超过 12 秒，就另起一段。"""
    spans = []
    start = None
    end = None
    for seg in segments:
        if start is None:
            start, end = seg["start"], seg["end"]
            continue
        if seg["start"] - end > 0.8 or seg["end"] - start > 12:
            spans.append((start, end))
            start = seg["start"]
        end = seg["end"]
    if start is not None:
        spans.append((start, min(end, duration)))
    return spans


def talking_head(video, duration, segments):
    """同一镜头里人脸和手势一直在动，按画面切会把一句话切成十几张。
    这种视频改按口播的停顿分段，每段留一张。幻灯片切换不会触发这里：
    硬切的画面差异远大于同镜头里的表情变化。"""
    spans = speech_spans(segments, duration)
    if len(spans) < 2:
        return None
    return [round((a + b) / 2.0, 3) for a, b in spans]


def extract(video, outdir, threshold, settle, tail, limit, transcribe_audio=True):
    require_ffmpeg()
    os.makedirs(outdir, exist_ok=True)
    frame_dir = os.path.join(outdir, "frames")
    os.makedirs(frame_dir, exist_ok=True)
    dur = duration_of(video)
    cuts = scene_cuts(video, threshold)
    times = sample_times(dur, cuts, settle, tail)
    if len(times) > limit:
        sys.stderr.write(
            "候选 %d 超过上限 %d。阈值 %.2f 对这个视频太敏感，提高到 %.2f 重跑。\n"
            % (len(times), limit, threshold, min(0.6, threshold + 0.12))
        )
        sys.exit(2)
    tmp = tempfile.mkdtemp(prefix="slide-")
    items = []
    try:
        for i, t in enumerate(times):
            dest = os.path.join(tmp, "c_%03d.jpg" % i)
            if not grab(video, t, dest):
                continue
            g = gray(dest)
            if g is None:
                continue
            items.append({"t": t, "path": dest, "sharp": sharpness(g), "gray": g})
        kept, dropped = keep_frames(items)
        for i, it in enumerate(kept, 1):
            shutil.copyfile(it["path"], os.path.join(frame_dir, "slide_%03d.jpg" % i))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    meta = {"input": video, "duration": dur, "threshold": threshold, "cuts": len(cuts)}
    speech = transcribe(video, outdir) if transcribe_audio else None
    rows = speech["rows"] if speech else None
    write_index(os.path.join(outdir, "index.md"), meta, kept, dropped, rows)
    # 候选帧比口播段数多出一倍以上，说明切页被同镜头里的表情和手势带动了。
    # 改按口播停顿重新取帧。幻灯片视频的候选数和页数接近，不会走到这里。
    if speech and speech["rows"]:
        spans = speech_spans(speech["rows"], dur)
        if len(times) > max(6, len(spans) * 2):
            times = [round((a + b) / 2.0, 3) for a, b in spans]
            shutil.rmtree(frame_dir, ignore_errors=True)
            os.makedirs(frame_dir, exist_ok=True)
            tmp2 = tempfile.mkdtemp(prefix="slide-")
            try:
                items = []
                for i, t in enumerate(times):
                    dest = os.path.join(tmp2, "c_%03d.jpg" % i)
                    if not grab(video, t, dest):
                        continue
                    g = gray(dest)
                    items.append({"t": t, "path": dest, "sharp": sharpness(g) if g else 0, "gray": g or b""})
                kept, dropped = items, []
                for i, it in enumerate(kept, 1):
                    shutil.copyfile(it["path"], os.path.join(frame_dir, "slide_%03d.jpg" % i))
            finally:
                shutil.rmtree(tmp2, ignore_errors=True)
            meta["cuts"] = len(spans)
            write_index(os.path.join(outdir, "index.md"), meta, kept, dropped, rows)
    result = {"kept": len(kept), "dropped": len(dropped), "cuts": len(cuts), "duration": dur}
    if speech:
        result["speech_lines"] = speech["lines"]
    return result


def selftest():
    require_ffmpeg()
    root = tempfile.mkdtemp(prefix="slide-selftest-")
    try:
        slides = os.path.join(root, "slides.mp4")
        blur = os.path.join(root, "blur.mp4")
        font = "C\\\\:/Windows/Fonts/arial.ttf"
        parts = []
        for i, (color, word) in enumerate((("0x1a3a5c", "SLIDE ONE"), ("0xc45c26", "SLIDE TWO"), ("0x1f6b4a", "SLIDE THREE")), 1):
            part = os.path.join(root, "p%d.mp4" % i)
            run([
                "ffmpeg", "-hide_banner", "-y", "-f", "lavfi",
                "-i", "color=c=%s:s=640x360:d=2" % color,
                "-vf", "drawtext=fontfile=%s:text='%s':fontsize=48:fontcolor=white:x=40:y=140" % (font, word),
                "-pix_fmt", "yuv420p", part,
            ])
            parts.append(part)
        lst = os.path.join(root, "list.txt")
        with open(lst, "w", encoding="utf-8") as f:
            for p in parts:
                f.write("file '%s'\n" % p.replace("\\", "/"))
        run(["ffmpeg", "-hide_banner", "-y", "-f", "concat", "-safe", "0", "-i", lst, "-c", "copy", slides])
        # 第二段整段抹糊，应当被清晰度过滤丢掉或不应单独成页
        run([
            "ffmpeg", "-hide_banner", "-y", "-i", slides,
            "-vf", "boxblur=lr=12:lp=2", "-c:v", "libx264", "-pix_fmt", "yuv420p", blur,
        ])
        out = os.path.join(root, "out")
        result = extract(slides, out, SCENE_THRESHOLD, SETTLE_SEC, TAIL_SEC, MAX_SLIDES,
                         transcribe_audio=False)
        n = result["kept"]
        frames = sorted(os.listdir(os.path.join(out, "frames")))
        index = open(os.path.join(out, "index.md"), encoding="utf-8").read()
        ok = n == 3 and frames == ["slide_001.jpg", "slide_002.jpg", "slide_003.jpg"] and "SLIDE" not in index
        # 文字在图里，不在 index 正文里编造；index 只记时间和清晰度
        sharp_slide = sharpness(gray(os.path.join(out, "frames", "slide_001.jpg")))
        # 糊视频：三页内容被抹掉，清晰度应明显低于清晰版
        out2 = os.path.join(root, "out2")
        result2 = extract(blur, out2, SCENE_THRESHOLD, SETTLE_SEC, TAIL_SEC, MAX_SLIDES,
                          transcribe_audio=False)
        # 同一背景、只有底部字幕在变：不应被切成多页。
        talking = os.path.join(root, "talking.mp4")
        run([
            "ffmpeg", "-hide_banner", "-y", "-f", "lavfi",
            "-i", "color=c=0x202020:s=640x360:d=4",
            "-vf", ("drawtext=fontfile=%s:text='SAME FACE':fontsize=42:fontcolor=white:x=40:y=150,"
                    "drawtext=fontfile=%s:text='caption one':fontsize=28:fontcolor=yellow:x=40:y=300:"
                    "enable='lt(t,2)',"
                    "drawtext=fontfile=%s:text='caption two is different':fontsize=28:fontcolor=yellow:x=40:y=300:"
                    "enable='gte(t,2)'") % (font, font, font),
            "-pix_fmt", "yuv420p", talking,
        ])
        out3 = os.path.join(root, "out3")
        result3 = extract(talking, out3, SCENE_THRESHOLD, SETTLE_SEC, TAIL_SEC, MAX_SLIDES,
                          transcribe_audio=False)
        sharp_blur = 0.0
        bdir = os.path.join(out2, "frames")
        if os.path.isdir(bdir) and os.listdir(bdir):
            sharp_blur = max(sharpness(gray(os.path.join(bdir, name))) for name in os.listdir(bdir))
        blur_ok = sharp_blur < sharp_slide * 0.5
        caption_ok = result3["kept"] == 1
        report = {
            "hard_cuts_kept": n,
            "hard_cuts_expected": 3,
            "sharp": round(sharp_slide, 1),
            "blur_max": round(sharp_blur, 1),
            "blur_lower": blur_ok,
            "caption_change_kept": result3["kept"],
            "pass": bool(ok and blur_ok and caption_ok),
        }
        sys.stdout.write(json.dumps(report, ensure_ascii=False) + "\n")
        return 0 if report["pass"] else 1
    finally:
        shutil.rmtree(root, ignore_errors=True)


def main(argv):
    p = argparse.ArgumentParser(description="按画面切换从视频里抽出幻灯片")
    p.add_argument("--input", help="本地视频，或公开视频链接")
    p.add_argument("--out", help="输出目录")
    p.add_argument("--threshold", type=float, default=SCENE_THRESHOLD)
    p.add_argument("--settle", type=float, default=SETTLE_SEC)
    p.add_argument("--tail", type=float, default=TAIL_SEC)
    p.add_argument("--limit", type=int, default=MAX_SLIDES)
    p.add_argument("--selftest", action="store_true")
    args = p.parse_args(argv)
    if args.selftest:
        return selftest()
    if not args.input or not args.out:
        p.print_help()
        return 2
    src = args.input
    os.makedirs(args.out, exist_ok=True)
    if re.match(r"https?://", src):
        src = download(src, args.out)
    elif not os.path.isfile(src):
        sys.stderr.write("找不到文件：%s\n" % src)
        return 2
    result = extract(src, args.out, args.threshold, args.settle, args.tail, args.limit)
    result["out"] = os.path.abspath(args.out)
    sys.stdout.write(json.dumps(result, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except subprocess.TimeoutExpired:
        sys.stderr.write("ffmpeg 超时。\n")
        sys.exit(2)
