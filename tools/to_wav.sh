#!/bin/sh
# Convert audio files (MP3, M4A, AAC, FLAC, AIFF...) to WAV for the PicoCalc
# music player: 16-bit PCM, 22,050 Hz, stereo (88 KB/s, what the SD card and
# the speakers handle well). Uses macOS afconvert, or ffmpeg elsewhere.
#   tools/to_wav.sh song.mp3 [more...]   ->  song.wav next to each file
# Then copy the .wav files to /sd/music on the card.
set -e
[ $# -gt 0 ] || { echo "usage: $0 file [file...]" >&2; exit 1; }
for f in "$@"; do
  out="${f%.*}.wav"
  if command -v afconvert >/dev/null 2>&1; then
    afconvert -f WAVE -d LEI16@22050 -c 2 "$f" "$out"
  elif command -v ffmpeg >/dev/null 2>&1; then
    ffmpeg -loglevel error -y -i "$f" -ar 22050 -ac 2 -c:a pcm_s16le "$out"
  else
    echo "needs afconvert (macOS) or ffmpeg" >&2
    exit 1
  fi
  echo "$out"
done
