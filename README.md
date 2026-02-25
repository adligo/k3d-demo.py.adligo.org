# k3d-demo.adligo.org

A demo of K3D running Apache Hadoop HDFS (and eventually Apache Flink / Beam) on an Ubuntu VM.


# Warning

This code was mostly created by Claude, and although I have tested to see it work, I haven't done a through analysis of everything contained here.  I will be cleaning up Claudes work in the future :)  

### VERIFY / USE AT YOUR OWN RISK!  

# Prerequisites

[Windows Prerequisites](docs/WINDOWS_SETUP_NOTES.md)

## Step 1: Install Docker Desktop

K3D runs K3s inside Docker containers, so Docker must be installed first.  Note the majority of Docker users prefer Docker Desktop so we will use it!

 - [official instructions](https://docs.docker.com/desktop/)

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

Helm is the Kubernetes package manager used to deploy HDFS.

```bash
curl https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash
```

Verify:

```bash
helm version
```

## Step 5: Create the K3D Cluster

Create a single-server K3D cluster named `hdfs-demo`:

```bash
k3d cluster create hdfs-demo --servers 1 --agents 2
```

This creates:
- 1 server node (runs the Kubernetes control plane)
- 2 agent nodes (worker nodes where HDFS pods will run)

Verify the cluster is running:

```bash
kubectl cluster-info
kubectl get nodes
```

You should see 3 nodes (1 server + 2 agents) all in `Ready` status.

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

## Step 8: Access the HDFS Web UI (Optional)

Port-forward the NameNode web UI to your local machine:

```bash
kubectl port-forward hadoop-hadoop-hdfs-nn-0 9870:9870
```

Then open http://localhost:9870 in your browser to see the HDFS management UI.

## Cleanup

To tear down the entire environment:

```bash
# Delete the HDFS Helm release
helm uninstall hadoop

# Delete the K3D cluster
k3d cluster delete hdfs-demo
```

## Configuration

The file `hdfs-values.yaml` contains the Helm values used for this demo. Key settings:

| Setting | Value | Description |
|---|---|---|
| DataNode replicas | 2 | Number of HDFS DataNodes |
| NodeManager replicas | 1 | Number of YARN NodeManagers |
| Persistence | disabled | Data is ephemeral (demo only) |
| Anti-affinity | soft | Allows pods to co-locate on a single node |
| Memory limits | 512Mi | Reduced from defaults for lightweight demo |
| WebHDFS | enabled | REST API access to HDFS |

## Troubleshooting

**Pods stuck in Pending:**
```bash
kubectl describe pod <pod-name>
```
Usually caused by insufficient resources. Try reducing replicas in `hdfs-values.yaml`.

**Pods in CrashLoopBackOff:**
```bash
kubectl logs <pod-name>
```
Check logs for configuration errors.

**K3D cluster won't start:**
Make sure Docker Desktop is running: `systemctl --user start docker-desktop`

**ImagePullBackOff:**
Check your internet connection. K3D needs to pull images from Docker Hub.
