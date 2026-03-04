#!/usr/bin/env python3
"""Upload math-images/ to HDFS and send batch signals to Kafka.

All HDFS traffic goes through the Istio/Envoy ingress gateway — no
kubectl port-forwards required.  The two Kafka signal messages still
use ``kubectl exec`` (see the note above ``send_kafka_message``).

No pip dependencies — only the Python standard library.

Prerequisites
-------------
The Istio gateway must be up and the routes applied — i.e. you've
completed README Steps 12–14:

    helm install istio-gateway ...         (Step 12)
    kubectl apply -f istio-routes.yaml     (Step 14)

Quick check before running this script:

    curl -sI http://hdfs.localhost:8081 | head -1
    # Should print: HTTP/1.1 200 OK
"""

import glob
import json
import os
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

# Istio gateway entry points (host:8081 → Envoy → in-cluster Service).
# These hostnames are defined in istio-routes.yaml and the port mapping
# comes from `-p "8081:80@loadbalancer"` in `k3d cluster create` (README
# Step 5).
GATEWAY_NAMENODE = "http://hdfs.localhost:8081"
GATEWAY_DATANODE = "http://hdfs-dn.localhost:8081"
NAMENODE_WEBHDFS = f"{GATEWAY_NAMENODE}/webhdfs/v1"

# HDFS user for WebHDFS requests.  The NameNode process runs as root in the
# farberg/apache-hadoop container, so root is the HDFS superuser.  Without
# this parameter WebHDFS defaults to the unprivileged "dr.who" user which
# cannot write to HDFS.
HDFS_USER = "root"

print_lock = threading.Lock()

GATEWAY_HINT = (
    "\n  Make sure the Istio gateway is up and the routes are applied\n"
    "  (README Steps 12-14):\n"
    "    kubectl get pods -n istio-system          # gateway pod Running?\n"
    "    kubectl get gateway,virtualservice -A     # routes applied?\n"
    "    curl -sI http://hdfs.localhost:8081 | head -1"
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
# WebHDFS helpers — all traffic goes through the Envoy gateway
# ---------------------------------------------------------------------------


def _rewrite_to_gateway_datanode(location):
    """Rewrite a WebHDFS DataNode redirect URL to the gateway hostname.

    The NameNode's redirect points at an in-cluster DataNode address
    (something like ``example.com:51000`` or a pod DNS name) that the
    host machine can't reach.  We swap the scheme://host:port for the
    gateway entry point and keep the path + query string intact — that's
    where the block token lives.
    """
    parsed = urlparse(location)
    gw = urlparse(GATEWAY_DATANODE)
    return urlunparse(parsed._replace(scheme=gw.scheme, netloc=gw.netloc))


def check_gateway():
    """Verify both gateway routes (NameNode + DataNode) answer.

    A 404 from the DataNode is *fine* — the WebHDFS handler only
    accepts requests that carry a valid block token.  We just want to
    confirm Envoy forwards the connection rather than refusing it.
    """
    for label, url in [
        ("NameNode", f"{NAMENODE_WEBHDFS}/?user.name={HDFS_USER}&op=LISTSTATUS"),
        ("DataNode", f"{GATEWAY_DATANODE}/"),
    ]:
        try:
            with urlopen(Request(url), timeout=5) as resp:
                resp.read()
        except HTTPError:
            # Any HTTP status means Envoy reached a backend — good enough.
            pass
        except (URLError, OSError) as exc:
            print(f"  ERROR: Gateway route '{label}' unreachable — {exc.reason}")
            print(GATEWAY_HINT)
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

    1. PUT to the NameNode (via gateway) with ``noredirect=true`` — it
       replies with a DataNode URL in the JSON body instead of a 307.
    2. Rewrite that URL's host to the DataNode gateway hostname and
       PUT the file bytes there.
    """
    filename = os.path.basename(local_path)

    # Step 1 — ask the NameNode (through Envoy) which DataNode to write to.
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
    except (URLError, OSError):
        raise RuntimeError(
            f"Cannot reach NameNode via gateway for {filename} — "
            "is the Istio gateway running?"
        ) from None

    redirect_url = body.get("Location")
    if not redirect_url:
        raise RuntimeError(
            f"NameNode did not return a DataNode URL for {filename}. "
            f"Response: {body}"
        )

    # Step 2 — rewrite the in-cluster DataNode URL so it goes through
    # Envoy, then PUT the file bytes.
    dn_url = _rewrite_to_gateway_datanode(redirect_url)
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
    except (URLError, OSError):
        raise RuntimeError(
            f"Cannot reach DataNode via gateway for {filename} — "
            "is the hdfs-dn route applied? (kubectl apply -f istio-routes.yaml)"
        ) from None


# ---------------------------------------------------------------------------
# Kafka helper — still uses kubectl exec
# ---------------------------------------------------------------------------
#
# Why isn't this going through the gateway too?
#
# The gateway *does* expose Kafka on localhost:9094 (TCP passthrough),
# but that only gets you as far as the bootstrap handshake.  After
# bootstrap, the broker sends back metadata listing all three broker
# addresses — and those are internal pod DNS names
# (kafka-controller-{0,1,2}.kafka-controller-headless...).  An external
# client tries to connect to those names directly and fails.
#
# Fixing it properly means either:
#   (a) reconfiguring Kafka's `advertised.listeners` to point at the
#       gateway, AND exposing one gateway port per broker, or
#   (b) running a Kafka REST Proxy inside the cluster and hitting that
#       over HTTP.
# Both add significant moving parts for what is, in this script, exactly
# two messages.  Running the producer inside a broker pod sidesteps the
# whole problem.
#
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

    # 0. Pre-flight: make sure the gateway routes answer
    print("\nChecking Istio gateway -> HDFS connectivity...")
    check_gateway()
    print("  OK - NameNode and DataNode reachable via gateway.")

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

    # 2. Create HDFS directory via WebHDFS (through the gateway)
    print(f"\nCreating HDFS directory {HDFS_DIR}...")
    hdfs_mkdir(HDFS_DIR)
    print("  Done.")

    # 3. Upload files concurrently via WebHDFS REST API (through the gateway)
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
