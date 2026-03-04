# Using Prometheus

Prometheus scrapes and stores time-series metrics from the K3D cluster
(node CPU/memory, pod restart counts, Kubernetes object state, etc.).

## Open the Web UI

**Via the Istio gateway (Step 14):**
http://prometheus.localhost:8081

**Via port-forward (if the gateway isn't set up yet):**

```bash
kubectl port-forward svc/prometheus-server 9090:80 &
```

Then open http://localhost:9090

## Run your first queries

In the Prometheus UI, click **Graph**, paste a query into the expression
bar, and click **Execute**.

```promql
# Are all the Kafka controller pods up?
kube_pod_status_phase{pod=~"kafka-controller-.*", phase="Running"}

# How many times has each pod restarted?
kube_pod_container_status_restarts_total

# CPU usage per K3D node (rate over the last 5 minutes)
sum by (instance) (rate(node_cpu_seconds_total{mode!="idle"}[5m]))

# Memory available per K3D node, in MiB
node_memory_MemAvailable_bytes / 1024 / 1024

# Istio — request rate hitting each destination service (only populates
# once Istio sidecars are injected and traffic is flowing)
sum by (destination_service) (rate(istio_requests_total[5m]))
```

> **Note:** In K3D the "nodes" are Docker containers, so node-exporter
> metrics (`node_*`) reflect the container's view of resources, not the
> bare-metal host.

## Check which targets Prometheus is scraping

Open **Status → Targets** — every target should show **UP**.  If a
target is **DOWN**, that component is not exposing metrics on the
expected port.

## Further reading

- [Prometheus — Getting Started](https://prometheus.io/docs/prometheus/latest/getting_started/)
- [Querying Basics (PromQL)](https://prometheus.io/docs/prometheus/latest/querying/basics/)
- [PromQL Cheat Sheet](https://promlabs.com/promql-cheat-sheet/)
- [kube-state-metrics — Exposed Metrics](https://github.com/kubernetes/kube-state-metrics/tree/main/docs)
- [Helm Chart Reference](https://github.com/prometheus-community/helm-charts/tree/main/charts/prometheus)
