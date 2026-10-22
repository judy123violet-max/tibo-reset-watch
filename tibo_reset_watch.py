#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Tibo Codex 额度重置监控 —— 抓公告 → 去重 → 推送微信

产出方：WorkBuddy（AnyLink to Note 1 号）
日期：2026-10-23
状态：初版，待雅旭配齐通道 token 后做真实送达验收
来源：https://tibo.modelyard.dev/zh/feed.xml （ModelYard 追踪 @thsottiaux 公开的 Codex 额度重置事件）

设计要点
- 零第三方依赖（urllib + xml.etree），Python 3.8+ 可跑，GitHub Actions 和本机都能直接跑
- 首次运行只落基线、不群发历史消息（避免一上来推 20 条旧闻）
- 按 guid 去重，state.json 落盘；Actions 里靠回写仓库持久化
- 通道按环境变量自动选择，优先级：pushplus > serverchan > 企业微信群机器人 > 自定义 webhook
- 只推「重置相关」分类，产品更新类默认不打扰（可用 --all 打开）

用法
  python tibo_reset_watch.py --dry-run      # 只看会推什么，不发送
  python tibo_reset_watch.py --test         # 立刻推一条「最新一条公告」做通道测试
  python tibo_reset_watch.py                # 正式跑一轮
"""

import argparse
import json
import os
import re
import ssl
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

FEED_URL = os.environ.get("TIBO_FEED_URL", "https://tibo.modelyard.dev/zh/feed.xml")
SITE_URL = "https://tibo.modelyard.dev/zh/"

# 只关心这几类；"Codex 产品更新" 默认不推（想看就加 --all 或改这里）
WATCH_CATEGORIES = {"计划重置", "重置完成", "重置卡", "时间变更", "政策"}
# 分类白名单见下方 WATCH_CATEGORIES / is_watched()：只跳过明确认识的产品更新类，
# 遇到从没见过的分类一律照推（fail-open），避免站点改版后静默漏推

STATE_FILE = os.environ.get("TIBO_STATE_FILE", os.path.join(os.path.dirname(os.path.abspath(__file__)), "state.json"))
STATE_MAX = 300  # 最多记 300 条已见 guid
MAX_FAIL_PER_DAY = 6  # 推送连续失败上限，防止 5 分钟一轮把接口刷爆（尤其密钥失效时）
ZERO_ALERT_THRESHOLD = 6  # 连续 6 次抓不到公告（约半小时）就发一次哑火告警
GAP_SLACK_SECONDS = 3600  # 缺口检测容差：RSS 自身也有抓取延迟，留 1 小时余量

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) tibo-reset-watch/1.0"


# ---------------------------------------------------------------- 网络
def http_get(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/rss+xml,application/xml,text/xml,*/*"})
    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
        return r.read()


def http_post_json(url, payload, timeout=20):
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json", "User-Agent": UA})
    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
        return r.status, r.read().decode("utf-8", "replace")


def http_post_form(url, payload, timeout=20):
    data = urllib.parse.urlencode(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/x-www-form-urlencoded", "User-Agent": UA})
    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
        return r.status, r.read().decode("utf-8", "replace")


# ---------------------------------------------------------------- 解析
def parse_feed(raw):
    root = ET.fromstring(raw)
    items = []
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        desc = (it.findtext("description") or "").strip()
        link = (it.findtext("link") or SITE_URL).strip()
        guid = (it.findtext("guid") or link).strip()
        pub = (it.findtext("pubDate") or "").strip()
        cats = [c.text.strip() for c in it.findall("category") if c.text]
        # 描述里带「来源链接：xxx」，抽出来放进消息，方便直接点原帖
        src = ""
        m = re.search(r"来源链接：\s*(https?://\S+)", desc)
        if m:
            src = m.group(1).strip()
        clean = re.sub(r"\s*来源链接：\s*https?://\S+\s*$", "", desc).strip()
        items.append({
            "title": title, "desc": clean, "link": link, "guid": guid,
            "pubDate": pub, "categories": cats, "source": src,
            "_ts": _parse_pub(pub).timestamp(),
        })
    return items


def _parse_pub(pub):
    """RSS 里的 pubDate 是 GMT，返回带时区的 datetime；解析不了就返回 epoch"""
    for f in ("%a, %d %b %Y %H:%M:%S %Z", "%a, %d %b %Y %H:%M:%S %z"):
        try:
            dt = datetime.strptime(pub, f)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except Exception:
            continue
    return datetime(1970, 1, 1, tzinfo=timezone.utc)


def fmt_local(pub):
    """GMT → 本地（北京）时间，便于读"""
    return _parse_pub(pub).astimezone().strftime("%m-%d %H:%M")


# ---------------------------------------------------------------- 状态
def load_state():
    if not os.path.exists(STATE_FILE):
        return {"seen": [], "inited": False}
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            s = json.load(f)
        s.setdefault("seen", [])
        s.setdefault("inited", False)
        return s
    except Exception as e:
        print(f"[warn] state 读取失败，按空状态处理：{e}", file=sys.stderr)
        return {"seen": [], "inited": False}


def save_state(state):
    """只在内容真的变了才落盘。
    原因：Actions 里 state.json 被改动就会触发一次提交回写，
    如果每轮都写 updated_at，一天就是 288 次提交，仓库会被刷烂。
    """
    state["seen"] = state["seen"][-STATE_MAX:]
    payload = json.dumps(
        {
            "seen": state["seen"],
            "inited": state["inited"],
            "fail": state.get("fail") or {"date": "", "n": 0},
            "zero_streak": int(state.get("zero_streak") or 0),
            "zero_alerted": bool(state.get("zero_alerted")),
            "watermark_ts": float(state.get("watermark_ts") or 0),
            "gap_alerted": bool(state.get("gap_alerted")),
        },
        ensure_ascii=False, indent=1,
    )
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, encoding="utf-8") as f:
                if f.read() == payload:
                    return False
        except Exception:
            pass
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        f.write(payload)
    return True


# ---------------------------------------------------------------- 分类判定
# dsh 复核指出：白名单 + 静默丢弃最危险 —— 站点哪天新增分类，脚本会永远不推，
# 而日志照样打印"跳过 N 条"，看起来一切正常。所以改成 fail-open：
# 只跳过「明确认识且确实不需要」的分类，遇到没见过的分类一律推。
WATCH_CATEGORIES = {"计划重置", "重置完成", "重置卡", "时间变更", "政策"}
OTHER_KNOWN_CATEGORIES = {"Codex 产品更新", "路线图线索", "功能讨论"}
UNKNOWN_TAG = "⚠️新分类待确认"


def is_watched(it, allow_all=False):
    if allow_all:
        return True
    cats = set(it["categories"])
    if not cats:                              # 没打分类 = 未知 → 推
        return True
    if cats & WATCH_CATEGORIES:
        return True
    if not (cats & OTHER_KNOWN_CATEGORIES):   # 出现了从没见过的分类 → 推
        return True
    return False


# ---------------------------------------------------------------- 文案
def build_message(items):
    head = f"⚡ Tibo 有 {len(items)} 条 Codex 额度动静"
    lines = [head, ""]
    for it in items:
        cat = "/".join(it["categories"]) or "未分类"
        if not (set(it["categories"]) & (WATCH_CATEGORIES | OTHER_KNOWN_CATEGORIES)):
            cat = f"{cat} {UNKNOWN_TAG}"
        lines.append(f"【{cat}】{fmt_local(it['pubDate'])}")
        lines.append(it["title"])
        if it["desc"]:
            d = it["desc"].split("\n")[0][:160]
            lines.append(d)
        if it["source"]:
            lines.append(f"原帖 {it['source']}")
        lines.append(f"详情 {it['link']}")
        lines.append("")
    lines.append(f"— 监控自 {SITE_URL}（追踪 @thsottiaux 公开公告）")
    return head, "\n".join(lines).strip()


# ---------------------------------------------------------------- 推送通道
def detect_channels():
    chans = []
    if os.environ.get("PUSHPLUS_TOKEN"):
        chans.append("pushplus")
    if os.environ.get("SERVERCHAN_SENDKEY"):
        chans.append("serverchan")
    if os.environ.get("WECOM_WEBHOOK"):
        chans.append("wecom")
    if os.environ.get("GENERIC_WEBHOOK_URL"):
        chans.append("webhook")
    return chans


def push_one(chan, title, content):
    if chan == "pushplus":
        token = os.environ["PUSHPLUS_TOKEN"]
        url = os.environ.get("PUSHPLUS_ENDPOINT", "https://www.pushplus.plus/send")
        payload = {"token": token, "title": title, "content": content, "template": "markdown"}
        if os.environ.get("PUSHPLUS_TOPIC"):
            payload["topic"] = os.environ["PUSHPLUS_TOPIC"]
        if os.environ.get("PUSHPLUS_CHANNEL"):
            payload["channel"] = os.environ["PUSHPLUS_CHANNEL"]  # 例如 clawbot
        st, body = http_post_json(url, payload)
        return st, body

    if chan == "serverchan":
        key = os.environ["SERVERCHAN_SENDKEY"]
        tmpl = os.environ.get("SERVERCHAN_ENDPOINT", "https://sctapi.ftqq.com/{key}.send")
        url = tmpl.format(key=key)
        st, body = http_post_form(url, {"title": title, "desp": content})
        return st, body

    if chan == "wecom":
        url = os.environ["WECOM_WEBHOOK"]
        st, body = http_post_json(url, {"msgtype": "markdown", "markdown": {"content": f"### {title}\n{content}"}})
        return st, body

    if chan == "webhook":
        url = os.environ["GENERIC_WEBHOOK_URL"]
        st, body = http_post_json(url, {"title": title, "content": content, "source": "tibo-reset-watch"})
        return st, body

    raise ValueError(f"未知通道 {chan}")


PUSHPLUS_HINTS = {
    200: "成功",
    903: "用户 token 无效 —— 去 pushplus 后台复制「一对一消息」里的 token，别复制成消息 token",
    905: "账号未实名认证 —— 打不开微信推送，去 https://verify.pushplus.plus 实名后再发",
    900: "token 为空",
    999: "请求过于频繁，被限流",
}


def push_all(title, content, channels, dry_run=False):
    ok = False
    for c in channels:
        if dry_run:
            print(f"[dry-run] 将通过 {c} 推送：{title}")
            ok = True
            continue
        try:
            st, body = push_one(c, title, content)
            print(f"[push] {c} HTTP {st} :: {body[:200]}")
            # pushplus 用 code==200 表示成功，HTTP 本身也返回 200
            if c == "pushplus":
                try:
                    code = json.loads(body).get("code")
                    if code != 200:
                        hint = PUSHPLUS_HINTS.get(code, "未知业务码，去 pushplus 官网查返回码说明")
                        print(f"[warn] {c} 业务码 {code} 未成功：{hint}", file=sys.stderr)
                        continue
                except Exception:
                    pass
            ok = True
        except Exception as e:
            print(f"[error] {c} 推送失败：{e}", file=sys.stderr)
    return ok


# ---------------------------------------------------------------- 主流程
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="只打印，不发送、不写 state")
    ap.add_argument("--test", action="store_true", help="立刻推最新一条公告，用于验证通道")
    ap.add_argument("--all", action="store_true", help="不按分类过滤，任何新条目都推")
    ap.add_argument("--reinit", action="store_true", help="清空基线重新播种（不会补推历史）")
    args = ap.parse_args()

    channels = detect_channels()
    print(f"[chan] 已识别通道：{channels or '无（将只记录不推送）'}")

    state = load_state()
    if args.reinit:
        state = {"seen": [], "inited": False}

    # ---- 抓取：把「抓不到」也当成一种需要上报的状态 ----
    # 监控最怕的不是报错，是静默失效：RSS 挂了两周，界面上一片安静，
    # 你会以为「最近没重置」，其实是脚本瞎了。所以连续抓空要主动喊一声。
    items = []
    outage = ""
    try:
        items = parse_feed(http_get(FEED_URL))
        if not items:
            outage = "RSS 返回 0 条"
    except Exception as e:
        outage = f"抓取异常：{type(e).__name__}: {e}"

    if outage:
        streak = int(state.get("zero_streak") or 0) + 1
        state["zero_streak"] = streak
        print(f"[outage] 第 {streak} 次连续抓空（{outage}）", file=sys.stderr)
        # 5 分钟一轮，连续 6 次 = 半小时，足够排除偶发抖动
        if streak >= ZERO_ALERT_THRESHOLD and not state.get("zero_alerted"):
            title = f"⚠️ Tibo 监控可能哑火（连续 {streak} 次没抓到公告）"
            content = (f"监控脚本连续 {streak} 次没有从数据源读到公告。\n\n原因：{outage}\n\n"
                       f"数据源：{FEED_URL}\n站点：{SITE_URL}\n\n"
                       f"大概率是 ModelYard 改版或挂了自己去看一眼；如果是源换了，脚本里的 FEED_URL 要跟着改。\n"
                       f"（这条只报一次，恢复后自动重置）")
            if channels and not args.dry_run and push_all(title, content, channels):
                state["zero_alerted"] = True
            elif not channels:
                print("[note] 没有推送通道，哑火告警只记录在本机日志", file=sys.stderr)
        save_state(state)
        return 2

    # 抓到了：恢复正常，清掉哑火计数
    if state.get("zero_streak") or state.get("zero_alerted"):
        print("[recover] 数据源已恢复，重置哑火计数")
        state["zero_streak"] = 0
        state["zero_alerted"] = False
        save_state(state)

    print(f"[feed] 抓到 {len(items)} 条，最新：{items[0]['title']}（{fmt_local(items[0]['pubDate'])}）")

    # 通道测试：不管去重，直接推最新一条
    if args.test:
        it = items[0]
        title, content = build_message([it])
        title = "【通道测试】" + title
        if not channels:
            print("[error] 没有任何通道环境变量，无法测试", file=sys.stderr)
            return 3
        return 0 if push_all(title, content, channels, args.dry_run) else 1

    # ---- 缺口检测（dsh 复核提出）----
    # RSS 只保留最近 20 条（实测跨度约 18 天）。监控要是停跑超过这个窗口，
    # 中间那些公告既不会推、也不在 seen 里，而且全程没有任何报错 —— 这就是静默丢事件。
    # 用「上次成功检查的时刻」当水位线：feed 里最旧一条如果比水位线还新，说明中间断了。
    now_ts = datetime.now(timezone.utc).timestamp()
    wm = float(state.get("watermark_ts") or 0)
    oldest_ts = min(it["_ts"] for it in items)
    if wm and oldest_ts > wm + GAP_SLACK_SECONDS:
        gap_h = (oldest_ts - wm) / 3600
        print(f"[gap] 检测到缺口：feed 最旧一条比上次检查还晚 {gap_h:.1f} 小时", file=sys.stderr)
        if not state.get("gap_alerted"):
            title = "⚠️ Tibo 监控可能有缺口"
            content = (f"监控上次成功检查是 {gap_h:.0f} 小时前，但数据源里最旧的一条比那还新。\n\n"
                       f"意思是：中间这段时间可能发生过公告，但已经滚出 RSS 窗口，脚本没看到、也没推。\n\n"
                       f"数据源：{FEED_URL}\n去站点翻一下这段时间的重置历史：{SITE_URL}\n\n"
                       f"（这条只报一次，恢复后自动重置）")
            if channels and not args.dry_run and push_all(title, content, channels):
                state["gap_alerted"] = True
            elif not channels:
                print("[note] 没有推送通道，缺口告警只记录在本机日志", file=sys.stderr)
    elif wm:
        state["gap_alerted"] = False
    state["watermark_ts"] = now_ts
    if not args.dry_run:
        save_state(state)

    # 首次运行：只播种基线
    if not state["inited"]:
        state["seen"] = [it["guid"] for it in items]
        state["inited"] = True
        if not args.dry_run:
            save_state(state)
        print(f"[init] 首次运行，已播种 {len(items)} 条基线，不推送历史。之后有新条目才提醒。")
        return 0

    seen = set(state["seen"])
    fresh = [it for it in items if it["guid"] not in seen]
    if not fresh:
        print("[idle] 无新条目")
        if not args.dry_run:
            save_state(state)
        return 0

    if args.all:
        picked = fresh
    else:
        picked = [it for it in fresh if is_watched(it)]
        skipped = len(fresh) - len(picked)
        if skipped:
            print(f"[filter] 跳过 {skipped} 条明确属于产品更新的条目（未知分类一律会推）")

    # 被过滤掉的条目直接记入 seen：我们主动不要，不该每轮重复判断
    picked_guids = {it["guid"] for it in picked}
    state["seen"].extend(it["guid"] for it in fresh if it["guid"] not in picked_guids)

    if not picked:
        print("[idle] 有更新但都不是重置相关，不打扰")
        if not args.dry_run:
            save_state(state)
        return 0

    picked.sort(key=lambda x: x["_ts"])  # 按真实时间升序：计划→完成，读起来像一条线
    title, content = build_message(picked)
    print(f"[push] 待推 {len(picked)} 条：{title}")
    if args.dry_run:
        print("-" * 50)
        print(content)
        print("-" * 50)
        return 0

    # 没有配置任何通道时，只做记录，记为已见，避免以后配好通道又补推一堆旧闻
    if not channels:
        state["seen"].extend(picked_guids)
        save_state(state)
        print("[note] 没有配置推送通道，本轮只记录不发送")
        return 0

    # 关键顺序：先推成功，才把这些条目记为已见。
    # 反过来的话（先记后推）一旦推送失败，这批公告就永远丢了 —— Server酱 免费额度 5 条/天，
    # 超了会静默失败，静默失败 + 提前记 seen = 消息永久消失，这是绝对不能留的坑。
    today = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d")
    fail = state.get("fail") or {}
    if fail.get("date") == today and fail.get("n", 0) >= MAX_FAIL_PER_DAY:
        print(f"[error] 今天已连续失败 {fail['n']} 次（上限 {MAX_FAIL_PER_DAY}），暂停推送防止刷爆接口；"
              f"条目保持未读，明天或人工排查后恢复", file=sys.stderr)
        save_state(state)
        return 1

    if not push_all(title, content, channels):
        n = (fail.get("n", 0) if fail.get("date") == today else 0) + 1
        state["fail"] = {"date": today, "n": n}
        print(f"[error] 推送失败（今日第 {n} 次），这批条目保持未读，下一轮重试", file=sys.stderr)
        save_state(state)   # 只落盘被过滤掉的那些，picked 仍未读
        return 1

    state["seen"].extend(picked_guids)
    state["fail"] = {"date": today, "n": 0}
    save_state(state)
    return 0


if __name__ == "__main__":
    sys.exit(main())
