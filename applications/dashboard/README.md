# Kubernetes Dashboard

## Add kubernetes-dashboard repository
`helm repo add kubernetes-dashboard https://kubernetes.github.io/dashboard/`
## Deploy a Helm Release named "kubernetes-dashboard" using the kubernetes-dashboard chart
`helm upgrade --install kubernetes-dashboard kubernetes-dashboard/kubernetes-dashboard --create-namespace --namespace kubernetes-dashboard`
## Enable the auth-user
`kubectl apply -f applications/dashboard/dashboard-adminuser.yaml`
## Get the token
`kubectl -n kubernetes-dashboard create token admin-user`
## Port Forward
`kubectl -n kubernetes-dashboard port-forward svc/kubernetes-dashboard-kong-proxy 8443:443`  
Allows access to the dashboard at https://localhost:8443
