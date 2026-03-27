#!/usr/bin/env python3
"""
nanobanana2生成脚本
支持文生图（text-to-image）和图生图（image-to-image）

用法:
  python generate.py text --prompt "..." [--aspect-ratio 16:9] [--resolution 1k] [--output ./output]
  python generate.py image --images url1 url2 --prompt "..." [--aspect-ratio 1:1] [--resolution 1k] [--output ./output]
"""

import argparse
import json
import os
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# 尝试加载 .env（优先使用 python-dotenv，无则手动解析）
# ---------------------------------------------------------------------------
def _load_dotenv():
    """在当前目录或脚本目录查找 .env 并加载到环境变量。"""
    candidates = [
        Path.cwd() / ".env",
        Path(__file__).parent.parent / ".env",  # 技能根目录
        Path(__file__).parent / ".env",
    ]
    for env_path in candidates:
        if env_path.exists():
            try:
                from dotenv import load_dotenv  # type: ignore
                load_dotenv(env_path)
                return
            except ImportError:
                # 手动解析
                with open(env_path, encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith("#") or "=" not in line:
                            continue
                        key, _, value = line.partition("=")
                        key = key.strip()
                        value = value.strip().strip('"').strip("'")
                        if key and key not in os.environ:
                            os.environ[key] = value
                return


# ---------------------------------------------------------------------------
# API 常量
# ---------------------------------------------------------------------------
BASE_URL = "https://www.runninghub.cn"
QUERY_PATH = "/openapi/v2/query"

# 渠道 → endpoint slug 映射
# budget  = 低价渠道版（个人 Key 可用）
# official = 官方稳定版（仅企业共享 Key）
CHANNEL_SLUG = {
    "budget":   "rhart-image-n-g31-flash",
    "official": "rhart-image-n-g31-flash-official",
}

def _make_paths(channel: str) -> tuple[str, str]:
    slug = CHANNEL_SLUG.get(channel, CHANNEL_SLUG["budget"])
    return (
        f"/openapi/v2/{slug}/text-to-image",
        f"/openapi/v2/{slug}/image-to-image",
    )

VALID_ASPECT_RATIOS = {"1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3", "21:9", "9:21"}
VALID_RESOLUTIONS = {"1k", "2k"}

MAX_WAIT_SECONDS = 300   # 最大等待 5 分钟
POLL_INTERVAL = 3        # 轮询间隔秒数


# ---------------------------------------------------------------------------
# HTTP 工具（零依赖）
# ---------------------------------------------------------------------------
def _http_post(url: str, payload: dict, api_key: str) -> dict:
    """发送 POST JSON 请求，返回解析后的响应 dict。"""
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {e.code}: {body}") from e


def _http_get(url: str, api_key: str) -> dict:
    """发送 GET 请求，返回解析后的响应 dict。"""
    req = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {api_key}"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {e.code}: {body}") from e


def _download_file(url: str, dest: Path) -> None:
    """下载文件到本地路径。"""
    dest.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url, timeout=60) as resp:
        dest.write_bytes(resp.read())


def _upload_file(api_key: str, file_path: Path) -> str:
    """上传本地文件到服务器，返回 download_url。"""
    if not file_path.exists():
        raise FileNotFoundError(f"文件不存在: {file_path}")

    url = f"{BASE_URL}/openapi/v2/media/upload/binary"
    boundary = "----nanobanana2Boundary" + str(int(time.time()))
    
    # 构造 multipart/form-data 正文
    # 注意：为了保持零依赖，手动构造正文。对于大文件这可能占用较多内存。
    parts = []
    parts.append(f"--{boundary}".encode("utf-8"))
    parts.append(
        f'Content-Disposition: form-data; name="file"; filename="{file_path.name}"'.encode("utf-8")
    )
    # 简单判断 Content-Type
    ext = file_path.suffix.lower()
    ctype = "image/png" if ext == ".png" else "image/jpeg" if ext in (".jpg", ".jpeg") else "application/octet-stream"
    parts.append(f"Content-Type: {ctype}".encode("utf-8"))
    parts.append(b"")
    parts.append(file_path.read_bytes())
    parts.append(f"--{boundary}--".encode("utf-8"))
    
    body = b"\r\n".join(parts) + b"\r\n"
    
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Content-Length": str(len(body)),
        },
        method="POST",
    )
    
    print(f"[↑] 正在上传本地文件: {file_path.name} ...")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if data.get("code") != 0:
                raise RuntimeError(f"上传失败: {data.get('message')}")
            download_url = data.get("data", {}).get("download_url")
            if not download_url:
                raise RuntimeError(f"上传成功但响应中无 URL: {data}")
            return download_url
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"上传时 HTTP {e.code}: {body}") from e


# ---------------------------------------------------------------------------
# 核心逻辑
# ---------------------------------------------------------------------------
def _submit_task(api_key: str, mode: str, payload: dict, channel: str = "budget") -> str:
    """提交生成任务，返回 taskId。"""
    t2i_path, i2i_path = _make_paths(channel)
    path = t2i_path if mode == "text" else i2i_path
    url = BASE_URL + path
    print(f"[→] 提交{('文生图' if mode == 'text' else '图生图')}任务...")
    resp = _http_post(url, payload, api_key)
    task_id = resp.get("taskId")
    if not task_id:
        raise RuntimeError(f"提交失败，响应: {resp}")
    print(f"[✓] 任务已提交，taskId: {task_id}")
    return task_id


def _poll_task(api_key: str, task_id: str) -> dict:
    """轮询任务状态直到完成或超时。返回最终响应 dict。"""
    query_url = f"{BASE_URL}{QUERY_PATH}"
    elapsed = 0
    while elapsed < MAX_WAIT_SECONDS:
        time.sleep(POLL_INTERVAL)
        elapsed += POLL_INTERVAL
        resp = _http_post(query_url, {"taskId": task_id}, api_key)
        status = resp.get("status", "").upper()
        print(f"[~] 任务状态: {status}（已等待 {elapsed}s）")

        if status == "SUCCESS":
            return resp
        if status in {"FAILED", "ERROR", "CANCELLED"}:
            err_msg = resp.get("errorMessage") or resp.get("errorCode") or status
            raise RuntimeError(f"任务失败: {err_msg}")
        # RUNNING / PENDING → 继续等待

    raise TimeoutError(f"任务 {task_id} 在 {MAX_WAIT_SECONDS}s 内未完成")


def _extract_image_urls(result: dict) -> list[str]:
    """从任务结果中解析图片 URL 列表。"""
    results = result.get("results") or []
    urls = []
    for item in results:
        # results 可能是 list of dict 或 list of str
        if isinstance(item, dict):
            url = item.get("url") or item.get("imageUrl") or item.get("fileUrl")
            if url:
                urls.append(url)
        elif isinstance(item, str) and item.startswith("http"):
            urls.append(item)
    return urls


def _save_images(urls: list[str], output_dir: Path) -> list[Path]:
    """批量下载图片到本地，返回保存路径列表。"""
    output_dir.mkdir(parents=True, exist_ok=True)
    saved = []
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    for i, url in enumerate(urls):
        # 尽量保留原始扩展名
        ext = url.split("?")[0].rsplit(".", 1)[-1] if "." in url.split("?")[0] else "png"
        ext = ext if ext in {"jpg", "jpeg", "png", "webp", "gif"} else "png"
        suffix = f"_{i+1}" if len(urls) > 1 else ""
        filename = f"generated_{timestamp}{suffix}.{ext}"
        dest = output_dir / filename
        print(f"[↓] 下载图片 → {dest}")
        _download_file(url, dest)
        saved.append(dest)
    return saved


# ---------------------------------------------------------------------------
# 参数校验
# ---------------------------------------------------------------------------
def _validate_args(args: argparse.Namespace) -> None:
    if args.aspect_ratio and args.aspect_ratio not in VALID_ASPECT_RATIOS:
        print(
            f"[!] 警告: --aspect-ratio '{args.aspect_ratio}' 不在推荐列表中。"
            f" 支持: {', '.join(sorted(VALID_ASPECT_RATIOS))}"
        )
    if args.resolution not in VALID_RESOLUTIONS:
        print(
            f"[!] 警告: --resolution '{args.resolution}' 不在推荐列表中。"
            f" 支持: {', '.join(sorted(VALID_RESOLUTIONS))}"
        )


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------
def main() -> None:
    _load_dotenv()

    # ---- 参数解析（先于 API Key 校验，保证 --help 正常输出）----
    parser = argparse.ArgumentParser(
        description="nanobanana2生成工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="mode", required=True)

    # -- 文生图子命令 --
    text_parser = subparsers.add_parser("text", help="文生图：根据提示词生成图片")
    text_parser.add_argument("--prompt", required=True, help="图片描述提示词")
    text_parser.add_argument(
        "--aspect-ratio",
        default=None,
        metavar="RATIO",
        help=f"宽高比（不填默认 1:1）。可选: {', '.join(sorted(VALID_ASPECT_RATIOS))}",
    )
    text_parser.add_argument(
        "--resolution",
        default="1k",
        choices=list(VALID_RESOLUTIONS),
        help="分辨率（默认: 1k）",
    )
    text_parser.add_argument(
        "--channel",
        default="budget",
        choices=["budget", "official"],
        help="渠道版本：budget=低价渠道版（个人Key）/ official=官方稳定版（企业Key）（默认: budget）",
    )
    text_parser.add_argument(
        "--output",
        default="./output",
        metavar="DIR",
        help="图片保存目录（默认: ./output）",
    )

    # -- 图生图子命令 --
    img_parser = subparsers.add_parser("image", help="图生图：参考图片生成新图")
    img_parser.add_argument(
        "--images",
        required=True,
        nargs="+",
        metavar="URL",
        help="参考图片 URL（可传多个，用空格分隔）",
    )
    img_parser.add_argument("--prompt", required=True, help="图片处理或生成提示词")
    img_parser.add_argument(
        "--aspect-ratio",
        default=None,
        metavar="RATIO",
        help=f"输出图片宽高比（不填则遵循原图）。可选: {', '.join(sorted(VALID_ASPECT_RATIOS))}",
    )
    img_parser.add_argument(
        "--resolution",
        default="1k",
        choices=list(VALID_RESOLUTIONS),
        help="分辨率（默认: 1k）",
    )
    img_parser.add_argument(
        "--channel",
        default="budget",
        choices=["budget", "official"],
        help="渠道版本：budget=低价渠道版（个人Key）/ official=官方稳定版（企业Key）（默认: budget）",
    )
    img_parser.add_argument(
        "--output",
        default="./output",
        metavar="DIR",
        help="图片保存目录（默认: ./output）",
    )

    args = parser.parse_args()

    # API Key 校验（在 argparse 之后，避免 --help 被拦截）
    api_key = os.environ.get("NANOBANANA_API_KEY", "").strip()
    if not api_key or api_key == "your_api_key_here":
        print(
            "[✗] 未找到有效的 NANOBANANA_API_KEY。\n"
            "    请在技能根目录创建 .env 文件：\n"
            "      echo 'NANOBANANA_API_KEY=你的密钥' > .env"
        )
        sys.exit(1)

    _validate_args(args)

    output_dir = Path(args.output)

    # ---- 构建请求体 ----
    payload = {
        "prompt": args.prompt,
        "resolution": args.resolution,
    }

    if args.mode == "text":
        # 文生图强制要求比例，默认为 1:1
        payload["aspectRatio"] = args.aspect_ratio or "1:1"
    else:
        # 图生图：处理图片输入（支持本地路径和 URL）
        final_urls = []
        for img_input in args.images:
            if img_input.startswith(("http://", "https://")):
                final_urls.append(img_input)
            else:
                # 检查是否为本地文件
                local_path = Path(img_input)
                if local_path.exists() and local_path.is_file():
                    try:
                        remote_url = _upload_file(api_key, local_path)
                        final_urls.append(remote_url)
                    except Exception as e:
                        print(f"[✗] 自动上传本地文件失败: {e}")
                        sys.exit(1)
                else:
                    print(f"[✗] 输入无效：既不是 URL 也不是有效的本地文件路径: {img_input}")
                    sys.exit(1)

        payload["imageUrls"] = final_urls
        # 如果用户指定了比例则传递，否则 API 通常默认遵循原图
        if args.aspect_ratio:
            payload["aspectRatio"] = args.aspect_ratio

        print(f"[i] 参考图片数量: {len(final_urls)}")
        for i, url in enumerate(final_urls, 1):
            print(f"    [{i}] {url}")


    # ---- 提交 → 轮询 → 下载 ----
    channel = args.channel
    print(f"[i] 渠道: {channel}")
    try:
        task_id = _submit_task(api_key, args.mode, payload, channel)
        result = _poll_task(api_key, task_id)

        image_urls_result = _extract_image_urls(result)
        if not image_urls_result:
            print(f"[!] 任务完成，但未能从响应中解析到图片 URL。\n    完整响应: {result}")
            sys.exit(1)

        print(f"\n[✓] 生成完成！共 {len(image_urls_result)} 张图片：")
        for url in image_urls_result:
            print(f"    {url}")

        saved = _save_images(image_urls_result, output_dir)
        print(f"\n[✓] 已保存到:")
        for p in saved:
            print(f"    {p.resolve()}")

    except (RuntimeError, TimeoutError) as e:
        print(f"\n[✗] 错误: {e}")
        sys.exit(1)
    except KeyboardInterrupt:
        print("\n[!] 用户中断")
        sys.exit(130)


if __name__ == "__main__":
    main()
