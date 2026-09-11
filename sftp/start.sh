#!/usr/bin/env bash
# Boot the SFTP receiver: persistent host keys + a chrooted, sftp-only upload
# user, then run sshd alongside the import poller.
set -euo pipefail

SFTP_USER="${SFTP_USER:-kerridge}"
SFTP_PORT="${SFTP_PORT:-2222}"
DATA=/data
: "${SFTP_PASSWORD:?Set SFTP_PASSWORD in the environment}"

mkdir -p "$DATA/ssh" "$DATA/upload/incoming" "$DATA/upload/processed" /run/sshd

# --- persistent host keys (so the ERP's stored fingerprint stays valid) ---
if [ ! -f "$DATA/ssh/ssh_host_ed25519_key" ]; then
  ssh-keygen -t ed25519 -f "$DATA/ssh/ssh_host_ed25519_key" -N "" -q
fi
if [ ! -f "$DATA/ssh/ssh_host_rsa_key" ]; then
  ssh-keygen -t rsa -b 4096 -f "$DATA/ssh/ssh_host_rsa_key" -N "" -q
fi

# --- sftp-only user (no shell), password from env ---
if ! id "$SFTP_USER" >/dev/null 2>&1; then
  useradd -M -d /upload -s /usr/sbin/nologin "$SFTP_USER"
fi
echo "$SFTP_USER:$SFTP_PASSWORD" | chpasswd

# chroot layout: the chroot dir must be root-owned & not writable; the user
# writes only into incoming/.
chown root:root "$DATA/upload"; chmod 755 "$DATA/upload"
chown "$SFTP_USER:$SFTP_USER" "$DATA/upload/incoming" "$DATA/upload/processed"

cat > /etc/ssh/sshd_config <<EOF
Port $SFTP_PORT
HostKey $DATA/ssh/ssh_host_ed25519_key
HostKey $DATA/ssh/ssh_host_rsa_key
PermitRootLogin no
PasswordAuthentication yes
ChallengeResponseAuthentication no
UsePAM no
Subsystem sftp internal-sftp
Match User $SFTP_USER
  ChrootDirectory $DATA/upload
  ForceCommand internal-sftp -d /incoming
  AllowTcpForwarding no
  X11Forwarding no
EOF

echo "===================================================================="
echo " SFTP host key fingerprints — enter one in the ERP 'Fingerprint' box."
echo " Most ERPs (incl. Kerridge) want the RSA key; use SHA256 if offered,"
echo " else the MD5 colon-hex line."
echo " -- RSA (pick this if the ERP only lists DSA/RSA) --"
ssh-keygen -lf "$DATA/ssh/ssh_host_rsa_key.pub" || true
ssh-keygen -E md5 -lf "$DATA/ssh/ssh_host_rsa_key.pub" || true
echo " -- ED25519 --"
ssh-keygen -lf "$DATA/ssh/ssh_host_ed25519_key.pub" || true
ssh-keygen -E md5 -lf "$DATA/ssh/ssh_host_ed25519_key.pub" || true
echo " user=$SFTP_USER  port(in-container)=$SFTP_PORT  upload dir=/incoming"
echo "===================================================================="

/usr/sbin/sshd -D -e &
exec python3 /app/poller.py
