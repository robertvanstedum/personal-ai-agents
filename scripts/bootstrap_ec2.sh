#!/bin/bash
# One-time EC2 setup. Run as ec2-user on a fresh Amazon Linux 2023 instance.
# Must have minimoi-ec2-role attached before running.
set -euo pipefail

ACCOUNT_ID="332704997792"
REGION="us-east-1"

echo "=== Installing Docker ==="
sudo yum install -y docker
sudo systemctl enable docker
sudo systemctl start docker
sudo usermod -aG docker ec2-user

echo "=== Installing Docker Compose ==="
COMPOSE_VERSION=$(curl -s https://api.github.com/repos/docker/compose/releases/latest | grep '"tag_name"' | cut -d'"' -f4)
sudo curl -L "https://github.com/docker/compose/releases/download/${COMPOSE_VERSION}/docker-compose-$(uname -s)-$(uname -m)" \
  -o /usr/local/bin/docker-compose
sudo chmod +x /usr/local/bin/docker-compose

echo "=== Installing Nginx and Certbot ==="
sudo yum install -y nginx
sudo systemctl enable nginx
sudo pip3 install certbot certbot-nginx

echo "=== Creating data directories ==="
sudo mkdir -p /opt/minimoi/data/german
sudo mkdir -p /opt/minimoi/logs
sudo chown -R ec2-user:ec2-user /opt/minimoi

echo "=== Cloning repo ==="
# Pull just the production scripts and compose file (no source needed — using ECR images)
git clone https://github.com/robertvanstedum/personal-ai-agents.git /opt/minimoi/repo || \
  git -C /opt/minimoi/repo pull

echo "=== Logging in to ECR ==="
aws ecr get-login-password --region ${REGION} | \
  docker login --username AWS --password-stdin \
  ${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com

echo ""
echo "=== Bootstrap complete ==="
echo "Next steps:"
echo "  1. Copy /opt/minimoi/.env (non-secret config only — secrets come from SSM)"
echo "  2. Run: scripts/sync_data.sh <your-mac-ip> to push data files"
echo "  3. Obtain SSL cert: sudo certbot --nginx -d app.minimoi.ai"
echo "  4. Copy infrastructure/nginx/minimoi.conf to /etc/nginx/conf.d/ and reload"
echo "  5. Run: bash /opt/minimoi/repo/scripts/deploy.sh"
