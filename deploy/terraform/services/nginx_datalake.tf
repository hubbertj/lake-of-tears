resource "null_resource" "nginx_datalake" {
  depends_on = [null_resource.superset, null_resource.jupyter, null_resource.airflow]

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
      <<-EOF
      cat <<'NGINXCONF' > /tmp/datalake_nginx.conf
      server {
          listen 8443 ssl;
          server_name ${var.vm_host};
          ssl_certificate     /etc/ssl/openclaw/cert.pem;
          ssl_certificate_key /etc/ssl/openclaw/key.pem;
          ssl_protocols       TLSv1.2 TLSv1.3;
          location / {
              proxy_pass http://localhost:8088;
              proxy_set_header Host $host;
              proxy_set_header X-Real-IP $remote_addr;
              proxy_set_header X-Forwarded-Proto https;
          }
      }
      server {
          listen 8444 ssl;
          server_name ${var.vm_host};
          ssl_certificate     /etc/ssl/openclaw/cert.pem;
          ssl_certificate_key /etc/ssl/openclaw/key.pem;
          ssl_protocols       TLSv1.2 TLSv1.3;
          location / {
              proxy_pass http://localhost:8888;
              proxy_http_version 1.1;
              proxy_set_header Upgrade $http_upgrade;
              proxy_set_header Connection "upgrade";
              proxy_set_header Host $host;
              proxy_read_timeout 86400;
          }
      }
      server {
          listen 8445 ssl;
          server_name ${var.vm_host};
          ssl_certificate     /etc/ssl/openclaw/cert.pem;
          ssl_certificate_key /etc/ssl/openclaw/key.pem;
          ssl_protocols       TLSv1.2 TLSv1.3;
          location / {
              proxy_pass http://localhost:8080;
              proxy_set_header Host $host;
              proxy_set_header X-Real-IP $remote_addr;
              proxy_set_header X-Forwarded-Proto https;
          }
      }
      NGINXCONF
      echo '${var.vm_password}' | sudo -S cp /tmp/datalake_nginx.conf /etc/nginx/sites-available/datalake
      rm /tmp/datalake_nginx.conf
      echo '${var.vm_password}' | sudo -S ln -sf /etc/nginx/sites-available/datalake /etc/nginx/sites-enabled/datalake
      echo '${var.vm_password}' | sudo -S nginx -t
      echo '${var.vm_password}' | sudo -S systemctl reload nginx
      EOF
    ]
  }

  provisioner "remote-exec" {
    when       = destroy
    on_failure = continue
    connection {
      type     = "ssh"
      host     = self.triggers.vm_host
      user     = self.triggers.vm_user
      password = self.triggers.vm_password
    }
    inline = [
      "echo '${self.triggers.vm_password}' | sudo -S rm -f /etc/nginx/sites-enabled/datalake /etc/nginx/sites-available/datalake",
      "echo '${self.triggers.vm_password}' | sudo -S systemctl reload nginx",
    ]
  }
}
