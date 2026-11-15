"""Deploy the API to a Hugging Face Space (Docker SDK).

The Space repo only needs the runtime image inputs: Dockerfile, .dockerignore,
requirements.txt, app/ and the Space card (deploy/huggingface/README.md, which
carries the YAML front matter HF reads: sdk, app_port...). This script stages
exactly those files and uploads them as one commit, removing anything stale.

Usage (needs `pip install huggingface_hub` and a write token in HF_TOKEN, or a
cached login from `hf auth login`):

    python scripts/deploy_hf_space.py --space <user>/apiwatch-api \\
        --cors-origins https://apiwatch.vercel.app

First deploy of a new Space also generates and sets JWT_SECRET_KEY. Pass
--rotate-jwt-secret to replace it later (signs everyone out). Secrets are
written straight to the Space settings and never printed or saved locally.
"""

from __future__ import annotations

import argparse
import os
import secrets
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FILES = ["Dockerfile", ".dockerignore", "requirements.txt"]


def stage(target: Path) -> None:
    for name in FILES:
        shutil.copy2(ROOT / name, target / name)
    shutil.copytree(
        ROOT / "app",
        target / "app",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    shutil.copy2(ROOT / "deploy" / "huggingface" / "README.md", target / "README.md")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--space", default=os.getenv("HF_SPACE"), help="<user>/<space-name>")
    parser.add_argument("--cors-origins", help="Comma-separated frontend origins to allow")
    parser.add_argument("--cors-origin-regex", help="Optional regex for preview deployments")
    parser.add_argument(
        "--rotate-jwt-secret", action="store_true", help="Set a new random JWT_SECRET_KEY"
    )
    parser.add_argument("--private", action="store_true", help="Create the Space as private")
    args = parser.parse_args()

    if not args.space or "/" not in args.space:
        parser.error("--space <user>/<space-name> is required (or set HF_SPACE)")

    try:
        from huggingface_hub import HfApi
    except ImportError:
        print("huggingface_hub is not installed: pip install huggingface_hub", file=sys.stderr)
        return 1

    api = HfApi(token=os.getenv("HF_TOKEN") or None)
    is_new = not api.repo_exists(args.space, repo_type="space")
    if is_new:
        api.create_repo(args.space, repo_type="space", space_sdk="docker", private=args.private)
        print(f"Created Space {args.space}")

    if is_new or args.rotate_jwt_secret:
        api.add_space_secret(args.space, "JWT_SECRET_KEY", secrets.token_urlsafe(48))
        print("Set JWT_SECRET_KEY (random, 64 chars)")
    if args.cors_origins:
        api.add_space_variable(args.space, "CORS_ORIGINS", args.cors_origins)
        print(f"Set CORS_ORIGINS={args.cors_origins}")
    if args.cors_origin_regex:
        api.add_space_variable(args.space, "CORS_ORIGIN_REGEX", args.cors_origin_regex)
        print(f"Set CORS_ORIGIN_REGEX={args.cors_origin_regex}")

    with tempfile.TemporaryDirectory() as tmp:
        staged = Path(tmp)
        stage(staged)
        commit = api.upload_folder(
            folder_path=staged,
            repo_id=args.space,
            repo_type="space",
            commit_message="Deploy APIWatch API",
            delete_patterns=["app/**", "*.txt", "Dockerfile", ".dockerignore", "README.md"],
        )
    owner, name = args.space.split("/", 1)
    host = f"{owner}-{name}".lower().replace("_", "-").replace(".", "-")
    print(f"Uploaded: {commit.commit_url}")
    print(f"Space page: https://huggingface.co/spaces/{args.space}")
    print(f"API base URL (once built): https://{host}.hf.space")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
