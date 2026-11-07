#!/usr/bin/env bash
# One-command bootstrap for the fuxi project inside an Ubuntu live session.
#
#   sudo bash fuxi-bootstrap.sh
#
# What it does:
#   1. picks a downloader (wget, curl or python3) - the live ISO may lack curl,
#   2. points apt at the Tsinghua mirror automatically (archive.ubuntu.com is
#      unreachable from most networks in China, which makes apt-get update fail),
#   3. installs Node 22 from the npmmirror tarball and the dsh agent from npm,
#   4. installs fastboot/adb/python3/libusb for the phone work,
#   5. mounts the Windows data disk (label St0rage) read-only at /mnt/win and
#      reuses the Windows DSH credentials when C: is readable,
#   6. starts the dsh web UI on 0.0.0.0:8080 and prints the phone-reachable URL.
#
# It never writes to a disk and never touches the phone.
set -u

if [ "$(id -u)" != 0 ]; then
  echo "run with sudo: sudo bash $0"
  exit 1
fi

log() { echo "[fuxi] $*"; }

# ---------------------------------------------------------------- downloader --
if command -v wget >/dev/null 2>&1; then
  dl() { wget -q -O "$2" "$1"; }
  DLNAME=wget
elif command -v curl >/dev/null 2>&1; then
  dl() { curl -fsSL --max-time 900 -o "$2" "$1"; }
  DLNAME=curl
elif command -v python3 >/dev/null 2>&1; then
  dl() { python3 -c 'import sys,urllib.request; urllib.request.urlretrieve(sys.argv[1], sys.argv[2])' "$1" "$2"; }
  DLNAME=python3
else
  echo "no downloader available (wget, curl, python3 all missing)"; exit 1
fi
log "downloader: $DLNAME"

# --------------------------------------------------------------------- apt ---
log "configuring apt to use the Tsinghua mirror"
CODENAME="$(. /etc/os-release 2>/dev/null; echo "${VERSION_CODENAME:-noble}")"
mkdir -p /etc/apt/sources.list.d
if [ -f /etc/apt/sources.list.d/ubuntu.sources ]; then
  cat > /etc/apt/sources.list.d/ubuntu.sources <<EOF
Types: deb
URIs: https://mirrors.tuna.tsinghua.edu.cn/ubuntu/
Suites: ${CODENAME} ${CODENAME}-updates ${CODENAME}-backports ${CODENAME}-security
Components: main restricted universe multiverse
Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg
EOF
else
  cat > /etc/apt/sources.list <<EOF
deb https://mirrors.tuna.tsinghua.edu.cn/ubuntu/ ${CODENAME} main restricted universe multiverse
deb https://mirrors.tuna.tsinghua.edu.cn/ubuntu/ ${CODENAME}-updates main restricted universe multiverse
deb https://mirrors.tuna.tsinghua.edu.cn/ubuntu/ ${CODENAME}-backports main restricted universe multiverse
deb https://mirrors.tuna.tsinghua.edu.cn/ubuntu/ ${CODENAME}-security main restricted universe multiverse
EOF
  [ -f /etc/apt/sources.list.d/ubuntu.sources ] && rm -f /etc/apt/sources.list.d/ubuntu.sources
fi

log "apt-get update"
for i in 1 2 3; do
  apt-get update -qq && break
  log "apt-get update failed (attempt $i); retrying in 5s"
  sleep 5
done

log "installing the phone toolchain"
apt-get install -y -qq android-tools-adb android-tools-fastboot python3 libusb-1.0-0 \
  usbutils ntfs-3g wget curl ca-certificates || log "apt install reported errors; continuing"

# -------------------------------------------------------------------- node ---
if ! command -v node >/dev/null 2>&1; then
  log "installing Node 22 from npmmirror"
  NODE_TARBALL=/tmp/node.tar.xz
  if ! dl https://npmmirror.com/mirrors/node/v22.20.0/node-v22.20.0-linux-x64.tar.xz "$NODE_TARBALL" \
     || [ ! -s "$NODE_TARBALL" ]; then
    log "npmmirror download failed; trying the official nodejs.org mirror"
    dl https://nodejs.org/dist/v22.20.0/node-v22.20.0-linux-x64.tar.xz "$NODE_TARBALL" || {
      echo "could not download Node.js"; exit 1; }
  fi
  mkdir -p /opt/node22
  tar -xJf "$NODE_TARBALL" -C /opt/node22 --strip-components=1 || { echo "node tarball extract failed"; exit 1; }
  ln -sf /opt/node22/bin/node /usr/local/bin/node
  ln -sf /opt/node22/bin/npm  /usr/local/bin/npm
  ln -sf /opt/node22/bin/npx  /usr/local/bin/npx
fi
log "node $(node --version)"

# --------------------------------------------------------------------- dsh ---
log "installing the dsh agent (npmmirror registry)"
npm config set registry https://registry.npmmirror.com
npm install -g @deepseek-ai/dsh || { echo "npm install -g @deepseek-ai/dsh failed"; exit 1; }
dsh --version || true

# --------------------------------------------------------------- project disk -
log "mounting the project disk (St0rage) at /mnt/win"
mkdir -p /mnt/win
mounted=""
for dev in $(lsblk -pnro NAME,LABEL,FSTYPE | awk '$2=="St0rage"{print $1}'); do
  if mount -t ntfs3 -o ro "$dev" /mnt/win 2>/dev/null || mount -t ntfs-3g -o ro "$dev" /mnt/win 2>/dev/null; then
    mounted=$dev; break
  fi
done
if [ -n "$mounted" ]; then
  log "mounted $mounted at /mnt/win"
  ls /mnt/win
else
  log "WARNING: St0rage not mounted; project files are not at /mnt/win"
fi

# --------------------------------------------------------------- credentials -
log "looking for the Windows DSH credentials"
mkdir -p /root/.dsh
for dev in $(lsblk -pnro NAME,FSTYPE | awk '$2=="ntfs"||$2=="ntfs3"{print $1}'); do
  mkdir -p /mnt/cred
  mount -t ntfs3 -o ro "$dev" /mnt/cred 2>/dev/null || mount -t ntfs-3g -o ro "$dev" /mnt/cred 2>/dev/null || continue
  if [ -f "/mnt/cred/Users/Jason Wu/.dsh/.credentials.yaml" ]; then
    cp "/mnt/cred/Users/Jason Wu/.dsh/.credentials.yaml" /root/.dsh/ && log "credentials copied from $dev"
    umount /mnt/cred 2>/dev/null || true
    break
  fi
  umount /mnt/cred 2>/dev/null || true
done

# --------------------------------------------------------------------- run ---
WORK=/mnt/win/fuxi-linux
[ -d "$WORK" ] || WORK=/root/fuxi-work
mkdir -p "$WORK"
cd "$WORK" || exit 1
IP=$(hostname -I | awk '{print $1}')
cat <<EOM

============================================================
 The agent UI is about to start. From your PHONE's browser:

     http://${IP}:8080

 Both devices must be on the same network (USB tether works).
============================================================

EOM
exec dsh web --port 8080 --host 0.0.0.0 --no-open
