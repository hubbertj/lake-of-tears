variable "truenas_host" {
  type    = string
  default = "10.0.0.160"
}

variable "truenas_password" {
  type      = string
  sensitive = true
}

variable "zfs_pool" {
  type    = string
  default = "WB-RAID-Z-18TB"
}

variable "snapshot_retention_days" {
  type    = number
  default = 30
}

# MinIO CE endpoint — used only by the aminueza/minio provider to create the
# datalake bucket.  Must point to the VM where MinIO CE is running.
# Apply terraform/services first, then re-apply foundation to register the bucket.
variable "aistor_endpoint" {
  type    = string
  default = "10.0.0.60:9000"
}

variable "aistor_access_key" {
  type      = string
  sensitive = true
}

variable "aistor_secret_key" {
  type      = string
  sensitive = true
}
