#!/usr/bin/env bash
# Drill: fill the disk an encode writes to, to provoke ENOSPC -> STORAGE_FULL classification.
# Most invasive drill: needs sudo (losetup/mount). Uses a small loopback fs so the host disk is never at risk.
# The real guard is Phase 7's storage watermark (load shedding before the disk fills); this proves the
# back-stop: an ENOSPC mid-encode is classified, not a silent corrupt artifact.
set -euo pipefail

IMG="${IMG:-/tmp/tideo-chaos-disk.img}"
MNT="${MNT:-/tmp/tideo-chaos-disk}"
SIZE_MB="${SIZE_MB:-100}"

case "${1:-}" in
  up)
    sudo mkdir -p "$MNT"
    fallocate -l "${SIZE_MB}M" "$IMG"
    mkfs.ext4 -q "$IMG"
    sudo mount -o loop "$IMG" "$MNT"
    echo "[chaos] mounted ${SIZE_MB}MB fs at $MNT."
    echo "[chaos] point the worker output here (DATA_DIR=$MNT, recreate workers), submit a job, then: $0 fill"
    ;;
  fill)
    # fill the loopback fs to within a few MB so the next encode write hits ENOSPC
    sudo dd if=/dev/zero of="$MNT/ballast" bs=1M 2>/dev/null || true
    echo "[chaos] $MNT is full. The in-flight encode should fail STORAGE_FULL (no half-written final artifact)."
    ;;
  down)
    sudo umount "$MNT" 2>/dev/null || true
    rm -f "$IMG"; sudo rmdir "$MNT" 2>/dev/null || true
    echo "[chaos] cleaned up."
    ;;
  *)
    echo "usage: $0 {up|fill|down}"; exit 1 ;;
esac
