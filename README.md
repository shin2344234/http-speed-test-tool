# HTTP Speed Test Tool

This is a dependency-free Python tool for testing HTTP/HTTPS download and upload throughput.
It is meant for cases where raw TCP/UDP tests look good, but HTTP transfers are slow.

## Requirements

- Python 3.9 or newer, or the bundled Codex Python runtime on this machine
- A second machine, VPS, or server outside the problem network if you want a real end-to-end test

On Windows, use `.\http-speed-test.cmd` from this folder. It will find `python`, `py`, or the bundled Codex Python runtime automatically.

On Linux, use `sh ./http-speed-test.sh` or run `chmod +x ./http-speed-test.sh` once and then use `./http-speed-test.sh`.

## Quick Local Test

Terminal 1:

```powershell
.\http-speed-test.cmd server --host 0.0.0.0 --port 8080
```

Terminal 2:

```powershell
.\http-speed-test.cmd both http://127.0.0.1:8080 --download-size 500M --upload-size 100M --runs 3
```

Loopback should be very fast. If loopback is also stuck below 10 Mbps, the bottleneck is likely local software, endpoint security, proxy settings, or the machine itself.

## LAN Or Remote Test

Run the server on a known-good machine:

```powershell
.\http-speed-test.cmd server --host 0.0.0.0 --port 8080
```

Then run the client from the affected machine:

```powershell
.\http-speed-test.cmd both http://SERVER_IP:8080 --download-size 1G --upload-size 500M --runs 3 --progress
```

If this is over the public internet, allow inbound TCP 8080 on the server firewall or change `--port` to a port you can reach.

## Windows Client Bootstrap

Use this on a Windows client to create `C:\install\speed`, check for Python 3.9+, install Python with winget if needed, download the tool files, and run a test against the server IP you provide.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -Command "New-Item -ItemType Directory -Force -Path C:\install\speed | Out-Null; Invoke-WebRequest -UseBasicParsing https://raw.githubusercontent.com/shin2344234/http-speed-test-tool/main/install-and-run-speed-test.ps1 -OutFile C:\install\speed\install-and-run-speed-test.ps1; & C:\install\speed\install-and-run-speed-test.ps1 -ServerIp SERVER_IP"
```

Or, if you already have the repo/files locally:

```powershell
.\install-and-run-speed-test.ps1 -ServerIp SERVER_IP
```

Optional parameters:

```powershell
.\install-and-run-speed-test.ps1 -ServerIp SERVER_IP -Port 8080 -DownloadSize 1G -UploadSize 500M -Runs 3
```

This script assumes it is running as the logged-in user. If Python is missing or older than 3.9, it uses:

```powershell
winget install -e --id Python.Python.3.13 --scope user --silent --accept-package-agreements --accept-source-agreements --disable-interactivity
```

## Linux Server

One-command deploy from GitHub and run in the foreground:

```bash
curl -fsSL https://raw.githubusercontent.com/shin2344234/http-speed-test-tool/main/install-and-run-linux-server.sh | sh
```

Set the port in the same one-liner:

```bash
curl -fsSL https://raw.githubusercontent.com/shin2344234/http-speed-test-tool/main/install-and-run-linux-server.sh | sh -s -- --port 9090
```

For NinjaOne or other remote scripting, start the server in the background:

```bash
curl -fsSL https://raw.githubusercontent.com/shin2344234/http-speed-test-tool/main/install-and-run-linux-server.sh | sh -s -- --background --quiet
```

NinjaOne/background with a custom port:

```bash
curl -fsSL https://raw.githubusercontent.com/shin2344234/http-speed-test-tool/main/install-and-run-linux-server.sh | sh -s -- --port 9090 --background --quiet
```

If the Linux machine has `wget` but not `curl`:

```bash
wget -qO- https://raw.githubusercontent.com/shin2344234/http-speed-test-tool/main/install-and-run-linux-server.sh | sh -s -- --port 9090 --background --quiet
```

Custom port or install directory:

```bash
curl -fsSL https://raw.githubusercontent.com/shin2344234/http-speed-test-tool/main/install-and-run-linux-server.sh | sh -s -- --port 8080 --install-dir "$HOME/.local/share/http-speed-test-tool" --background --quiet
```

The Linux bootstrap script checks for Python 3.9+, installs Python 3 with the system package manager if needed, downloads the current tool files from GitHub, and starts the server. The default install folder is:

```bash
$HOME/.local/share/http-speed-test-tool
```

To stop a background server:

```bash
kill "$(cat "$HOME/.local/share/http-speed-test-tool/server.pid")"
```

Copy `http_speed_test.py` and `http-speed-test.sh` to the Linux device, then run:

```bash
chmod +x ./http-speed-test.sh
./http-speed-test.sh server --host 0.0.0.0 --port 8080
```

If Python is not installed:

```bash
sudo apt update && sudo apt install -y python3
```

From the Windows client, test against the Linux server:

```powershell
.\http-speed-test.cmd both http://LINUX_SERVER_IP:8080 --download-size 1G --upload-size 500M --runs 3 --progress
```

For a quick Linux-side sanity check:

```bash
./http-speed-test.sh both http://127.0.0.1:8080 --download-size 500M --upload-size 100M --runs 3
```

## Test A Direct HTTP/HTTPS Download URL

You can point the download tester at any large direct file URL:

```powershell
.\http-speed-test.cmd download "https://example.com/large-test-file.bin" --runs 3 --progress
```

Use `--cache-bust` only when the server accepts arbitrary query parameters:

```powershell
.\http-speed-test.cmd download "https://example.com/large-test-file.bin" --cache-bust
```

## Test Upload To A Server You Control

The included server accepts uploads at `/upload`:

```powershell
.\http-speed-test.cmd upload http://SERVER_IP:8080/upload --size 500M --runs 3 --progress
```

Most random public websites do not accept large POST bodies, so upload tests are best run against this tool's server or another endpoint you control.

## Useful Comparisons

Run the same test from:

- The affected machine on HTTP
- Another machine on the same network
- A phone hotspot or different ISP
- A machine outside the network back toward the affected site
- A browser download versus this CLI download

If only HTTP is below 10 Mbps while iperf/TCP/UDP are fine, likely suspects include HTTP inspection, antivirus web filtering, transparent proxying, QoS rules, MTU/MSS issues, WAN optimizer behavior, or a CDN/server-specific path.

## Commands

```powershell
.\http-speed-test.cmd --help
.\http-speed-test.cmd server --help
.\http-speed-test.cmd download --help
.\http-speed-test.cmd upload --help
.\http-speed-test.cmd both --help
```

Speeds are reported as decimal Mbps. Size suffixes like `500M` and `1G` are decimal bytes; `MiB` and `GiB` are also supported.
