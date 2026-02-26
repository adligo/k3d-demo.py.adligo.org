# Teardown

Or use the teardown script to remove all k3d clusters and orphaned containers at once:

```bash
bash bin/teardown.sh
```

This file contains instructions on how to tear down/destroy all of the Docker containers.


```bash
# Delete the Kafka Helm release
helm uninstall kafka

# Delete the HDFS Helm release
helm uninstall hadoop

# Stop the K3D cluster
k3d cluster stop demo

# Delete the K3D cluster
k3d cluster delete demo
```

