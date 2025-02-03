# Backups

Backups are sent to a hetzner storage box over WebDAV. As of now, the backups are not encrypted.
Ideally we would use the tried and true SMB plugin, but the TAMU network seems to be blocking connecting to
the storage box over SMB.

## Usage

Install the csi-driver-webdav plugin
```bash
kubectl apply -f applications/backups/driver
```

Install the backup StorageClass
```bash
kubectl apply -f applications/backups/hetzner.yaml
```

Deploy the secret for the storage box
```bash
sops -d applications/backups/secrets.yaml | kubectl apply -f -
```

Now any PVCs that are created with the `storageClassName: backup-webdav` will be connected to the storage box.
An example is available in the `applications/backups/test.yaml` file.

NOTE: it seems that it takes some time for the volumes to sync with the storage box. Further investigation is needed.