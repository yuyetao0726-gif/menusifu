#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
apply-analytics.py
把 site.config.json 里配置的 Google Search Console / GA4 / GTM 一键注入全站页面，
并顺带修正 canonical / og:url / twitter:image 的错误域名。

用法:
    python apply-analytics.py            应用配置（幂等，可反复运行）
    python apply-analytics.py --check    校验线上站点是否已生效
"""
import json
import os
import re
import sys
import urllib.request

BASE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE, "site.config.json")
PAGES = ["index.html", "Gallery-W.html", "Contact-Us.html"]
WRONG_DOMAIN = "https://www.menusifu.com"

HEAD_BEGIN = "<!-- BEGIN:ANALYTICS -->"
HEAD_END = "<!-- END:ANALYTICS -->"
BODY_BEGIN = "<!-- BEGIN:GTM-NOSCRIPT -->"
BODY_END = "<!-- END:GTM-NOSCRIPT -->"

GTM_SNIPPET = (
    "<script>(function(w,d,s,l,i){w[l]=w[l]||[];w[l].push({'gtm.start':"
    "new Date().getTime(),event:'gtm.js'});var f=d.getElementsByTagName(s)[0],"
    "j=d.createElement(s),dl=l!='dataLayer'?'&l='+l:'';j.async=true;j.src="
    "'https://www.googletagmanager.com/gtm.js?id='+i+dl;"
    "f.parentNode.insertBefore(j,f);})(window,document,'script','dataLayer','__GTM_ID__');</script>"
)

# 旧版遗留代码（含占位符）的清理规则
LEGACY_PATTERNS = [
    (r'<meta\s+name=["\']google-site-verification["\'][^>]*>', 0),
    (r'<script>\s*\(function\(w,d,s,l,i\).*?</script>', re.S),
    (r'<script\s+async\s+src=["\']https://www\.googletagmanager\.com/gtag/js\?id=[^"\']*["\']>\s*</script>', re.S),
    (r'<script>\s*window\.dataLayer\s*=\s*window\.dataLayer\s*\|\|\s*\[\];.*?</script>', re.S),
    (r'<noscript><iframe\s+src=["\']https://www\.googletagmanager\.com/ns\.html\?id=[^"\']*["\'][^>]*>.*?</noscript>', re.S),
    (r'<link\s+rel=["\']preconnect["\']\s+href=["\']https://www\.googletagmanager\.com["\']\s*>', 0),
]
LEGACY_COMMENT_KEYWORDS = [
    "Google Tag Manager",
    "Google Analytics 4",
    "Google Search Console",
    "分析工具：GTM",
]


def load_config():
    with open(CONFIG_PATH, encoding="utf-8") as f:
        cfg = json.load(f)
    cfg.setdefault("siteUrl", "")
    cfg.setdefault("gsc", {}).setdefault("verificationCode", "")
    cfg["gsc"].setdefault("verificationMethod", "meta")
    cfg.setdefault("gtm", {}).setdefault("containerId", "")
    cfg.setdefault("ga4", {}).setdefault("measurementId", "")
    cfg["ga4"].setdefault("viaGtm", True)
    return cfg


def strip_legacy(html):
    """清掉旧的/占位符的 GSC、GTM、GA4 代码，保证重复运行不产生重复标签。"""
    for pat, flags in LEGACY_PATTERNS:
        html = re.sub(pat, "", html, flags=flags)
    # 清理相关注释行
    def drop_comment(m):
        text = m.group(0)
        return "" if any(k in text for k in LEGACY_COMMENT_KEYWORDS) else text
    html = re.sub(r"<!--.*?-->", drop_comment, html, flags=re.S)
    # 清理本脚本自己的标记块
    for begin, end in ((HEAD_BEGIN, HEAD_END), (BODY_BEGIN, BODY_END)):
        html = re.sub(re.escape(begin) + r".*?" + re.escape(end), "", html, flags=re.S)
    return html


def build_head_block(cfg):
    parts = []
    gsc_code = cfg["gsc"]["verificationCode"].strip()
    method = cfg["gsc"]["verificationMethod"].strip()
    gtm_id = cfg["gtm"]["containerId"].strip()
    ga4_id = cfg["ga4"]["measurementId"].strip()

    if gsc_code and method == "meta":
        parts.append("<!-- Google Search Console 验证 -->")
        parts.append('<meta name="google-site-verification" content="%s">' % gsc_code)

    if gtm_id:
        parts.append("<!-- Google Tag Manager -->")
        parts.append(GTM_SNIPPET.replace("__GTM_ID__", gtm_id))
        parts.append("<!-- End Google Tag Manager -->")

    if ga4_id and not cfg["ga4"]["viaGtm"]:
        parts.append("<!-- Google Analytics 4 (standalone) -->")
        parts.append('<script async src="https://www.googletagmanager.com/gtag/js?id=%s"></script>' % ga4_id)
        parts.append(
            "<script>window.dataLayer=window.dataLayer||[];"
            "function gtag(){dataLayer.push(arguments);}"
            "gtag('js',new Date());gtag('config','%s');</script>" % ga4_id
        )

    if gtm_id or ga4_id:
        parts.append('<link rel="preconnect" href="https://www.googletagmanager.com">')

    if not parts:
        return ""
    return HEAD_BEGIN + "\n    " + "\n    ".join(parts) + "\n    " + HEAD_END


def build_body_block(cfg):
    gtm_id = cfg["gtm"]["containerId"].strip()
    if not gtm_id:
        return ""
    body = ('<noscript><iframe src="https://www.googletagmanager.com/ns.html?id=%s" '
            'height="0" width="0" style="display:none;visibility:hidden"></iframe></noscript>' % gtm_id)
    return BODY_BEGIN + "\n    " + body + "\n    " + BODY_END


def write_verification_file(cfg):
    """htmlfile 方式：生成 googleXXXX.html 验证文件。"""
    code = cfg["gsc"]["verificationCode"].strip()
    if cfg["gsc"]["verificationMethod"].strip() != "htmlfile" or not code:
        return None
    name = "google%s.html" % code
    path = os.path.join(BASE, name)
    with open(path, "w", encoding="utf-8") as f:
        f.write("google-site-verification: %s\n" % name)
    return name


def tidy(html):
    """压缩删除残留造成的连续空行（head 全部整理，body 仅处理开头）。"""
    idx = html.lower().find("</head>")
    if idx != -1:
        head, rest = html[:idx], html[idx:]
        head = re.sub(r"\n(?:[ \t]*\n)+", "\n\n", head).rstrip()
        html = head + "\n" + rest
    html = re.sub(r"(<body[^>]*>)(?:\s*\n){2,}", r"\1\n    ", html, count=1, flags=re.I)
    return html


def process_page(filename, cfg):
    path = os.path.join(BASE, filename)
    with open(path, encoding="utf-8") as f:
        html = f.read()
    original = html

    # 1) 修正错误域名（canonical / og:url / og:image / twitter:image）
    fixed_domain = 0
    if cfg["siteUrl"] and WRONG_DOMAIN in html:
        fixed_domain = html.count(WRONG_DOMAIN)
        html = html.replace(WRONG_DOMAIN, cfg["siteUrl"].rstrip("/"))

    # 2) 清理旧代码，注入新代码
    html = strip_legacy(html)
    head_block = build_head_block(cfg)
    body_block = build_body_block(cfg)

    if head_block:
        html = re.sub(r"\s*</head>", "\n    " + head_block + "\n</head>", html, count=1, flags=re.I)
    if body_block:
        html = re.sub(r"(<body[^>]*>)", r"\1\n    " + body_block, html, count=1, flags=re.I)

    html = tidy(html)

    changed = html != original
    if changed:
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(html)
    return changed, fixed_domain


def check_live(cfg):
    base = cfg["siteUrl"].rstrip("/")
    gsc_code = cfg["gsc"]["verificationCode"].strip()
    gtm_id = cfg["gtm"]["containerId"].strip()
    ga4_id = cfg["ga4"]["measurementId"].strip()
    print("校验线上站点: %s\n" % base)
    ok_all = True
    for page in PAGES:
        url = base + "/" if page == "index.html" else base + "/" + page
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            html = urllib.request.urlopen(req, timeout=20).read().decode("utf-8", "replace")
        except Exception as e:
            print("  [FAIL] %-18s 无法访问: %s" % (page, e))
            ok_all = False
            continue
        checks = []
        checks.append(("canonical正确", base in html and WRONG_DOMAIN not in html))
        if gsc_code:
            checks.append(("GSC验证码", 'google-site-verification" content="%s"' % gsc_code in html))
        if gtm_id:
            checks.append(("GTM容器", "gtm.js?id=" + gtm_id in html or "GTM-" in html and gtm_id in html))
        if ga4_id and not cfg["ga4"]["viaGtm"]:
            checks.append(("GA4代码", "gtag/js?id=" + ga4_id in html))
        for name, ok in checks:
            print("  [%s] %-18s %s" % ("PASS" if ok else "FAIL", page, name))
            if not ok:
                ok_all = False
    print("\n结果: %s" % ("全部通过 ✅" if ok_all else "存在未生效项 ❌（确认是否已 push 并等待部署完成）"))


def main():
    cfg = load_config()

    if "--check" in sys.argv:
        check_live(cfg)
        return

    missing = []
    if not cfg["gsc"]["verificationCode"].strip():
        missing.append("gsc.verificationCode")
    if not cfg["gtm"]["containerId"].strip():
        missing.append("gtm.containerId")
    if not cfg["ga4"]["measurementId"].strip():
        missing.append("ga4.measurementId")
    if not cfg["siteUrl"].strip():
        missing.append("siteUrl")

    print("站点: %s" % cfg["siteUrl"])
    print("-" * 56)
    for page in PAGES:
        changed, fixed = process_page(page, cfg)
        note = "域名修正 %d 处" % fixed if fixed else ""
        print("  %-18s %s %s" % (page, "已更新" if changed else "无变化", note))
    vf = write_verification_file(cfg)
    if vf:
        print("  生成 GSC 验证文件: %s" % vf)
    print("-" * 56)

    if missing:
        print("⚠️  以下配置仍为空，相关代码暂时不会写入页面：")
        for m in missing:
            print("     - %s" % m)
        print("   拿到真实 ID 后填入 site.config.json，重新运行本脚本即可。")
    else:
        print("✅ 全部配置已写入。提交并 push 后等待 Cloudflare 自动部署，")
        print("   然后运行: python apply-analytics.py --check 校验线上是否生效。")


if __name__ == "__main__":
    main()
