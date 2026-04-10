# Infrastructure Rules

## Build Location
- ALL project code must be built on the Mac Mini — never locally on the laptop
- Mac Mini: `ssh timtrailor@100.126.253.40` (Tailscale) or `192.168.0.172` (LAN)
- Working directory: `~/code/` on Mac Mini
- If running Claude on the laptop, SSH to Mac Mini and work there
- The laptop is a thin client — it accesses the Mac Mini, it doesn't host code

## Project Structure
- Every non-trivial project gets a `topics/` folder for structured data/config
- Credentials stay in `~/code/credentials.py` (gitignored) on the Mac Mini
- Shared utilities go in `~/code/shared_utils.py`

## Tailscale
- All machines are on Tailscale and can reach each other
- Mac Mini Tailscale IP: 100.126.253.40
- MacBook Pro Tailscale IP: 100.112.125.42
- iPhone Tailscale IP: 100.94.119.47
- Always prefer Tailscale IPs for cross-machine access
