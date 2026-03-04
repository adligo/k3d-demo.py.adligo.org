# Using Grafana

Grafana turns raw Prometheus metrics into dashboards and graphs.  The
`grafana-values.yaml` in this repo pre-wires Prometheus as the default
datasource, so Grafana is useful the moment it starts.

## Open the Web UI

**Via the Istio gateway (Step 14):**
http://grafana.localhost:8081

**Via port-forward (if the gateway isn't set up yet):**

```bash
kubectl port-forward svc/grafana 3000:80 &
```

Then open http://localhost:3000

## Log in

- **Username:** `admin`
- **Password:** `admin`

> The credentials are hard-coded in `grafana-values.yaml` for demo
> convenience.  Never do this outside a local sandbox.

## Verify the Prometheus datasource

1. In the left sidebar, go to **Connections → Data sources**.
2. You should see **Prometheus** already listed (it was provisioned by
   `grafana-values.yaml`).
3. Click it, scroll to the bottom, and click **Save & test**.  It should
   say *"Successfully queried the Prometheus API."*

## Run ad-hoc queries (Explore view)

The fastest way to poke at metrics is the **Explore** view — no dashboard
needed.

1. In the left sidebar, click **Explore** (the compass icon).
2. Make sure **Prometheus** is selected in the datasource dropdown
   (top left).
3. In the query editor, switch from **Builder** to **Code** mode.
4. Paste a PromQL expression and click **Run query** (top right):

```promql
sum by (pod) (kube_pod_container_status_restarts_total)
```

You'll see a live time-series graph of container restarts per pod.

## Build your first dashboard panel

1. In the left sidebar, click **Dashboards → New → New dashboard**.
2. Click **+ Add visualization** and pick **Prometheus** as the
   datasource.
3. In the query editor (**Code** mode), enter:

   ```promql
   sum by (instance) (rate(node_cpu_seconds_total{mode!="idle"}[5m]))
   ```

4. On the right-hand **Panel options** pane, set **Title** to
   `K3D Node CPU`.
5. Click **Apply** (top right) to add the panel, then the **Save**
   (disk) icon to save the dashboard.

## Customize a query

Every panel is just a PromQL query — edit it to change what's plotted.

1. Hover over any panel, click the **⋮** (three-dot) menu, and choose
   **Edit**.
2. In the query editor you can:
   - **Filter by label** — add a `{...}` selector.  To show only the
     HDFS pods:
     ```promql
     kube_pod_container_status_restarts_total{pod=~"hadoop-.*"}
     ```
   - **Change the time window** — edit the `[5m]` range.  `[1m]` is
     spikier, `[1h]` is smoother.
   - **Change the aggregation** — swap `sum by (instance)` for
     `avg by (instance)` or `max by (instance)`.
   - **Add a second query** — click **+ Query** to overlay another line
     on the same graph (e.g. plot Kafka and HDFS restarts together).
3. Use the **Legend** field under the query to give each line a readable
   name: `{{pod}}` renders the pod label, `{{instance}}` renders the
   node.
4. Click **Apply** when you're done.

> **Tip:** Don't know the metric name?  Start typing in **Code** mode —
> Grafana autocompletes metric names and label values straight from
> Prometheus.

## Further reading

- [Grafana — Getting Started with Prometheus](https://grafana.com/docs/grafana/latest/getting-started/get-started-grafana-prometheus/)
- [Explore View Guide](https://grafana.com/docs/grafana/latest/explore/)
- [Build Your First Dashboard](https://grafana.com/docs/grafana/latest/getting-started/build-first-dashboard/)
- [Prometheus Query Editor Reference](https://grafana.com/docs/grafana/latest/datasources/prometheus/query-editor/)
- [Helm Chart Reference](https://github.com/grafana-community/helm-charts/tree/main/charts/grafana)
