# Cloudflared
Cloudflared is a tunneling software that allows you to expose services running on your local machine to the internet. It is used to expose http services running on the cluster to the internet.

## Usage

### Deploy the main configuration file:
`kubectl apply -f applications/cloudflared/cloudflared.yaml`

### Deploy the secrets:
`sops -d applications/cloudflared/secrets.yaml | kubectl apply -f -`

### Deleting the deployment
`kubectl delete namespace cloudflared`

## Configuration
The configuration for the tunnel is stored in the `applications/cloudflared/cloudflared.yaml` file. The config is currently just a hello world that is exposed at https://dev.ieeetamu.org (connected to Caleb's cloudflare account).

In the future, this tunnel should point to the ingress controller which will route traffic to the correct service.