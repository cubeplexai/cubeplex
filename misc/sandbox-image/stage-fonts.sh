#!/usr/bin/env bash
# Stage a curated set of premium, license-clean fonts for the sandbox image.
#
# All fonts are downloaded from their OFFICIAL upstreams (Google Fonts repo,
# the type foundries' own GitHub releases / sites) — never from a third-party
# bundle. Output goes to ./fonts/ (gitignored; COPY'd into the image by the
# Dockerfile + fc-cache). Budget target ~300MB.
#
# Licenses (all permit redistribution / commercial use):
#   Latin — Inter, Barlow, Anton, Oranienbaum, Unna, Liter, SortsMillGoudy,
#           HedvigLettersSans, QuattrocentoSans : SIL OFL 1.1 (Google Fonts)
#   CJK   — LXGW WenKai, LXGW Bright            : SIL OFL 1.1 (github.com/lxgw)
#           Smiley Sans (得意黑)                : SIL OFL 1.1 (atelier-anchor)
#           Noto Sans/Serif CJK SC              : SIL OFL 1.1 (installed via apt)
#           MiSans                              : free-for-commercial (Xiaomi EULA)
#           Alimama ShuHeiTi / DongFangDaKai    : free-for-commercial (Alibaba)
# See LICENSES note printed at the end.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
OUT="$HERE/fonts"
rm -rf "$OUT"; mkdir -p "$OUT"

GF="https://github.com/google/fonts/raw/main/ofl"     # Google Fonts OFL tree
dl() {  # dl <url> <dest-filename>
  local url="$1" dst="$OUT/$2"
  if curl -fsSL --retry 4 --retry-delay 2 --connect-timeout 20 -o "$dst" "$url"; then
    echo "  ok   $2 ($(du -h "$dst" | cut -f1))"
  else
    echo "  MISS $2  <- $url" >&2; rm -f "$dst"
  fi
}

echo "→ Latin (Google Fonts, OFL)"
dl "$GF/inter/Inter%5Bopsz,wght%5D.ttf"                 "Inter.ttf"
dl "$GF/barlow/Barlow-Regular.ttf"                      "Barlow-Regular.ttf"
dl "$GF/barlow/Barlow-Bold.ttf"                         "Barlow-Bold.ttf"
dl "$GF/anton/Anton-Regular.ttf"                        "Anton-Regular.ttf"
dl "$GF/oranienbaum/Oranienbaum-Regular.ttf"            "Oranienbaum-Regular.ttf"
dl "$GF/unna/Unna-Regular.ttf"                          "Unna-Regular.ttf"
dl "$GF/unna/Unna-Bold.ttf"                             "Unna-Bold.ttf"
dl "$GF/liter/Liter-Regular.ttf"                        "Liter-Regular.ttf"
dl "$GF/sortsmillgoudy/SortsMillGoudy-Regular.ttf"      "SortsMillGoudy-Regular.ttf"
dl "$GF/quattrocentosans/QuattrocentoSans-Regular.ttf" "QuattrocentoSans-Regular.ttf"

echo "→ CJK display/serif (Google Fonts, OFL)"
dl "$GF/zcoolkuaile/ZCOOLKuaiLe-Regular.ttf"            "ZCOOLKuaiLe-Regular.ttf"
dl "$GF/zcoolxiaowei/ZCOOLXiaoWei-Regular.ttf"          "ZCOOLXiaoWei-Regular.ttf"
dl "$GF/zcoolqingkehuangyou/ZCOOLQingKeHuangYou-Regular.ttf" "ZCOOLQingKeHuangYou-Regular.ttf"
dl "$GF/mashanzheng/MaShanZheng-Regular.ttf"            "MaShanZheng-Regular.ttf"
dl "$GF/longcang/LongCang-Regular.ttf"                  "LongCang-Regular.ttf"

echo "→ CJK body (official foundry releases, OFL)"
# LXGW WenKai (OFL) — elegant Kai, great for body / quotes
dl "https://github.com/lxgw/LxgwWenKai/releases/download/v1.520/LXGWWenKai-Regular.ttf" "LXGWWenKai-Regular.ttf"
# Smiley Sans / 得意黑 (OFL) — bold oblique display Hei
if curl -fsSL --retry 4 --connect-timeout 20 -o /tmp/smiley.zip \
    "https://github.com/atelier-anchor/smiley-sans/releases/download/v2.0.1/smiley-sans-v2.0.1.zip"; then
  unzip -joq /tmp/smiley.zip '*.ttf' -d "$OUT" 2>/dev/null && echo "  ok   SmileySans" || echo "  MISS SmileySans (unzip)" >&2
  rm -f /tmp/smiley.zip
else echo "  MISS SmileySans (download)" >&2; fi

# Premium free-for-commercial Hei (MiSans / Alibaba PuHuiTi): official sources
# are the Xiaomi (hyperos.mi.com/font) and Alibaba (alibabafont.taobao.com) font
# portals, which ship zips behind a click-through, not stable raw URLs. They are
# free for commercial use; fetch the .ttf from the vendor portal into ./fonts/
# before building if you want them. The OFL set above already gives a strong
# CJK Hei via the image's Noto Sans CJK SC.

echo ""
echo "Staged $(ls "$OUT" | wc -l) fonts, $(du -sh "$OUT" | cut -f1) in $OUT"
echo "Anything marked MISS was unreachable from this network — re-run or fetch"
echo "those from the official source listed in the header before building."
