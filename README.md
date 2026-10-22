# Tibo Codex 额度重置监控

产出方 WorkBuddy｜日期 2026-10-23｜状态 通道已实测打通，云端定时待补 workflow 权限后启用
来源：<https://tibo.modelyard.dev/zh/feed.xml>
仓库：<https://github.com/judy123violet-max/tibo-reset-watch>（公开，Actions 无限免费）

## 当前状态（2026-10-23 01:45）

- ✅ RSS 源可用、分类字段可读、脚本自测通过（播种 / 去重 / 过滤 / 排序）
- ✅ **Server酱 真实送达已打通**：`code 0 / error SUCCESS`，雅旭手机已确认收到；免费额度 **5 条/天**（雅旭确认）
- ✅ 端到端复验：模拟新公告 → 合并成 1 条推送 → 成功后去重生效（再跑一轮 `[idle] 无新条目`）
- ✅ 失败不吞消息：故意用错密钥验证过，推送失败时该批公告**不记为已读**，下一轮重试；每日失败超 6 次则熔断，防止 5 分钟一轮把接口刷爆
- ✅ 公开仓库已建，`SERVERCHAN_SENDKEY` 已写入 Secrets
- ⏳ 卡在 GitHub：本机 gh 的 OAuth token 缺 `workflow` scope，workflow 文件推不上去。
  需跑一次 `gh auth refresh -h github.com -s workflow`（浏览器点一下授权），之后即可推送并启用 5 分钟定时
- ❓ 未验证：Server酱 的推送接口从 GitHub 美国机房能不能通（国内直连没问题，出海未实测）

## 这玩意解决什么

Tibo（@thsottiaux，OpenAI Codex 团队）会在 X 上临时宣布「今天结束前全局重置 Codex 额度」，时间不固定。
[ModelYard](https://tibo.modelyard.dev/zh/) 把 @thsottiaux 的公开帖整理成了 RSS，还分了类（计划重置 / 重置完成 / 重置卡 / 产品更新 / 路线图线索 / 功能讨论）。

本脚本每 5 分钟读一次这个 RSS，只要有**新的重置类公告**，就推一条到你微信。电脑关机也照跑（跑在 GitHub 服务器上）。

## 三步部署

### 第 1 步：拿一个能推到你微信的 key（雅旭做，约 2 分钟）

推荐 **PushPlus**：微信扫码登录 → 复制 token。推送会以公众号消息形式进你微信。

- 官网 <https://www.pushplus.plus/> → 微信扫码登录 → 首页能看到你的 token
- 想走 ClawBot 渠道的话，在 PushPlus 后台把接收渠道切到 ClawBot，然后把仓库变量 `PUSHPLUS_CHANNEL` 设成 `clawbot`（这一步我没实测，按文档配）

备选：Server酱 <https://sct.ftqq.com/>（SendKey，接口已验证 `https://sctapi.ftqq.com/<key>.send` 可用，免费额度 5 条/天——额度以官网为准，我没实测），或企业微信群机器人 webhook（**注意：企业微信机器人发不到个人微信**）。

本地跑的时候凭证放 `.local.env`（已在 `.gitignore` 里）：

```
PUSHPLUS_TOKEN=xxx
```

然后 `set -a; . ./.local.env; set +a` 再跑脚本。

### 第 2 步：建仓库并塞 key（我可以代做，需你点头）

```bash
gh repo create tibo-reset-watch --private --source . --push
gh secret set PUSHPLUS_TOKEN      # 粘贴 token
```

私有仓库的 Actions 每月免费额度 2000 分钟，5 分钟一轮大约要 1500-2000 分钟，**贴着上限**。
要是想省，把 `watch.yml` 里的 cron 改成 `*/10` 或 `*/15`；或者建成公开仓库（公开仓库 Actions 无限免费，token 在 Secrets 里不会暴露）。

### 第 3 步：验一下

仓库 → Actions → tibo-reset-watch → Run workflow，看日志有没有 `[init] 首次运行，已播种 N 条基线`。之后等新公告就行。

想立刻确认通道通不通，在 workflow 里把命令临时改成 `python tibo_reset_watch.py --test`，会拿最新一条公告当测试消息推一次。

## 本地跑（可选，PC 开着时的兜底）

```bash
python tibo_reset_watch.py --dry-run   # 只看会推什么
python tibo_reset_watch.py --test      # 推一条测试消息
python tibo_reset_watch.py             # 跑一轮
```

环境变量（Git Bash / PowerShell 都可以）：

```
PUSHPLUS_TOKEN=xxx
```

⚠️ 别在这个目录里放 `.bat` / `.vbs` 启动器 —— 路径里带中文（`_agent协作\00-公共事务`），cmd 传参会转码损坏。
要挂 Windows 计划任务，把脚本复制到纯英文路径（如 `D:\Tools\tibo-watch\`）再挂。

## 已知限制（不粉饰）

0. **PushPlus 必须付费实名才能发**（2026-10-23 实测）：token 有效但账号未实名时返回 `code 905 未实名认证用户无法发送消息`，绕不过去，已改用 Server酱。
   **Server酱免费额度 5 条/天**（雅旭确认）——脚本每次把多条公告合并成一条推，正常一周用不到 5 条；真撞上密集公告，超额的推送会失败，脚本让那批公告保持未读、下轮重试，不静默吞掉。
1. **延迟**：GitHub Actions 的 cron 在高峰期会晚几分钟到十几分钟触发；RSS 本身也依赖 ModelYard 抓帖速度。所以是「几分钟到二十分钟级」，不是秒级。
2. **只覆盖公开公告**：ModelYard 抓的是 @thsottiaux 的公开发言。它自己的免责声明写明不读取个人账户余额。**「官方说重置」≠「你账号已到账」**，到账还得自己看 Codex 里剩多少。
3. **RSS 是第三方转载**：ModelYard 挂了/改结构，这个监控就哑了。所以脚本每次跑都会打印抓到几条，静默失败能在日志里看出来。
4. **分类靠 ModelYard 打的标签**：漏标就会漏推。用 `--all` 可以不分类型全推，代价是产品更新也会来。
