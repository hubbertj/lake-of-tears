output "minio_endpoint" {
  value       = var.aistor_endpoint
  description = "MinIO CE S3 API endpoint (VM:9000)"
}

output "datalake_dataset_path" {
  value       = "/mnt/${var.zfs_pool}/datalake"
  description = "ZFS dataset path on TrueNAS (NFS-exported to VM)"
}

output "nfs_export" {
  value       = "${var.truenas_host}:/mnt/${var.zfs_pool}/datalake"
  description = "NFS export path (use 172.16.100.1 from the VM, 10.0.0.160 from LAN)"
}
