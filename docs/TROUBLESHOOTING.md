# Troubleshooting

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

**ImagePullBackOff / ErrImagePull:**
First, find which image is failing:
```bash
kubectl describe pod <pod-name> | grep -A 10 'Events'
```
Common causes:
- **No internet** — K3D needs to pull images from Docker Hub.
- **Image removed from registry** — Bitnami periodically removes older images. Check the Events output for the exact image tag, then search [Docker Hub](https://hub.docker.com/r/bitnami/kafka/tags) for available tags. You may need to override the image repository in your values file (see the Kafka image fix below).

**Kafka image not found (`bitnami/kafka` tag missing):**
Bitnami moved Kafka images from `docker.io/bitnami` to `docker.io/bitnamilegacy`. The `kafka-values.yaml` in this repo already overrides this, but if you see `ErrImagePull` for a `bitnami/kafka` tag, make sure your values file contains:
```yaml
image:
  repository: bitnamilegacy/kafka
```
Then reinstall: `helm uninstall kafka && helm install kafka bitnami/kafka -f kafka-values.yaml`
See [bitnami/charts#36325](https://github.com/bitnami/charts/issues/36325) for details.

**Kafka pods OOMKilled:**
The micro resource preset may not be enough on memory-constrained VMs. Edit `kafka-values.yaml` and switch to explicit resource limits:
```yaml
controller:
  resourcePreset: ""
  resources:
    requests:
      memory: "512Mi"
      cpu: "250m"
    limits:
      memory: "1Gi"
      cpu: "500m"
```
Then run `helm upgrade kafka bitnami/kafka -f kafka-values.yaml`.

**Kafka topic creation fails with "not enough replicas":**
All 3 controller pods must be Running before creating topics with replication factor 3. Check with:
```bash
kubectl get pods -l app.kubernetes.io/instance=kafka
```
Wait until all show `Running`, then retry the `kafka-topics.sh --create` commands.