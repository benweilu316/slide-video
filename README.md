# slide-video

从一条公开视频里抽出幻灯片，并转写口播。按画面切换切页，翻页中的模糊帧丢掉，每页只留稳定后的一张。

给 Claude Code 用的 skill。`SKILL.md` 放进 `~/.claude/skills/slide-video/` 就能被调用。脚本也可以单独跑。

## 它做的事

1. 公开链接先用 yt-dlp 下载。B 站失败时自动改用 yutto 再试一次。
2. ffmpeg 的 scene 分数找切换点，每段取临近段尾的一帧。
3. 清晰度太低的帧丢掉，相邻两帧几乎一样就只留更清晰的一张。
4. 平台没有字幕时，用本机 faster-whisper 转写口播，音频不外送。

## 用法

```bash
python scripts/extract.py --input "<本地视频或公开链接>" --out "<输出目录>"
```

输出：

```
<输出目录>/frames/slide_001.jpg
<输出目录>/index.md
<输出目录>/speech.txt
```

`index.md` 只记时间和清晰度。画面上的字要另做读图，不要凭口播补。

## 依赖

- ffmpeg、ffprobe
- 公开链接：`pip install -U yt-dlp`。B 站备用：`pip install -U yutto`
- 口播转写：`pip install faster-whisper`，模型 `small`，CPU int8，首次运行下载约 480 MB

本地文件不需要下载器。

## 自检

```bash
python scripts/extract.py --selftest
```

合成三段各 2 秒的硬切画面，应正好留 3 张。整段抹糊后，清晰度应明显低于清晰版。输出 JSON 里 `pass` 为 true 才算过。

## 不做的事

只处理公开链接，或你已经下到本地的文件。会员、要登录、别人的私密作品，脚本会停。它不收 cookie，不重写站点签名，不接第三方签名服务。

抖音的下载器目前过不了站点校验。把视频先下到本地，再把文件路径传进来，切页和转写同样可用。

## 许可

MIT
