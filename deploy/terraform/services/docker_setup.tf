resource "null_resource" "docker_setup" {
  triggers = {
    vm_host     = var.vm_host
    vm_user     = var.vm_user
    vm_password = var.vm_password
  }

  connection {
    type     = "ssh"
    host     = self.triggers.vm_host
    user     = self.triggers.vm_user
    password = self.triggers.vm_password
  }

  provisioner "remote-exec" {
    inline = [
      "command -v docker &>/dev/null && echo 'Docker already installed' || (echo '${var.vm_password}' | sudo -S apt-get install -y docker.io -qq)",
      "echo '${var.vm_password}' | sudo -S usermod -aG docker ${var.vm_user}",
      "echo '${var.vm_password}' | sudo -S systemctl enable --now docker",
    ]
  }
}
