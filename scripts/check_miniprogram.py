#!/usr/bin/env python3
"""Static integrity and secret-safety checks for the WeChat mini program."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path, PurePosixPath
from urllib.parse import urlparse
from xml.etree import ElementTree


ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / "wechat-miniprogram"
SOURCE = PROJECT / "miniprogram"


def fail(message: str) -> None:
    raise SystemExit(f"WeChat mini program check failed: {message}")


def read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        fail(f"invalid JSON in {path.relative_to(ROOT)}: {exc}")
    if not isinstance(value, dict):
        fail(f"{path.relative_to(ROOT)} must contain a JSON object")
    return value


def read_wxml(path: Path):
    try:
        text = path.read_text(encoding="utf-8")
        # WXML permits valueless directive attributes such as wx:else, while
        # the standard XML parser requires an explicit value.
        text = re.sub(r"(\s)wx:else(?=\s|>)", r'\1wx:else=""', text)
        return ElementTree.fromstring(f'<root xmlns:wx="wechat">{text}</root>')
    except (OSError, UnicodeError, ElementTree.ParseError) as exc:
        fail(f"invalid WXML in {path.relative_to(ROOT)}: {exc}")


def is_git_ignored(path: Path) -> bool:
    """Ignore private local files, but still inspect files already tracked by Git."""
    result = subprocess.run(
        ["git", "check-ignore", "-q", "--", path.relative_to(ROOT).as_posix()],
        cwd=ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return result.returncode == 0


def reviewable_files(root: Path):
    return [path for path in root.rglob("*") if path.is_file() and not is_git_ignored(path)]


required_files = {
    PROJECT / "README.md",
    PROJECT / "project.config.json",
    PROJECT / "project.private.config.example.json",
    SOURCE / "app.js",
    SOURCE / "app.json",
    SOURCE / "app.wxss",
    SOURCE / "sitemap.json",
    SOURCE / "utils" / "config.js",
    SOURCE / "pages" / "home" / "index.js",
    SOURCE / "pages" / "home" / "index.json",
    SOURCE / "pages" / "home" / "index.wxml",
    SOURCE / "pages" / "home" / "index.wxss",
    SOURCE / "pages" / "webview" / "index.js",
    SOURCE / "pages" / "webview" / "index.json",
    SOURCE / "pages" / "webview" / "index.wxml",
    SOURCE / "pages" / "webview" / "index.wxss",
}
missing = sorted(str(path.relative_to(ROOT)) for path in required_files if not path.is_file())
if missing:
    fail(f"missing required files: {', '.join(missing)}")

project_config = read_json(PROJECT / "project.config.json")
if project_config.get("compileType") != "miniprogram":
    fail("project.config.json compileType must be miniprogram")
if project_config.get("miniprogramRoot") != "miniprogram/":
    fail("project.config.json miniprogramRoot must be miniprogram/")
if project_config.get("appid") != "touristappid":
    fail("the public repository must retain the touristappid placeholder")
project_setting = project_config.get("setting")
if not isinstance(project_setting, dict) or project_setting.get("urlCheck") is not True:
    fail("release-safe domain checking must remain enabled")

app_config = read_json(SOURCE / "app.json")
pages = app_config.get("pages")
required_pages = ["pages/home/index", "pages/webview/index"]
if not isinstance(pages, list) or not pages or pages[0] != "pages/home/index":
    fail("app.json must start at the native pages/home/index page")
if pages != required_pages or len(pages) != len(set(pages)):
    fail("app.json must register the home and webview pages exactly once")
for page in pages:
    page_path = PurePosixPath(page) if isinstance(page, str) else None
    if (
        page_path is None
        or not page.startswith("pages/")
        or ".." in page_path.parts
        or page.startswith("/")
    ):
        fail(f"invalid page path in app.json: {page!r}")
    for suffix in (".js", ".json", ".wxml", ".wxss"):
        if not (SOURCE / f"{page}{suffix}").is_file():
            fail(f"page {page} is missing {suffix}")

read_json(SOURCE / "sitemap.json")
for wxml_path in SOURCE.rglob("*.wxml"):
    read_wxml(wxml_path)
config_text = (SOURCE / "utils" / "config.js").read_text(encoding="utf-8")
match = re.search(r"ERP_BASE_URL\s*=\s*['\"]([^'\"]+)['\"]", config_text)
if not match:
    fail("utils/config.js must define a literal ERP_BASE_URL")
erp_url_text = match.group(1)
erp_url = urlparse(erp_url_text)
if erp_url_text != "https://erp.qvgro.com" or erp_url.netloc != "erp.qvgro.com":
    fail("ERP_BASE_URL must be exactly https://erp.qvgro.com")
module_paths = re.findall(r"\{\s*path:\s*['\"]([^'\"]+)['\"]", config_text)
expected_module_paths = [
    "/",
    "/molds",
    "/racks",
    "/orders",
    "/production",
    "/quality",
    "/analytics",
    "/product-specifications",
]
if module_paths != expected_module_paths or any("?" in path or "#" in path for path in module_paths):
    fail("the native shortcut path allowlist is incomplete or unsafe")

webview_tree = read_wxml(SOURCE / "pages" / "webview" / "index.wxml")
webview_js = (SOURCE / "pages" / "webview" / "index.js").read_text(encoding="utf-8")
webview_nodes = [node for node in webview_tree.iter() if node.tag == "web-view"]
if len(webview_nodes) != 1:
    fail("the webview page must contain exactly one web-view component")
if webview_nodes[0].attrib.get("binderror") != "handleError":
    fail("the web-view component must expose an error fallback")
if "ALLOWED_PATHS.includes(requestedPath)" not in webview_js:
    fail("web-view routes must be restricted to the fixed allowlist")

project_files = reviewable_files(PROJECT)
for path in project_files:
    if path.name == "project.private.config.json" or path.name == ".env" or path.name.startswith(".env."):
        fail(f"forbidden private file is tracked: {path.relative_to(ROOT)}")
    if path.name in {"package.json", "package-lock.json"}:
        fail(f"the native mini program must not add Node dependencies: {path.relative_to(ROOT)}")
    if path.suffix.lower() in {".pem", ".key", ".p12", ".pfx"}:
        fail(f"private keys and certificates must not be tracked: {path.relative_to(ROOT)}")

public_root = ROOT / "frontend" / "public"
# Everything under Vite's public directory is copied to the web root, including
# files ignored by Git. Inspect the real directory rather than only reviewable
# source files so a local secret cannot be published by a Docker build.
public_files = [path for path in public_root.rglob("*") if path.is_file()]
verification_name = re.compile(r"MP_verify_[A-Za-z0-9_-]+\.txt")
for path in public_files:
    if path.name == ".gitkeep":
        continue
    if not verification_name.fullmatch(path.name):
        fail(
            "frontend/public is internet-facing and only official MP_verify_*.txt "
            f"files are allowed; found {path.relative_to(ROOT)}"
        )
    if path.stat().st_size > 16 * 1024:
        fail(f"unexpectedly large WeChat verification file: {path.relative_to(ROOT)}")

secret_assignment = re.compile(
    r"(?ix)['\"]?(app[_-]?secret|access[_-]?token|refresh[_-]?token|session[_-]?key|"
    r"erp[_-]?password|password|cookie)['\"]?\s*[:=]\s*"
    r"(?P<quote>['\"`])[^'\"`\r\n]{4,}(?P=quote)"
)
for path in [*project_files, *public_files]:
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeError:
        continue
    if (
        secret_assignment.search(text)
        or "-----BEGIN PRIVATE KEY-----" in text
        or re.search(r"(?i)Bearer\s+[A-Za-z0-9._~+/-]{20,}", text)
    ):
        fail(f"possible embedded credential in {path.relative_to(ROOT)}")

dockerfile = (ROOT / "deploy" / "web.Dockerfile").read_text(encoding="utf-8")
if "COPY frontend/public ./public" not in dockerfile:
    fail("web image must copy frontend/public for the official domain verification file")
nginx_config = (ROOT / "deploy" / "nginx.conf").read_text(encoding="utf-8")
if "^/MP_verify_[A-Za-z0-9_-]+\\.txt$" not in nginx_config or "try_files $uri =404" not in nginx_config:
    fail("Nginx must serve only existing MP_verify_*.txt files without SPA fallback")

print(
    "WeChat mini program checks passed: native shortcuts, fixed HTTPS ERP domain, "
    "web-view fallback, project structure, and secret boundaries are valid."
)
