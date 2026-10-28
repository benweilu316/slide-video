---
name: slide-video
description: 从口播加幻灯片的视频里按画面切换抽出每一页，丢掉翻页模糊帧，每页留一张清晰图并写成带时间戳的 index.md。触发：视频链接转文字、B站、抖音、本地 mp4、幻灯片截图、不用手截、把视频里的 PPT 抽出来、slide video、yt-dlp。只处理公开视频或用户自己已有的文件。❌ 不适用：普通网页抓取→wechat-scrape；看一张已经截好的图→直接 Read；下载会员、登录后或他人私密视频（不做）。
---

# 幻灯片视频抽页

信息在画面上，不在字幕里。口播转写拿不到幻灯片上的字。按画面切换切页，翻页中间的模糊帧丢掉，每页只留稳定后的一张。

## 红线

1. 只处理公开链接，或用户已经下到本地的文件。会员、要登录、别人的私密作品：停下，说明原因，不绕登录，不收 cookie。
2. 站点签名失败就停，不在本技能里重写签名，不收 cookie。公开链接先走 yt-dlp；B 站失败时脚本自己改用 yutto 再试一次。两次都失败就停：会员、要登录、私密作品不继续。抖音没有公开接口可用时同样停，不接第三方签名服务。
3. 图里的字以读图为准。index.md 只记时间和清晰度，不把没读到的文字写进去。
4. 一张图没读出来就说没读出来，不按前后页补。

## 做法

```bash
python "<skill 目录>/scripts/extract.py" --input "<本地视频或公开链接>" --out "<输出目录>"
```

输出：

```
<输出目录>/frames/slide_001.jpg
<输出目录>/index.md
<输出目录>/speech.txt
```

`speech.txt` 是本机 faster-whisper small 的口播转写，只在平台没有字幕时生成，音频不外送。没装这个包就跳过，画面索引照常写。

然后按 index.md 的顺序 Read 每一张。总结必须同时用到口播和画面上的字：口播里的专名以画面文字核对，画面没有的句子不编。同一页只出现一次。

脚本做的事：ffmpeg 的 scene 分数找切换点（默认 0.28），每段取临近段尾的一帧（翻页动画已结束），拉普拉斯方差丢掉明显发虚的帧，相邻帧像素差很小则视为同一页只留更清晰的一张。候选超过 80 张就停，提示把阈值调高，不默默吐出几百张。

## 依赖

- ffmpeg、ffprobe。没有就先装再跑：`winget install Gyan.FFmpeg`
- 公开链接用 yt-dlp（`python -m yt_dlp`）。B 站失败自动改用 yutto（`python -m yutto`）。
- 口播转写用 faster-whisper（`python -m pip install faster-whisper`），模型 `small`，CPU int8。首次运行会下载约 480 MB。
- 本地文件不用下载器。

## 自检

```bash
python "<skill 目录>/scripts/extract.py" --selftest
```

合成三段各 2 秒的纯色加文字，硬切，应正好留 3 张。同一条片整体模糊后，清晰度应明显低于清晰版。输出 JSON 里 `pass` 为 true 才算过。

## 和现成工具的差别

BiliNote、openclaw-video-vision 按固定间隔截帧，同一页会重复，翻页中间的模糊帧也会留下。本技能按画面变化切，每页一张。reverse-skill 是安全研究的技能路由，不负责视频；这里只借鉴「工具缺失就明确说装什么、每步留可核对的记录」。
