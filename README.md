# Tibo Codex 额度重置监控

产出方 WorkBuddy｜日期 2026-10-23｜状态 **已上线，云端定时运行中**
来源：<https://tibo.modelyard.dev/zh/feed.xml>
仓库：<https://github.com/judy123violet-max/tibo-reset-watch>（公开，Actions 无限免费）

## 当前状态（2026-10-23 02:45）

全部打通，云端实测证据：

- ✅ 抓 RSS：Actions 日志 `[feed] 抓到 20 条`
- ✅ **GitHub 美国机房能连国内数据源**（这曾是最大未知项，已证伪风险）
- ✅ **云端 → Server酱 → 手机微信 全链路走通**：`{"code":0,"error":"SUCCESS","pushid":"63316419"}`（从 GitHub runner 发出）
- ✅ 云端首跑 `conclusion: success`，state.json 无变化时跳过提交（不会刷提交）
- ✅ 失败不吞消息、每日失败熔断、连续抓空哑火告警、缺口水位线检测、分类 fail-open —— 全部本地实测过
- ⏳ 待观察：cron 真正的定时触发效果（Actions 自带几分钟到十几分钟延迟）
- 🔧 手动测试：仓库 → Actions → tibo-reset-watch → Run workflow，勾上 `test` 就只发一条测试消息

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
   **Server酱额度**：雅旭账号是赠送的订阅会员，**2026-10-30 之前不限次数**；到期后回落免费档 **5 条/天**。
   脚本不用改——每次把多条公告合并成一条推，正常一周用不到 5 条；真撞上密集公告，超额的推送会失败，脚本让那批公告保持未读、下轮重试，不静默吞掉。
1. **延迟**：GitHub Actions 的 cron 在高峰期会晚几分钟到十几分钟触发；RSS 本身也依赖 ModelYard 抓帖速度。所以是「几分钟到二十分钟级」，不是秒级。
2. **只覆盖公开公告**：ModelYard 抓的是 @thsottiaux 的公开发言。它自己的免责声明写明不读取个人账户余额。**「官方说重置」≠「你账号已到账」**，到账还得自己看 Codex 里剩多少。
3. **RSS 是第三方转载**：ModelYard 挂了/改结构，这个监控就哑了。所以脚本每次跑都会打印抓到几条，静默失败能在日志里看出来。
4. **分类靠 ModelYard 打的标签**：漏标就会漏推。用 `--all` 可以不分类型全推，代价是产品更新也会来。
