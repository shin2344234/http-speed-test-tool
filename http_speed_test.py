#!/usr/bin/env python3
"""
HTTP upload and download speed tester.

This script is intentionally dependency-free. It can run as:

  python http_speed_test.py server
  python http_speed_test.py both http://127.0.0.1:8080

The server exposes:
  GET  /download?size=500M&chunk=1M
  POST /upload
"""

from __future__ import annotations

import argparse
import json
import ssl
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from http.client import HTTPConnection, HTTPSConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Dict, Iterable, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse
from urllib.request import Request, urlopen


USER_AGENT = "http-speed-test/1.0"
DEFAULT_CHUNK_SIZE = 1024 * 1024


SIZE_UNITS = {
    "": 1,
    "b": 1,
    "k": 1_000,
    "kb": 1_000,
    "m": 1_000_000,
    "mb": 1_000_000,
    "g": 1_000_000_000,
    "gb": 1_000_000_000,
    "t": 1_000_000_000_000,
    "tb": 1_000_000_000_000,
    "kib": 1024,
    "mib": 1024**2,
    "gib": 1024**3,
    "tib": 1024**4,
}


@dataclass
class SpeedResult:
    operation: str
    url: str
    bytes_transferred: int
    seconds: float
    status: int
    reason: str = ""
    server_seconds: Optional[float] = None
    server_mbps: Optional[float] = None
    streams: int = 1

    @property
    def mbps(self) -> float:
        return bytes_to_mbps(self.bytes_transferred, self.seconds)


class ProgressPrinter:
    def __init__(self, label: str, enabled: bool) -> None:
        self.label = label
        self.enabled = enabled
        self.last_print = 0.0

    def update(self, total_bytes: int, started_at: float, force: bool = False) -> None:
        if not self.enabled:
            return
        now = time.perf_counter()
        if not force and now - self.last_print < 1.0:
            return
        elapsed = max(now - started_at, 0.000001)
        print(
            f"{self.label}: {format_bytes(total_bytes)} in {elapsed:.1f}s "
            f"({bytes_to_mbps(total_bytes, elapsed):.2f} Mbps)",
            file=sys.stderr,
        )
        self.last_print = now


class AggregateProgress:
    def __init__(self, label: str, enabled: bool) -> None:
        self.lock = threading.Lock()
        self.total_bytes = 0
        self.started_at = time.perf_counter()
        self.printer = ProgressPrinter(label, enabled)

    def add(self, byte_count: int) -> None:
        with self.lock:
            self.total_bytes += byte_count
            total = self.total_bytes
        self.printer.update(total, self.started_at)

    def finish(self) -> None:
        with self.lock:
            total = self.total_bytes
        self.printer.update(total, self.started_at, force=True)


def parse_size(value: str | int) -> int:
    if isinstance(value, int):
        return value
    text = value.strip().lower()
    if not text:
        raise argparse.ArgumentTypeError("size cannot be empty")

    number = []
    suffix = []
    seen_suffix = False
    for char in text:
        if char.isdigit() or (char == "." and not seen_suffix):
            if seen_suffix:
                raise argparse.ArgumentTypeError(f"invalid size: {value}")
            number.append(char)
        elif char.isalpha():
            seen_suffix = True
            suffix.append(char)
        elif char in (" ", "_"):
            continue
        else:
            raise argparse.ArgumentTypeError(f"invalid size: {value}")

    if not number:
        raise argparse.ArgumentTypeError(f"invalid size: {value}")

    unit = "".join(suffix)
    if unit not in SIZE_UNITS:
        valid = ", ".join(sorted(unit_name or "bytes" for unit_name in SIZE_UNITS))
        raise argparse.ArgumentTypeError(f"unknown size unit {unit!r}; valid units: {valid}")
    return int(float("".join(number)) * SIZE_UNITS[unit])


def format_bytes(byte_count: int) -> str:
    value = float(byte_count)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1000 or unit == "TB":
            return f"{value:.2f} {unit}" if unit != "B" else f"{byte_count} B"
        value /= 1000
    return f"{byte_count} B"


def bytes_to_mbps(byte_count: int, seconds: float) -> float:
    if seconds <= 0:
        return 0.0
    return (byte_count * 8) / seconds / 1_000_000


def make_payload_chunk(size: int) -> bytes:
    """Create a deterministic non-zero payload without per-byte Python work."""
    size = max(size, 1)
    pattern = bytes(range(256))
    return (pattern * ((size // len(pattern)) + 1))[:size]


def append_query(url: str, params: Dict[str, str]) -> str:
    parsed = urlparse(url)
    existing = parse_qs(parsed.query, keep_blank_values=True)
    for key, value in params.items():
        existing[key] = [value]
    query = urlencode(existing, doseq=True)
    return urlunparse(parsed._replace(query=query))


def ensure_base_url(base_url: str) -> str:
    return base_url.rstrip("/")


def split_size(total_bytes: int, parts: int) -> list[int]:
    base_size = total_bytes // parts
    remainder = total_bytes % parts
    return [base_size + (1 if index < remainder else 0) for index in range(parts)]


def make_ssl_context(insecure: bool) -> Optional[ssl.SSLContext]:
    if insecure:
        return ssl._create_unverified_context()
    return None


def download_once(
    url: str,
    chunk_size: int,
    timeout: float,
    duration: Optional[float],
    insecure: bool,
    progress: bool,
    progress_callback: Optional[Callable[[int], None]] = None,
    progress_label: str = "download",
) -> SpeedResult:
    headers = {
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "User-Agent": USER_AGENT,
    }
    request = Request(url, headers=headers)
    context = make_ssl_context(insecure)
    progress_printer = ProgressPrinter(progress_label, progress and progress_callback is None)
    total = 0
    started_at = time.perf_counter()

    try:
        with urlopen(request, timeout=timeout, context=context) as response:
            status = getattr(response, "status", 200)
            reason = getattr(response, "reason", "")
            while True:
                chunk = response.read(chunk_size)
                if not chunk:
                    break
                total += len(chunk)
                if progress_callback:
                    progress_callback(len(chunk))
                else:
                    progress_printer.update(total, started_at)
                if duration and time.perf_counter() - started_at >= duration:
                    break
    except HTTPError as error:
        raise RuntimeError(f"download failed: HTTP {error.code} {error.reason}") from error
    except URLError as error:
        raise RuntimeError(f"download failed: {error.reason}") from error

    seconds = time.perf_counter() - started_at
    if not progress_callback:
        progress_printer.update(total, started_at, force=True)
    return SpeedResult("download", url, total, seconds, int(status), str(reason))


def upload_once(
    url: str,
    size: int,
    chunk_size: int,
    timeout: float,
    insecure: bool,
    progress: bool,
    progress_callback: Optional[Callable[[int], None]] = None,
    progress_label: str = "upload",
) -> SpeedResult:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise RuntimeError("upload URL must start with http:// or https://")
    if not parsed.hostname:
        raise RuntimeError("upload URL is missing a hostname")

    path = parsed.path or "/"
    if parsed.query:
        path += f"?{parsed.query}"

    context = make_ssl_context(insecure)
    if parsed.scheme == "https":
        connection = HTTPSConnection(
            parsed.hostname,
            parsed.port,
            timeout=timeout,
            context=context,
        )
    else:
        connection = HTTPConnection(parsed.hostname, parsed.port, timeout=timeout)

    headers = {
        "Cache-Control": "no-store",
        "Content-Length": str(size),
        "Content-Type": "application/octet-stream",
        "User-Agent": USER_AGENT,
    }
    payload = make_payload_chunk(chunk_size)
    progress_printer = ProgressPrinter(progress_label, progress and progress_callback is None)
    sent = 0
    started_at = time.perf_counter()

    try:
        connection.putrequest("POST", path)
        for key, value in headers.items():
            connection.putheader(key, value)
        connection.endheaders()

        remaining = size
        while remaining:
            take = min(remaining, len(payload))
            connection.send(payload[:take])
            sent += take
            remaining -= take
            if progress_callback:
                progress_callback(take)
            else:
                progress_printer.update(sent, started_at)

        response = connection.getresponse()
        body = response.read()
    except OSError as error:
        raise RuntimeError(f"upload failed: {error}") from error
    finally:
        connection.close()

    seconds = time.perf_counter() - started_at
    server_seconds = None
    server_mbps = None
    try:
        response_json = json.loads(body.decode("utf-8"))
        server_seconds = float(response_json.get("seconds"))
        server_mbps = float(response_json.get("mbps"))
    except (ValueError, TypeError, AttributeError):
        pass

    if not progress_callback:
        progress_printer.update(sent, started_at, force=True)
    return SpeedResult(
        "upload",
        url,
        sent,
        seconds,
        int(response.status),
        str(response.reason),
        server_seconds=server_seconds,
        server_mbps=server_mbps,
    )


def download_many(
    urls: list[str],
    display_url: str,
    chunk_size: int,
    timeout: float,
    duration: Optional[float],
    insecure: bool,
    progress: bool,
) -> SpeedResult:
    streams = len(urls)
    if streams == 1:
        return download_once(
            url=urls[0],
            chunk_size=chunk_size,
            timeout=timeout,
            duration=duration,
            insecure=insecure,
            progress=progress,
        )

    aggregate_progress = AggregateProgress("download", progress)
    results: list[SpeedResult] = []
    errors: list[str] = []

    with ThreadPoolExecutor(max_workers=streams) as executor:
        futures = {
            executor.submit(
                download_once,
                url,
                chunk_size,
                timeout,
                duration,
                insecure,
                False,
                aggregate_progress.add,
                f"download[{index}]",
            ): index
            for index, url in enumerate(urls, start=1)
        }
        for future in as_completed(futures):
            stream_index = futures[future]
            try:
                results.append(future.result())
            except RuntimeError as error:
                errors.append(f"stream {stream_index}: {error}")

    aggregate_progress.finish()
    if errors:
        raise RuntimeError("; ".join(errors[:3]))

    total_bytes = sum(result.bytes_transferred for result in results)
    seconds = time.perf_counter() - aggregate_progress.started_at
    status = results[0].status
    reason = results[0].reason
    return SpeedResult("download", display_url, total_bytes, seconds, status, reason, streams=streams)


def upload_many(
    url: str,
    size: int,
    streams: int,
    chunk_size: int,
    timeout: float,
    insecure: bool,
    progress: bool,
) -> SpeedResult:
    if streams == 1:
        return upload_once(
            url=url,
            size=size,
            chunk_size=chunk_size,
            timeout=timeout,
            insecure=insecure,
            progress=progress,
        )

    aggregate_progress = AggregateProgress("upload", progress)
    results: list[SpeedResult] = []
    errors: list[str] = []
    stream_sizes = split_size(size, streams)

    with ThreadPoolExecutor(max_workers=streams) as executor:
        futures = {
            executor.submit(
                upload_once,
                url,
                stream_size,
                chunk_size,
                timeout,
                insecure,
                False,
                aggregate_progress.add,
                f"upload[{index}]",
            ): index
            for index, stream_size in enumerate(stream_sizes, start=1)
        }
        for future in as_completed(futures):
            stream_index = futures[future]
            try:
                results.append(future.result())
            except RuntimeError as error:
                errors.append(f"stream {stream_index}: {error}")

    aggregate_progress.finish()
    if errors:
        raise RuntimeError("; ".join(errors[:3]))

    total_bytes = sum(result.bytes_transferred for result in results)
    seconds = time.perf_counter() - aggregate_progress.started_at
    status = results[0].status
    reason = results[0].reason

    return SpeedResult(
        "upload",
        url,
        total_bytes,
        seconds,
        status,
        reason,
        streams=streams,
    )


def print_result(result: SpeedResult) -> None:
    status = f"HTTP {result.status}"
    if result.reason:
        status += f" {result.reason}"
    stream_text = f"  streams={result.streams}" if result.streams > 1 else ""
    print(
        f"{result.operation:8} {format_bytes(result.bytes_transferred):>11} "
        f"in {result.seconds:>7.2f}s = {result.mbps:>8.2f} Mbps  {status}{stream_text}"
    )
    if result.server_mbps is not None and result.server_seconds is not None:
        print(
            f"{'server':8} {format_bytes(result.bytes_transferred):>11} "
            f"in {result.server_seconds:>7.2f}s = {result.server_mbps:>8.2f} Mbps"
        )


def print_summary(results: Iterable[SpeedResult]) -> None:
    values = list(results)
    if len(values) <= 1:
        return
    grouped: Dict[str, list[float]] = {}
    for result in values:
        grouped.setdefault(result.operation, []).append(result.mbps)
    for operation, mbps_values in grouped.items():
        mbps_values.sort()
        average = sum(mbps_values) / len(mbps_values)
        median = mbps_values[len(mbps_values) // 2]
        print(
            f"{operation:8} average {average:.2f} Mbps, "
            f"median {median:.2f} Mbps over {len(mbps_values)} runs"
        )


class SpeedTestHandler(BaseHTTPRequestHandler):
    server_version = "HTTPSpeedTest/1.0"

    def log_message(self, format_text: str, *args: object) -> None:
        if getattr(self.server, "quiet", False):
            return
        super().log_message(format_text, *args)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path in ("", "/", "/health"):
            self.send_text(
                200,
                "HTTP speed test server is running.\n"
                "Download: /download?size=500M\n"
                "Upload:   POST /upload\n",
            )
            return

        if parsed.path != "/download":
            self.send_text(404, "not found\n")
            return

        query = parse_qs(parsed.query)
        try:
            size = parse_size(query.get("size", ["100M"])[0])
            chunk_size = parse_size(query.get("chunk", ["1M"])[0])
        except argparse.ArgumentTypeError as error:
            self.send_text(400, f"{error}\n")
            return

        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(size))
        self.send_header("Cache-Control", "no-store, no-cache, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.end_headers()

        payload = make_payload_chunk(min(chunk_size, max(size, 1)))
        remaining = size
        try:
            while remaining:
                take = min(remaining, len(payload))
                self.wfile.write(payload[:take])
                remaining -= take
        except (BrokenPipeError, ConnectionResetError):
            return

    def do_HEAD(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/download":
            query = parse_qs(parsed.query)
            try:
                size = parse_size(query.get("size", ["100M"])[0])
            except argparse.ArgumentTypeError as error:
                self.send_text(400, f"{error}\n")
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(size))
            self.send_header("Cache-Control", "no-store, no-cache, max-age=0")
            self.end_headers()
            return
        self.send_text(404, "not found\n")

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path != "/upload":
            self.send_text(404, "not found\n")
            return

        started_at = time.perf_counter()
        try:
            total = self.read_request_body()
        except ValueError as error:
            self.send_text(400, f"{error}\n")
            return

        seconds = time.perf_counter() - started_at
        result = {
            "bytes": total,
            "seconds": seconds,
            "mbps": bytes_to_mbps(total, seconds),
        }
        body = json.dumps(result, separators=(",", ":")).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def read_request_body(self) -> int:
        transfer_encoding = self.headers.get("Transfer-Encoding", "").lower()
        if "chunked" in transfer_encoding:
            return self.read_chunked_body()

        content_length = self.headers.get("Content-Length")
        if content_length is None:
            raise ValueError("upload requires Content-Length or chunked Transfer-Encoding")

        try:
            remaining = int(content_length)
        except ValueError as error:
            raise ValueError("invalid Content-Length") from error

        total = 0
        while remaining:
            data = self.rfile.read(min(remaining, DEFAULT_CHUNK_SIZE))
            if not data:
                break
            total += len(data)
            remaining -= len(data)
        return total

    def read_chunked_body(self) -> int:
        total = 0
        while True:
            line = self.rfile.readline(128)
            if not line:
                raise ValueError("unexpected end of chunked upload")
            chunk_size_text = line.split(b";", 1)[0].strip()
            try:
                chunk_size = int(chunk_size_text, 16)
            except ValueError as error:
                raise ValueError("invalid chunk size") from error
            if chunk_size == 0:
                while True:
                    trailer = self.rfile.readline(8192)
                    if trailer in (b"\r\n", b"\n", b""):
                        return total

            remaining = chunk_size
            while remaining:
                data = self.rfile.read(min(remaining, DEFAULT_CHUNK_SIZE))
                if not data:
                    raise ValueError("unexpected end of chunked upload")
                total += len(data)
                remaining -= len(data)
            self.rfile.read(2)

    def send_text(self, status: int, body: str) -> None:
        encoded = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(encoded)


def run_server(args: argparse.Namespace) -> int:
    server = ThreadingHTTPServer((args.host, args.port), SpeedTestHandler)
    server.quiet = args.quiet  # type: ignore[attr-defined]
    host_for_display = args.host if args.host not in ("", "0.0.0.0") else "127.0.0.1"
    print(f"Serving HTTP speed test endpoints on http://{host_for_display}:{args.port}")
    print(f"Download endpoint: http://{host_for_display}:{args.port}/download?size=500M")
    print(f"Upload endpoint:   http://{host_for_display}:{args.port}/upload")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServer stopped.")
    finally:
        server.server_close()
    return 0


def run_download(args: argparse.Namespace) -> int:
    results: list[SpeedResult] = []
    for run_number in range(1, args.runs + 1):
        urls = []
        for _ in range(args.streams):
            url = args.url
            if args.cache_bust:
                url = append_query(url, {"_cb": uuid.uuid4().hex})
            urls.append(url)
        print(f"Run {run_number}/{args.runs}: download {args.url} with {args.streams} stream(s)")
        result = download_many(
            urls=urls,
            display_url=args.url,
            chunk_size=args.chunk_size,
            timeout=args.timeout,
            duration=args.duration,
            insecure=args.insecure,
            progress=args.progress,
        )
        print_result(result)
        results.append(result)
    print_summary(results)
    return 0


def run_upload(args: argparse.Namespace) -> int:
    results: list[SpeedResult] = []
    for run_number in range(1, args.runs + 1):
        print(
            f"Run {run_number}/{args.runs}: upload {format_bytes(args.size)} "
            f"to {args.url} with {args.streams} stream(s)"
        )
        result = upload_many(
            url=args.url,
            size=args.size,
            streams=args.streams,
            chunk_size=args.chunk_size,
            timeout=args.timeout,
            insecure=args.insecure,
            progress=args.progress,
        )
        print_result(result)
        results.append(result)
    print_summary(results)
    return 0


def run_both(args: argparse.Namespace) -> int:
    base_url = ensure_base_url(args.base_url)
    download_display_url = args.download_url or f"{base_url}/download"
    upload_url = args.upload_url or f"{base_url}/upload"

    results: list[SpeedResult] = []
    for run_number in range(1, args.runs + 1):
        if args.download_url:
            download_urls = [args.download_url for _ in range(args.streams)]
        else:
            if args.duration:
                stream_sizes = [args.download_size_text for _ in range(args.streams)]
            else:
                stream_sizes = [str(size) for size in split_size(args.download_size, args.streams)]
            download_urls = [
                append_query(
                    f"{base_url}/download",
                    {
                        "size": stream_size,
                        "chunk": str(args.chunk_size),
                        "_cb": uuid.uuid4().hex,
                    },
                )
                for stream_size in stream_sizes
            ]
        print(
            f"Run {run_number}/{args.runs}: download {args.download_size_text} "
            f"from {download_display_url} with {args.streams} stream(s)"
        )
        download_result = download_many(
            urls=download_urls,
            display_url=download_display_url,
            chunk_size=args.chunk_size,
            timeout=args.timeout,
            duration=args.duration,
            insecure=args.insecure,
            progress=args.progress,
        )
        print_result(download_result)
        results.append(download_result)

        print(
            f"Run {run_number}/{args.runs}: upload {format_bytes(args.upload_size)} "
            f"to {upload_url} with {args.streams} stream(s)"
        )
        upload_result = upload_many(
            url=upload_url,
            size=args.upload_size,
            streams=args.streams,
            chunk_size=args.chunk_size,
            timeout=args.timeout,
            insecure=args.insecure,
            progress=args.progress,
        )
        print_result(upload_result)
        results.append(upload_result)

    print_summary(results)
    return 0


def add_common_client_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--chunk-size", default="1M", type=parse_size, help="read/write chunk size")
    parser.add_argument("--timeout", default=60.0, type=float, help="socket timeout in seconds")
    parser.add_argument("--runs", default=1, type=int, help="number of repeated test runs")
    parser.add_argument("--streams", default=1, type=int, help="parallel HTTP streams per test")
    parser.add_argument("--insecure", action="store_true", help="skip HTTPS certificate validation")
    parser.add_argument("--progress", action="store_true", help="print once-per-second progress to stderr")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Test HTTP/HTTPS download and upload throughput.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    server = subparsers.add_parser("server", help="run a local HTTP speed test server")
    server.add_argument("--host", default="0.0.0.0", help="interface to bind")
    server.add_argument("--port", default=8080, type=int, help="TCP port to bind")
    server.add_argument("--quiet", action="store_true", help="disable request logging")
    server.set_defaults(func=run_server)

    download = subparsers.add_parser("download", help="test a direct HTTP/HTTPS download URL")
    download.add_argument("url", help="direct URL to download")
    download.add_argument("--duration", default=None, type=float, help="stop after N seconds")
    download.add_argument("--cache-bust", action="store_true", help="append a random query parameter")
    add_common_client_args(download)
    download.set_defaults(func=run_download)

    upload = subparsers.add_parser("upload", help="POST generated bytes to an upload endpoint")
    upload.add_argument("url", help="HTTP/HTTPS endpoint that accepts POST bodies")
    upload.add_argument("--size", default="100M", type=parse_size, help="upload body size")
    add_common_client_args(upload)
    upload.set_defaults(func=run_upload)

    both = subparsers.add_parser("both", help="run download and upload against this tool's server")
    both.add_argument("base_url", nargs="?", default="http://127.0.0.1:8080", help="server base URL")
    both.add_argument("--download-url", default=None, help="override download URL")
    both.add_argument("--upload-url", default=None, help="override upload URL")
    both.add_argument("--download-size", dest="download_size_text", default="500M", help="download size")
    both.add_argument("--upload-size", default="100M", type=parse_size, help="upload body size")
    both.add_argument("--duration", default=None, type=float, help="stop download after N seconds")
    add_common_client_args(both)
    both.set_defaults(func=run_both)

    return parser


def normalize_args(args: argparse.Namespace) -> argparse.Namespace:
    if hasattr(args, "download_size_text"):
        args.download_size = parse_size(args.download_size_text)
    if getattr(args, "runs", 1) < 1:
        raise RuntimeError("--runs must be at least 1")
    if getattr(args, "streams", 1) < 1:
        raise RuntimeError("--streams must be at least 1")
    return args


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        normalize_args(args)
        return int(args.func(args))
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 130
    except RuntimeError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
