# Using Istio, Envoy and Kiali

## What is what

- **Istio** is the service-mesh *control plane*.  It configures every
  proxy in the cluster, issues mTLS certificates, and decides routing
  policy.  It does **not** carry traffic itself.
- **Envoy** is the *data plane* — the actual proxy.  Two flavours in
  this demo:
    - The **ingress gateway** pod (`istio-gateway-…`) is a standalone
      Envoy that terminates inbound connections from your laptop.
    - **Sidecar** Envoys are injected next to application pods in any
      namespace labelled `istio-injection=enabled`.  They intercept
      every inbound and outbound connection for that pod.
- **Kiali** is the dashboard that reads Istio config + Prometheus
  metrics and draws a live picture of traffic flowing through the mesh.

## Open Kiali

**Via the Istio gateway (Step 14):**
http://kiali.localhost:8081

**Via port-forward (if the gateway isn't set up yet):**

```bash
kubectl port-forward -n istio-system svc/kiali 20001:20001 &
```

Then open http://localhost:20001

No login is required — `kiali-values.yaml` sets `auth.strategy: anonymous`.

## See live traffic (the point of Kiali)

1. In the left sidebar, click **Traffic Graph**.
2. In the **Namespace** dropdown, select `default` and `istio-system`.
3. In the **Display** dropdown, turn on **Traffic Animation** and
   **Response Time**.
4. Generate some traffic — open http://grafana.localhost:8081 in another
   tab and click around, or run the upload script from Step 15.
5. Watch the graph: nodes are services, edges are live requests.
   Green = healthy, red = errors, thickness = throughput.

> **Seeing "Empty Graph"?**  The graph only shows traffic that actually
> passed through an Envoy proxy in the selected time window.  Generate
> requests, wait ~30s for Prometheus to scrape, then refresh.

## Inspect a single service

1. In the left sidebar, click **Services**.
2. Click any service (e.g. `grafana`).
3. Tabs:
   - **Overview** — health, inbound/outbound request rates.
   - **Traffic** — who is calling this service, who it calls.
   - **Inbound Metrics** — latency percentiles (p50/p95/p99), error
     rate, request volume.  Pulled straight from Prometheus.
   - **Istio Config** — the VirtualService / DestinationRule objects
     attached to this service.  Kiali flags misconfigurations in red.

## Validate your routing config

In the left sidebar, click **Istio Config**.  Every Gateway and
VirtualService from `istio-routes.yaml` is listed here.  A red icon
means Kiali found a problem (e.g. a VirtualService pointing at a
non-existent service) — click it for the exact validation error.

## Peek at Envoy directly

Every Envoy (gateway or sidecar) exposes an admin API on port 15000.
To dump the live config of the gateway pod:

```bash
GATEWAY_POD=$(kubectl get pod -n istio-system -l istio=gateway -o jsonpath='{.items[0].metadata.name}')

# List every listener Envoy has open (should include :80 and :9092)
kubectl exec -n istio-system $GATEWAY_POD -- \
  pilot-agent request GET listeners

# Full routing table (which Host header goes where)
kubectl exec -n istio-system $GATEWAY_POD -- \
  pilot-agent request GET config_dump | head -200
```

## Inject sidecars into application pods (optional)

Right now only the gateway carries traffic.  To get Kiali to draw edges
*between* your services (e.g. Grafana → Prometheus), inject sidecars:

```bash
# Tell Istio to inject a sidecar into every new pod in `default`
kubectl label namespace default istio-injection=enabled

# Restart workloads so they pick up the sidecar
kubectl rollout restart deployment grafana
kubectl rollout restart deployment prometheus-server
```

After the pods come back, every one of them will have **2/2** containers
— the second one is Envoy.  Intra-cluster calls (Grafana scraping
Prometheus) now show up in the Kiali traffic graph.

> StatefulSets (HDFS, Kafka) can be injected the same way, but the
> sidecar adds ~70 MiB per pod.  On a tight VM you may want to skip
> them.

## Further reading

- [Istio — Concepts Overview](https://istio.io/latest/docs/concepts/)
- [Istio — Traffic Management (Gateway / VirtualService)](https://istio.io/latest/docs/concepts/traffic-management/)
- [Istio — Install with Helm](https://istio.io/latest/docs/setup/install/helm/)
- [Kiali — Traffic Graph Guide](https://kiali.io/docs/features/topology/)
- [Envoy — Admin Interface Reference](https://www.envoyproxy.io/docs/envoy/latest/operations/admin)
