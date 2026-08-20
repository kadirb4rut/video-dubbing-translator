#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

INPUT_VIDEO="${1:-uploads/input-highlight-15s.mp4}"
TARGET_VIDEO="${2:-uploads/target-voxcpm2-highlight-15s.mp4}"
OUTPUT_VIDEO="${3:-docs/assets/demo-comparison-15s.mp4}"

if [[ ! -f "$INPUT_VIDEO" || ! -f "$TARGET_VIDEO" ]]; then
  echo "Usage: $0 INPUT_VIDEO TARGET_VIDEO [OUTPUT_VIDEO]" >&2
  echo "Input files were not found. Download the demo release assets first." >&2
  exit 1
fi

mkdir -p "$(dirname "$OUTPUT_VIDEO")"

# The first and second halves use the real source and generated audio. Labels are
# deliberately minimal so the same artifact works in README links and social posts.
# Some minimal FFmpeg builds omit libfreetype/drawtext, so a colored label bar is
# retained as a portable fallback instead of making the generator fail.
if ffmpeg -hide_banner -filters 2>/dev/null | grep -q 'drawtext'; then
  LABEL_0="drawbox=x=0:y=0:w=iw:h=58:color=black@0.70:t=fill,drawtext=text='ORIGINAL  —  ENGLISH':fontcolor=white:fontsize=28:x=40:y=15"
  LABEL_1="drawbox=x=0:y=0:w=iw:h=58:color=black@0.70:t=fill,drawtext=text='AI DUB  —  TURKISH':fontcolor=white:fontsize=28:x=40:y=15"
else
  echo "Warning: this FFmpeg build has no drawtext filter; creating unlabeled color bars." >&2
  LABEL_0="drawbox=x=0:y=0:w=iw:h=58:color=black@0.70:t=fill"
  LABEL_1="drawbox=x=0:y=0:w=iw:h=58:color=black@0.70:t=fill"
fi

ffmpeg -hide_banner -loglevel error -y \
  -i "$INPUT_VIDEO" -i "$TARGET_VIDEO" \
  -filter_complex "[0:v]trim=duration=7.5,setpts=PTS-STARTPTS,$LABEL_0[v0];[1:v]trim=duration=7.5,setpts=PTS-STARTPTS,$LABEL_1[v1];[v0][0:a][v1][1:a]concat=n=2:v=1:a=1[outv][outa]" \
  -map "[outv]" -map "[outa]" -t 15 \
  -c:v libx264 -crf 20 -preset medium -pix_fmt yuv420p \
  -c:a aac -b:a 192k -ar 48000 -movflags +faststart "$OUTPUT_VIDEO"

echo "Created $OUTPUT_VIDEO"
