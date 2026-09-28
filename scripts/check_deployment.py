"""Static deployment checks for failures that Compose syntax cannot detect."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def require(text: str, needle: str, source: str) -> None:
    if needle not in text:
        raise SystemExit(f"{source} must contain: {needle}")


nginx_config = (ROOT / "deploy" / "nginx.conf").read_text(encoding="utf-8")
web_dockerfile = (ROOT / "deploy" / "web.Dockerfile").read_text(encoding="utf-8")
compose_config = (ROOT / "compose.yaml").read_text(encoding="utf-8")

require(nginx_config, "root /usr/share/nginx/html;", "deploy/nginx.conf")
require(nginx_config, "try_files $uri $uri/ /index.html;", "deploy/nginx.conf")
require(
    web_dockerfile,
    "COPY --from=frontend-build /app/frontend/dist /usr/share/nginx/html",
    "deploy/web.Dockerfile",
)
require(
    compose_config,
    'wget -qO- http://127.0.0.1/ >/dev/null || exit 1',
    "compose.yaml web healthcheck",
)

print("Deployment static checks passed.")
