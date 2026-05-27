resource "null_resource" "datalake_dataset" {
  triggers = {
    pool = var.zfs_pool
  }

  # local-exec + SSH heredoc avoids TrueNAS /tmp noexec restriction
  provisioner "local-exec" {
    interpreter = ["/bin/bash", "-c"]
    command     = <<-SSHCMD
      sshpass -p '${var.truenas_password}' \
        ssh -o StrictHostKeyChecking=no root@${var.truenas_host} sh -s <<'SHEOF'
      zfs list ${var.zfs_pool}/datalake 2>/dev/null \
        || zfs create -o mountpoint=/mnt/${var.zfs_pool}/datalake ${var.zfs_pool}/datalake
      mkdir -p /mnt/${var.zfs_pool}/datalake/raw \
               /mnt/${var.zfs_pool}/datalake/processed \
               /mnt/${var.zfs_pool}/datalake/analytics \
               /mnt/${var.zfs_pool}/datalake/embeddings
      chmod 770 /mnt/${var.zfs_pool}/datalake/raw \
                /mnt/${var.zfs_pool}/datalake/processed \
                /mnt/${var.zfs_pool}/datalake/analytics \
                /mnt/${var.zfs_pool}/datalake/embeddings
      chown -R 568:568 /mnt/${var.zfs_pool}/datalake/
      echo "ZFS dataset and directories ready."
      SHEOF
    SSHCMD
  }

  lifecycle {
    prevent_destroy = true
  }
}
