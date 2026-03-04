# Teardown

Or use the teardown script to remove all k3d clusters and orphaned containers at once:

```bash
bash bin/teardown.sh
```

This file contains instructions on how to tear down/destroy all of the Docker containers.


```bash
# Delete the Istio Gateway + VirtualService routes
kubectl delete -f istio-routes.yaml --ignore-not-found

# Delete the Kiali Helm release
helm uninstall kiali -n istio-system

# Delete Istio (reverse install order: gateway → control plane → CRDs)
helm uninstall istio-gateway -n istio-system
helm uninstall istiod -n istio-system
helm uninstall istio-base -n istio-system
kubectl delete namespace istio-system --ignore-not-found

# Delete the Grafana Helm release
helm uninstall grafana

# Delete the Prometheus Helm release
helm uninstall prometheus

# Delete the Kafka Helm release
helm uninstall kafka

# Delete the HDFS Helm release
helm uninstall hadoop

# Stop the K3D cluster
k3d cluster stop demo

# Delete the K3D cluster
k3d cluster delete demo
```

