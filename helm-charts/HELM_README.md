# Helm Charts

I have noticed that helm charts disappear from time to time from the web, so this directory was created with the following commands;

```
helm repo add pfisterer-hadoop https://pfisterer.github.io/apache-hadoop-helm/
helm repo add bitnami https://charts.bitnami.com/bitnami
helm repo update

helm pull pfisterer-hadoop/hadoop --untar --untardir .
helm pull bitnami/kafka  --untar --untardir .

```
