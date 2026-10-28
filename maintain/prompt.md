你在维护公开仓库 https://github.com/benweilu316/slide-video，本地目录是 C:\Users\PC\Desktop\claude输出\slide-video-repo。

每次只做一轮，做完就停。

1. 先 `git pull`。看 GitHub 上有没有新的 issue 和评论（用 git 凭据访问 api.github.com，不要把令牌写进文件或远程地址）。
2. 社区评论里指出的问题优先。没有评论，就自己找一个真实的可改进点：切页漏页、同一页重复、模糊帧没丢掉、转写编码、下载失败后的行为。
3. 改进必须能用 `python scripts/extract.py --selftest` 验证。自检不过就撤回这次改动，不要提交。
4. 只在确有改进时提交并推送，提交说明写清改了什么、怎么验证的。没有可落地的改进就什么都不提交，在日志里写「今天没有改动」和原因。
5. 不改许可，不收 cookie，不加入绕过站点校验的代码，不把会员或私密视频纳入支持范围。
