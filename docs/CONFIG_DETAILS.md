# Configuration Notes

The file `hdfs-values.yaml` contains the Helm values used for this demo. Key settings:

| Setting | Value | Description |
|---|---|---|
| DataNode replicas | 2 | Number of HDFS DataNodes |
| NodeManager replicas | 1 | Number of YARN NodeManagers |
| Persistence | disabled | Data is ephemeral (demo only) |
| Anti-affinity | soft | Allows pods to co-locate on a single node |
| Memory limits | 512Mi | Reduced from defaults for lightweight demo |
| WebHDFS | enabled | REST API access to HDFS |

The file `kafka-values.yaml` contains the Helm values for Kafka. Key settings:

| Setting | Value | Description |
|---|---|---|
| Image repository | bitnamilegacy/kafka | Workaround for removed `bitnami/kafka` images |
| Controller replicas | 3 | Combined controller+broker nodes (KRaft mode) |
| Resource preset | micro | 250m CPU, 256Mi memory per node |
| Persistence | disabled | Data is ephemeral (demo only) |
| Listeners | PLAINTEXT | No SASL/TLS auth (demo only) |
| Provisioning | disabled | Topics are created manually after pods are Running |