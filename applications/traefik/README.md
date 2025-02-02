# Traefik
Traefik is responsible for routing traffic to the appropriate service. It is a reverse proxy that is able to route traffic based on the domain name. This allows us to have multiple services running on the same server and have them all be accessible through the same port.

This configuration depends on the fact that k3s deploys traefik as the default ingress controller using the helm chart.

## Usage

### Deploy the main configuration file:
`kubectl apply -f applications/traefik/traefik.yaml`

### Deploy the secrets:
`sops -d applications/traefik/secrets.yaml | kubectl apply -f -`

### Deleting the deployment
`kubectl delete -f applications/traefik/traefik.yaml`