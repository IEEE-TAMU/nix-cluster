{
  cluster-node = import ./cluster-node.nix;
  haproxy = import ./haproxy.nix;
  k3s = import ./k3s.nix;
  keepalived = import ./keepalived.nix;
}
