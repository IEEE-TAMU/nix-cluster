# ContainerSSH
ContainerSSH allows users to log into an ephemeral container using SSH.

Based on https://github.com/ContainerSSH/examples/blob/main/quick-start/kubernetes.yaml

## Usage

Deploy the main configuration file:
```bash
kubectl apply -f applications/containerssh/containerssh.yaml
```

Deploy the secrets:
```bash
sops -d applications/containerssh/secrets.yaml | kubectl apply -f -
```

to delete the deployment you can simply delete the containerssh and containerssh-guests namespaces:
```bash
kubectl delete namespace containerssh containerssh-guests
```