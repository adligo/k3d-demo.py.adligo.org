#!/usr/bin/env python3
"""Upload math-images/ to HDFS and send batch signals to Kafka.

Uses the WebHDFS REST API for file uploads (concurrent HTTP PUTs) and
kubectl exec only for the two Kafka messages.  No pip dependencies —
only the Python standard library.

Prerequisites — run these port-forwards before starting the script:

    kubectl port-forward hadoop-hadoop-hdfs-nn-0 9870:9870 &
    kubectl port-forward hadoop-hadoop-hdfs-dn-0 51000:51000 &
"""

import glob
import json
import os
import socket
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse, urlunparse
from urllib.request import Request, urlopen

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
KAFKA_POD = "kafka-controller-0"
KAFKA_TOPIC = "batchSignals"
BOOTSTRAP_SERVER = "kafka:9092"
LOCAL_DIR = "math-images"
HDFS_DIR = "/math-images"
MAX_WORKERS = 4

# WebHDFS endpoints — assumes port-forwards are already running (see README).
NAMENODE_WEBHDFS = "http://localhost:9870/webhdfs/v1"
DATANODE_HOST = "localhost"
DATANODE_PORT = 51000

# HDFS user for WebHDFS requests.  The NameNode process runs as root in the
# farberg/apache-hadoop container, so root is the HDFS superuser.  Without
# this parameter WebHDFS defaults to the unprivileged "dr.who" user which
# cannot write to HDFS.
HDFS_USER = "root"

print_lock = threading.Lock()

PORT_FORWARD_HINT = (
    "\n  Make sure the port-forwards are running (see README Step 8):\n"
    "    kubectl port-forward hadoop-hadoop-hdfs-nn-0 9870:9870 &\n"
    "    kubectl port-forward hadoop-hadoop-hdfs-dn-0 51000:51000 &"
)

# ---------------------------------------------------------------------------
# Friendly error formatting
# ---------------------------------------------------------------------------


def _friendly_http_error(exc, context):
    """Turn an HTTPError / URLError into a short, readable string."""
    if isinstance(exc, HTTPError):
        try:
            body = exc.read().decode(errors="replace").strip()
            data = json.loads(body)
            msg = data.get("RemoteException", {}).get("message", body)
        except (json.JSONDecodeError, ValueError):
            msg = body or "(no details from server)"
        return f"{context}: HTTP {exc.code} — {msg}"
    if isinstance(exc, URLError):
        return f"{context}: {exc.reason}"
    return f"{context}: {exc}"


# ---------------------------------------------------------------------------
# WebHDFS helpers
# ---------------------------------------------------------------------------


def _rewrite_to_local_datanode(location):
    """Rewrite any WebHDFS DataNode redirect URL to the local port-forward.

    Inside k3d the DataNodes report their hostname as ``example.com``
    (an unreachable placeholder).  We unconditionally replace the
    host:port with the local port-forward target.
    """
    parsed = urlparse(location)
    return urlunparse(parsed._replace(
        netloc=f"{DATANODE_HOST}:{DATANODE_PORT}"
    ))


def check_webhdfs():
    """Verify the WebHDFS NameNode is reachable."""
    url = f"{NAMENODE_WEBHDFS}/?user.name={HDFS_USER}&op=LISTSTATUS"
    try:
        with urlopen(Request(url), timeout=5) as resp:
            json.loads(resp.read())
    except (HTTPError, URLError, OSError, ValueError) as exc:
        print(f"  ERROR: {_friendly_http_error(exc, 'NameNode health-check')}")
        print(PORT_FORWARD_HINT)
        sys.exit(1)


def check_datanode():
    """Verify the DataNode port-forward is reachable."""
    try:
        with socket.create_connection(
            (DATANODE_HOST, DATANODE_PORT), timeout=3
        ):
            pass
    except (ConnectionRefusedError, OSError):
        print(
            f"  ERROR: Cannot connect to DataNode at "
            f"{DATANODE_HOST}:{DATANODE_PORT}."
        )
        print(PORT_FORWARD_HINT)
        sys.exit(1)


def hdfs_mkdir(path):
    """Create a directory in HDFS via WebHDFS MKDIRS."""
    url = f"{NAMENODE_WEBHDFS}{path}?user.name={HDFS_USER}&op=MKDIRS"
    try:
        with urlopen(Request(url, method="PUT"), timeout=30) as resp:
            body = json.loads(resp.read())
    except (HTTPError, URLError, OSError) as exc:
        print(
            f"  ERROR: {_friendly_http_error(exc, f'Creating HDFS directory {path}')}"
        )
        sys.exit(1)

    if not body.get("boolean"):
        print(f"  ERROR: HDFS refused to create directory {path}.")
        print(f"  Server response: {body}")
        sys.exit(1)


def hdfs_upload(local_path, hdfs_path):
    """Upload a local file to HDFS via WebHDFS (two-step CREATE).

    1. PUT with ``noredirect=true`` to get the DataNode URL in JSON
    2. Rewrite the DataNode URL to localhost and PUT the file bytes
    """
    filename = os.path.basename(local_path)

    # Step 1 — ask the NameNode which DataNode to write to.
    # ``noredirect=true`` returns the DataNode URL as JSON instead of a
    # 307 redirect, which is easier to handle from Python.
    create_url = (
        f"{NAMENODE_WEBHDFS}{hdfs_path}"
        f"?user.name={HDFS_USER}&op=CREATE&overwrite=true&noredirect=true"
    )
    try:
        with urlopen(Request(create_url, method="PUT"), timeout=30) as resp:
            body = json.loads(resp.read())
    except HTTPError as exc:
        raise RuntimeError(
            _friendly_http_error(exc, f"NameNode CREATE for {filename}")
        ) from None
    except (URLError, OSError) as exc:
        raise RuntimeError(
            f"Cannot reach NameNode for {filename} — "
            "is the NameNode port-forward running?"
        ) from None

    redirect_url = body.get("Location")
    if not redirect_url:
        raise RuntimeError(
            f"NameNode did not return a DataNode URL for {filename}. "
            f"Response: {body}"
        )

    # Step 2 — PUT file bytes to the rewritten DataNode URL.
    dn_url = _rewrite_to_local_datanode(redirect_url)
    with open(local_path, "rb") as fh:
        data = fh.read()
    req = Request(dn_url, data=data, method="PUT")
    req.add_header("Content-Type", "application/octet-stream")

    try:
        with urlopen(req, timeout=60) as resp:
            if resp.status != 201:
                raise RuntimeError(
                    f"DataNode accepted {filename} but returned HTTP "
                    f"{resp.status} instead of 201 Created."
                )
    except HTTPError as exc:
        raise RuntimeError(
            _friendly_http_error(exc, f"DataNode write for {filename}")
        ) from None
    except (URLError, OSError) as exc:
        raise RuntimeError(
            f"Cannot reach DataNode at {DATANODE_HOST}:{DATANODE_PORT} "
            f"for {filename} — is the DataNode port-forward running?"
        ) from None


# ---------------------------------------------------------------------------
# Kafka helper (still uses kubectl exec — only two sequential calls)
# ---------------------------------------------------------------------------
def send_kafka_message(message_dict):
    """
      TODO move to a python API, Claude did this
      Send a JSON message to the batchSignals Kafka topic.
    """
    print("TODO move the code in send_kafka_message to a python API, Claude did this in bash")
    msg = json.dumps(message_dict)
    result = subprocess.run(
        [
            "kubectl", "exec", KAFKA_POD, "--",
            "bash", "-c",
            f"echo '{msg}' | kafka-console-producer.sh "
            f"--bootstrap-server {BOOTSTRAP_SERVER} --topic {KAFKA_TOPIC}",
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        stderr = result.stderr.strip()
        print(f"  ERROR: Failed to send Kafka message to topic '{KAFKA_TOPIC}'.")
        if stderr:
            print(f"  kubectl stderr: {stderr}")
        print(f"  Make sure pod '{KAFKA_POD}' is running:")
        print(f"    kubectl get pod {KAFKA_POD}")
        sys.exit(1)


# ---------------------------------------------------------------------------
# Per-file upload (called inside the thread pool)
# ---------------------------------------------------------------------------
def _upload_one(index, total, local_path):
    """Upload one file; returns None on success, error string on failure."""
    filename = os.path.basename(local_path)
    try:
        hdfs_upload(local_path, f"{HDFS_DIR}/{filename}")
        with print_lock:
            print(f"  [{index}/{total}] {filename} - done")
        return None
    except Exception as e:
        with print_lock:
            print(f"  [{index}/{total}] {filename} - FAILED: {e}")
        return f"{filename}: {e}"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    files = sorted(glob.glob(os.path.join(LOCAL_DIR, "*.png")))
    if not files:
        print(f"No .png files found in {LOCAL_DIR}/")
        sys.exit(1)

    file_count = len(files)
    print(f"Found {file_count} images in {LOCAL_DIR}/")

    # 0. Pre-flight: make sure WebHDFS is reachable
    print("\nChecking WebHDFS connectivity...")
    check_webhdfs()
    check_datanode()
    print("  OK — NameNode and DataNode are reachable.")

    # 1. Kafka STARTING
    start_msg = {
        "event": "BATCH_UPLOAD_STARTING",
        "directory": LOCAL_DIR,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "fileCount": file_count,
    }
    print(f"\nSending {start_msg['event']} to Kafka topic '{KAFKA_TOPIC}'...")
    send_kafka_message(start_msg)
    print("  Sent.")

    # 2. Create HDFS directory via WebHDFS
    print(f"\nCreating HDFS directory {HDFS_DIR}...")
    hdfs_mkdir(HDFS_DIR)
    print("  Done.")

    # 3. Upload files concurrently via WebHDFS REST API
    print(f"\nUploading {file_count} files to HDFS ({MAX_WORKERS} workers)...")
    errors = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {
            pool.submit(_upload_one, i, file_count, path): path
            for i, path in enumerate(files, 1)
        }
        for future in as_completed(futures):
            err = future.result()
            if err:
                errors.append(err)

    if errors:
        print(f"\n{len(errors)} file(s) failed:")
        for e in errors:
            print(f"  {e}")
        sys.exit(1)

    print("  All files uploaded.")

    # 4. Kafka COMPLETE
    complete_msg = {
        "event": "BATCH_UPLOAD_COMPLETE",
        "directory": LOCAL_DIR,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "filesUploaded": file_count,
    }
    print(f"\nSending {complete_msg['event']} to Kafka topic '{KAFKA_TOPIC}'...")
    send_kafka_message(complete_msg)
    print("  Sent.")

    # 5. Verification hints
    print("\n--- Done! ---")
    print("Verify files in HDFS:")
    print("  kubectl exec -it hadoop-hadoop-hdfs-nn-0 -- hdfs dfs -ls "
          f"{HDFS_DIR}")
    print("Verify Kafka messages:")
    print(f"  kubectl exec -it {KAFKA_POD} -- kafka-console-consumer.sh "
          f"--bootstrap-server {BOOTSTRAP_SERVER} --topic {KAFKA_TOPIC} "
          f"--from-beginning")


if __name__ == "__main__":
    main()
