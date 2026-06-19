"""把最新发布包整理成「手机发布套件」，统一放进 outbox/，方便一步传到手机。

固定通道：
    python main.py ... --direct-ai-card        # 生成发布包到 dist/
    python prepare_publish.py                   # 整理成 outbox/<包名>/ + <包名>.zip

然后把 outbox 里的那个 zip（或文件夹）通过你习惯的方式发到手机：
    - 微信「文件传输助手」：发给自己，手机接收，跨平台都行
    - iPhone：python prepare_publish.py --sync "<iCloud Drive 文件夹>" 自动同步
手机端：图片存进相册 → 小红书选这 7 张；发布单里的文案长按复制即可。
"""
from __future__ import annotations

import argparse
import shutil
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DIST = ROOT / "dist"
OUTBOX = ROOT / "outbox"


def latest_package() -> Path | None:
    if not DIST.exists():
        return None
    packages = [p for p in DIST.iterdir() if p.is_dir()]
    return max(packages, key=lambda p: p.stat().st_mtime) if packages else None


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _split_title(markdown: str) -> tuple[str, str]:
    """从文案 md 里抽出首个 # 标题，返回(标题, 去掉标题行的正文)。"""
    lines = markdown.splitlines()
    title = ""
    body_start = 0
    for i, line in enumerate(lines):
        if line.startswith("# "):
            title = line[2:].strip()
            body_start = i + 1
            break
    body = "\n".join(lines[body_start:]).strip()
    return title, body


def build_publish_sheet(pkg: Path) -> str:
    xhs_title, xhs_body = _split_title(_read(pkg / "小红书正文.md"))
    wechat_title, wechat_body = _split_title(_read(pkg / "公众号文章.md"))
    images = sorted(pkg.glob("card_*.png"))

    parts: list[str] = []
    parts.append("# 手机发布单")
    parts.append(f"> 发布包：{pkg.name}\n")

    parts.append("## ① 配图（小红书按顺序选这 7 张）")
    for img in images:
        parts.append(f"- {img.name}")
    parts.append("")

    parts.append("## ② 小红书")
    parts.append("**标题**（复制到标题栏）：")
    parts.append("```\n" + (xhs_title or "（无标题）") + "\n```")
    parts.append("**正文 + 标签**（复制到正文栏）：")
    parts.append("```\n" + xhs_body + "\n```\n")

    parts.append("## ③ 公众号（建议电脑后台 mp.weixin.qq.com 粘贴排版）")
    parts.append("**标题**：")
    parts.append("```\n" + (wechat_title or "（无标题）") + "\n```")
    parts.append("**正文**：")
    parts.append("```\n" + wechat_body + "\n```\n")

    checklist = _read(pkg / "发布清单.md")
    if checklist:
        parts.append("## ④ 发布前自查")
        parts.append(checklist)

    return "\n".join(parts) + "\n"


def prepare(pkg: Path, sync_dir: Path | None) -> tuple[Path, Path]:
    OUTBOX.mkdir(exist_ok=True)
    kit_dir = OUTBOX / pkg.name
    if kit_dir.exists():
        shutil.rmtree(kit_dir)
    kit_dir.mkdir(parents=True)

    # 图片 + 文案 + 手机发布单
    for img in sorted(pkg.glob("card_*.png")):
        shutil.copy2(img, kit_dir / img.name)
    for doc in ["小红书正文.md", "公众号文章.md", "发布清单.md"]:
        src = pkg / doc
        if src.exists():
            shutil.copy2(src, kit_dir / doc)
    (kit_dir / "手机发布单.md").write_text(build_publish_sheet(pkg), encoding="utf-8")

    # 打包 zip
    zip_path = OUTBOX / f"{pkg.name}.zip"
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for item in sorted(kit_dir.iterdir()):
            zf.write(item, arcname=item.name)

    # 可选：同步到 iCloud Drive 等目录
    if sync_dir is not None:
        sync_dir = sync_dir.expanduser()
        sync_dir.mkdir(parents=True, exist_ok=True)
        dest = sync_dir / pkg.name
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(kit_dir, dest)
        shutil.copy2(zip_path, sync_dir / zip_path.name)

    return kit_dir, zip_path


def main() -> None:
    parser = argparse.ArgumentParser(description="把发布包整理成手机发布套件，放到 outbox/。")
    parser.add_argument("--dir", help="指定发布包目录；默认取 dist/ 里最新的一个。")
    parser.add_argument("--sync", help="额外同步到的目录（如 iCloud Drive 文件夹），手机自动可见。")
    args = parser.parse_args()

    pkg = Path(args.dir).expanduser().resolve() if args.dir else latest_package()
    if pkg is None or not pkg.exists():
        raise SystemExit("没有找到发布包。先用 main.py 生成，或用 --dir 指定。")

    sync_dir = Path(args.sync) if args.sync else None
    kit_dir, zip_path = prepare(pkg, sync_dir)

    print(f"发布套件已就绪：{kit_dir}")
    print(f"打包文件：{zip_path}")
    print("手机发布单：" + str(kit_dir / "手机发布单.md"))
    if sync_dir:
        print(f"已同步到：{sync_dir}")
    print("\n下一步：把上面的 zip 用微信「文件传输助手」发给自己（或已同步到 iCloud），手机接收即可。")


if __name__ == "__main__":
    main()
