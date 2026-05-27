output "superset_url" {
  value = "https://${var.vm_host}:8443"
}

output "jupyter_url" {
  value = "https://${var.vm_host}:8444"
}
