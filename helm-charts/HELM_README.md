# Helm Charts

I have noticed that helm charts disappear from time to time from the web, so this directory was created with the following commands;

```
helm repo add pfisterer-hadoop https://pfisterer.github.io/apache-hadoop-helm/
helm repo add bitnami https://charts.bitnami.com/bitnami
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm repo add grafana-community https://grafana-community.github.io/helm-charts
helm repo add istio https://istio-release.storage.googleapis.com/charts
helm repo add kiali https://kiali.org/helm-charts
helm repo update

helm pull pfisterer-hadoop/hadoop --untar --untardir .
helm pull bitnami/kafka  --untar --untardir .
helm pull prometheus-community/prometheus --untar --untardir .
helm pull grafana-community/grafana --untar --untardir .

# Istio is split across three charts.  We rename the untarred
# directories with an `istio-` prefix so it's obvious what `base`
# and `gateway` belong to when you `ls helm-charts/`.
helm pull istio/base    --untar --untardir . && mv base    istio-base
helm pull istio/istiod  --untar --untardir . && mv istiod  istio-istiod
helm pull istio/gateway --untar --untardir . && mv gateway istio-gateway
helm pull kiali/kiali-server --untar --untardir .

```
