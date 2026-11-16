"""Re-pack binary APK assets as base64 text (developer tool).

Source: the local Coupon GO! 1.2.6 APK unpack (NOT part of this repo).
Run from anywhere:
    python3 apps/mobile/tools/pack_assets.py
Writes apps/mobile/assets-packed/*.b64 + manifest.json.
Only .png/.gif/.ttf image & font files are packed. Never pack secrets.
"""
import base64
import hashlib
import json
import os
import textwrap

HERE = os.path.dirname(os.path.abspath(__file__))
MOBILE = os.path.dirname(HERE)
OUT = os.path.join(MOBILE, "assets-packed")
SRC = os.environ.get(
    "COUPON_GO_PACKAGED_ASSETS",
    os.path.expanduser("~/workspace/coupon-go-apk/packaged-assets"),
)
EXTRA = os.environ.get(
    "COUPON_GO_SPLASH_GIF",
    os.path.expanduser(
        "~/workspace/coupon-go-apk/xapk/apk_unzip/res/drawable-mdpi-v4/"
        "src_assets_images_splash.gif"
    ),
)

manifest = []


def add(src_path, target):
    data = open(src_path, "rb").read()
    b64 = base64.b64encode(data).decode("ascii")
    out_path = os.path.join(OUT, target + ".b64")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        f.write("\n".join(textwrap.wrap(b64, 60)) + "\n")
    manifest.append(
        {
            "target": target,
            "source": os.path.basename(src_path),
            "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        }
    )


def main():
    for name in sorted(os.listdir(SRC)):
        p = os.path.join(SRC, name)
        if not os.path.isfile(p):
            continue
        if name.lower().endswith(".ttf"):
            add(p, f"fonts/{name}")
        else:
            add(p, f"images/{name}")
    add(EXTRA, "images/splash.gif")
    with open(os.path.join(OUT, "manifest.json"), "w") as f:
        json.dump(
            {
                "assets": manifest,
                "total_files": len(manifest),
                "total_bytes": sum(m["bytes"] for m in manifest),
            },
            f,
            indent=2,
        )
    print(f"packed {len(manifest)} files, "
          f"{sum(m['bytes'] for m in manifest)} bytes")


if __name__ == "__main__":
    main()
