# k3d-demo.adligo.org

A demo of K3D running Apache Hadoop HDFS and Apache Kafka (and eventually Apache Flink / Beam) on an Ubuntu VM.


# Warning

This code was mostly created by Claude, and although I have tested to see it work, I haven't done a through analysis of everything contained here.  I will be cleaning up Claudes work in the future :)  

### VERIFY / USE AT YOUR OWN RISK!  

# Prerequisites / Setup Notes

- [Ubuntu on VirtualBox Setup Notes](docs/UBUNTU_VBOX_SETUP_NOTES.md)
- [Windows Setup Notes](docs/WINDOWS_SETUP_NOTES.md)

## Step 1: Install Docker Desktop

K3D runs K3s inside Docker containers, so Docker must be installed first.  Note the majority of Docker users prefer Docker Desktop so we will use it!

 - [official instructions](https://docs.docker.com/desktop/)

Note:  Docker Desktop does NOT work well when installed on Virtual Box in many cases.  Virtual Box users will likely want to use Docker Station instead, refer to the following README;

- [Ubuntu on VirtualBox Setup Notes](docs/UBUNTU_VBOX_SETUP_NOTES.md)

After you complete the installation you should see a GUI window like this;

![Docker Desktop GUI](docs/top-images/dockerDesktop.png)


### 1d: Verify Docker is working

```bash
docker run --rm hello-world
```

## Step 2: Install kubectl

kubectl is the Kubernetes command-line tool, follow the official instructions.

-[Install kubectl](https://kubernetes.io/docs/tasks/tools/)

Verify:

```bash
kubectl version --client
```

## Step 3: Install K3D

K3D is a lightweight wrapper that runs K3s (a minimal Kubernetes distribution) inside Docker.

- [Official K3D installation instructions](https://k3d.io/stable/#releases)



Verify:

```bash
k3d version
```

## Step 4: Install Helm

Helm is the Kubernetes package manager used to deploy HDFS and Kafka.

- [Official Helm Installation Instructions](https://helm.sh/docs/intro/install/)

Verify:

```bash
helm version
```

## Step 5: Create the K3D Cluster

If you have any existing k3d clusters or orphaned containers, tear them down first:

- [TEARDOWN_NOTES.md](docs/TEARDOWN_NOTES.md)

Create a single-server K3D cluster named `demo`:

```bash
k3d cluster create demo --servers 1 --agents 4
```

This creates:
- 1 server node (runs the Kubernetes control plane)
- 4 agent nodes (worker nodes where HDFS and Kafka pods will run)

Verify the cluster is running:

```bash
kubectl cluster-info
kubectl get nodes
```

You should see 5 nodes (1 server + 4 agents) all in `Ready` status.

## Step 6: Deploy HDFS with Helm

Add the Hadoop Helm chart repository and install HDFS using the provided
lightweight values file:

```bash
# Add the Helm repo
helm repo add pfisterer-hadoop https://pfisterer.github.io/apache-hadoop-helm/
helm repo update

# Install HDFS using the demo values file (from the root of this repo)
helm install hadoop pfisterer-hadoop/hadoop -f hdfs-values.yaml
```

Wait for all pods to become ready (this may take a few minutes as images are pulled):

```bash
# This command updates after every 5-7m 
kubectl get pods -w
```

You should eventually see pods like:
- `hadoop-hadoop-hdfs-nn-0` (NameNode) — Running
- `hadoop-hadoop-hdfs-dn-0` (DataNode) — Running
- `hadoop-hadoop-hdfs-dn-1` (DataNode) — Running
- `hadoop-hadoop-yarn-rm-0` (YARN ResourceManager) — Running
- `hadoop-hadoop-yarn-nm-0` (YARN NodeManager) — Running

Press `Ctrl+C` to stop watching once all pods show `Running`.

## Step 7: Verify HDFS is Working

Open a shell inside the NameNode pod and run HDFS commands:

```bash
# Get a shell on the NameNode
kubectl exec -it hadoop-hadoop-hdfs-nn-0 -- /bin/bash

# Inside the pod, run these HDFS commands:
hdfs dfs -ls /
hdfs dfs -mkdir /test
hdfs dfs -ls /
echo "Hello HDFS on K3D!" > /tmp/hello.txt
hdfs dfs -put /tmp/hello.txt /test/
hdfs dfs -cat /test/hello.txt

# Exit the pod
exit
```

## Step 8: Enable the HDFS Web Interface

The NameNode exposes both a management Web UI and the **WebHDFS REST API** on
port 9870.  The upload script in Step 10 uses the REST API to write files into
HDFS, so this step is **required**.

Port-forward the NameNode **and** one DataNode to your local machine.  Run each
command in its own terminal, or background them with `&`:

```bash
# NameNode — Web UI + WebHDFS REST API (port 9870)
kubectl port-forward hadoop-hadoop-hdfs-nn-0 9870:9870 &

# DataNode — WebHDFS file-write endpoint (port 51000)
# The NameNode redirects file writes to a DataNode; this port-forward
# lets the upload script reach it from localhost.
# NOTE: The Helm chart configures DataNode HTTP on port 51000, not the
# Hadoop default of 9864.
kubectl port-forward hadoop-hadoop-hdfs-dn-0 51000:51000 &
# The Ampersands at the end of these lines & enable the 
# port forwarding to continue even after the shell is closed!
```

Verify the port-forwards are working:

```bash
# Should print JSON (not "Connection refused")
curl -s "http://localhost:9870/webhdfs/v1/?user.name=root&op=LISTSTATUS"
```

> **Note:** WebHDFS requests must include `user.name=root` because the HDFS
> NameNode runs as `root` inside the container.  Without this parameter,
> WebHDFS defaults to the unprivileged `dr.who` user and returns
> **403 Forbidden** on any write operation.  The upload script handles this
> automatically.

You can now:
- Browse the HDFS management UI at http://localhost:9870
- The upload script (Step 10) will use the WebHDFS REST API on these ports

## Step 9: Deploy Kafka with Helm

Add the Bitnami Helm chart repository and install Kafka using the provided
values file:

```bash
# Add the Bitnami Helm repo
helm repo add bitnami https://charts.bitnami.com/bitnami
helm repo update

# Install Kafka using the demo values file (from the root of this repo)
helm install kafka bitnami/kafka -f kafka-values.yaml
```

Wait for all pods to become ready (this may take a few minutes as images are pulled):

```bash
kubectl get pods -w
```

You should eventually see pods like:
- `kafka-controller-0` — Running
- `kafka-controller-1` — Running
- `kafka-controller-2` — Running

Press `Ctrl+C` to stop watching once all three controller pods show `Running`.

### Create the demo topics

Once all controller pods are running, create the three topics manually:

```bash
kubectl exec -it kafka-controller-0 -- kafka-topics.sh \
  --bootstrap-server kafka:9092 --create --topic ocrImages \
  --partitions 3 --replication-factor 3 \
  --config retention.ms=604800000

kubectl exec -it kafka-controller-0 -- kafka-topics.sh \
  --bootstrap-server kafka:9092 --create --topic batchSignals \
  --partitions 3 --replication-factor 3 \
  --config retention.ms=604800000

kubectl exec -it kafka-controller-0 -- kafka-topics.sh \
  --bootstrap-server kafka:9092 --create --topic generalEvents \
  --partitions 3 --replication-factor 3 \
  --config retention.ms=604800000
```

### Verify topics

```bash
kubectl exec -it kafka-controller-0 -- kafka-topics.sh \
  --bootstrap-server kafka:9092 --list
```

You should see `ocrImages`, `batchSignals`, and `generalEvents`.

To inspect a specific topic:

```bash
kubectl exec -it kafka-controller-0 -- kafka-topics.sh \
  --bootstrap-server kafka:9092 --describe --topic ocrImages
```

### Optional: produce and consume a test message

```bash
# In one terminal — start a consumer
kubectl exec -it kafka-controller-0 -- kafka-console-consumer.sh \
  --bootstrap-server kafka:9092 --topic generalEvents --from-beginning

# In another terminal — send a message
kubectl exec -it kafka-controller-0 -- bash -c \
  'echo "hello kafka" | kafka-console-producer.sh --bootstrap-server kafka:9092 --topic generalEvents'
```

## Step 10: Run the Batch Upload Script

This Python script uploads the `math-images/` directory to HDFS and sends
start/complete signals to the `batchSignals` Kafka topic. It uses the
**WebHDFS REST API** for file uploads (concurrent) and `kubectl exec` only for
the two Kafka messages.  No pip dependencies — only the Python standard library.

### Prerequisites

Make sure the WebHDFS port-forwards from **Step 8** are running:

```bash
# Quick check — should print JSON (not "Connection refused")
curl -s http://localhost:9870/webhdfs/v1/?op=LISTSTATUS | head -c 80
```

If not, re-run the two `kubectl port-forward` commands from Step 8.

### Watch Kafka messages (optional — run in a separate terminal)

```bash
kubectl exec -it kafka-controller-0 -- kafka-console-consumer.sh \
  --bootstrap-server kafka:9092 --topic batchSignals --from-beginning
```

### Run the upload

```bash
cd src 
python3 upload_math_images.py
```

The script will:
1. Send a `BATCH_UPLOAD_STARTING` JSON message to the `batchSignals` topic
2. Upload each `.png` from `math-images/` into HDFS at `/math-images/` via the
   WebHDFS REST API (4 concurrent workers)
3. Send a `BATCH_UPLOAD_COMPLETE` JSON message to the `batchSignals` topic

### Verify files in HDFS

```bash
kubectl exec -it hadoop-hadoop-hdfs-nn-0 -- hdfs dfs -ls /math-images
# optionally delete the folder with this command and iterate on the
# upload_math_images.py Python program 
kubectl exec -it hadoop-hadoop-hdfs-nn-0 -- hdfs dfs -rm -r /math-images

```

## Cleanup

- [TEARDOWN_NOTES.md](docs/TEARDOWN_NOTES.md)

## Configuration

- [CONFIG_DETAILS.md](docs/CONFIG_DETAILS.md)

## Troubleshooting

- [TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md)
