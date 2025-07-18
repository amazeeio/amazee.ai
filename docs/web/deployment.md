# Deployment Guide

This guide covers production deployment strategies for amazee.ai, including Docker, Kubernetes, cloud platforms, and best practices for scaling and monitoring.

## Deployment Options

### 1. Kubernetes (Recommended for Production)

Best for production deployments with high availability requirements and enterprise needs.

### 2. Cloud Platform Deployment

Deploy directly to cloud platforms like AWS, Google Cloud, or Azure for managed production environments.



## Production Deployment

### Kubernetes Deployment (Recommended)

For production deployments, Kubernetes provides the best combination of scalability, reliability, and manageability.

#### Prerequisites

- Kubernetes cluster (1.20+)
- Helm (3.0+)
- kubectl configured
- Ingress controller (nginx-ingress recommended)

### Helm Chart Structure

The Helm chart is located in the `helm/` directory:

```
helm/
├── charts/
│   ├── frontend/
│   ├── backend/
│   └── litellm/
├── values.yaml
└── README.md
```

#### Installation

```bash
# Add the Helm repository
helm repo add amazee-ai https://charts.amazee.ai

# Install amazee.ai
helm install amazee-ai amazee-ai/amazee-ai \
  --namespace amazee-ai \
  --create-namespace \
  --values values.yaml
```

#### Custom Values

Create a `values.yaml` file:

```yaml
# Global configuration
global:
  environment: production
  domain: app.your-domain.com

# Database configuration
postgresql:
  enabled: true
  auth:
    username: amazee_user
    password: secure-password
    database: amazee_ai
  primary:
    persistence:
      enabled: true
      size: 100Gi

# Backend configuration
backend:
  replicaCount: 3
  resources:
    requests:
      memory: "512Mi"
      cpu: "250m"
    limits:
      memory: "1Gi"
      cpu: "500m"
  env:
    SECRET_KEY: your-secret-key
    DYNAMODB_ROLE_NAME: amazeeai-ddb-prod
    SES_ROLE_NAME: amazeeai-send-email-prod
    SES_SENDER_EMAIL: noreply@your-domain.com

# Frontend configuration
frontend:
  replicaCount: 2
  resources:
    requests:
      memory: "256Mi"
      cpu: "100m"
    limits:
      memory: "512Mi"
      cpu: "200m"
  env:
    NEXT_PUBLIC_API_URL: https://api.your-domain.com

# LiteLLM configuration
litellm:
  replicaCount: 2
  resources:
    requests:
      memory: "1Gi"
      cpu: "500m"
    limits:
      memory: "2Gi"
      cpu: "1000m"

# Ingress configuration
ingress:
  enabled: true
  className: nginx
  annotations:
    cert-manager.io/cluster-issuer: letsencrypt-prod
    nginx.ingress.kubernetes.io/rate-limit: "100"
  hosts:
    - host: app.your-domain.com
      paths:
        - path: /
          pathType: Prefix
  tls:
    - secretName: amazee-ai-tls
      hosts:
        - app.your-domain.com

# Monitoring
monitoring:
  prometheus:
    enabled: true
  grafana:
    enabled: true
    adminPassword: secure-password
```

#### Kubernetes Manifests

For manual deployment without Helm:

```yaml
# namespace.yaml
apiVersion: v1
kind: Namespace
metadata:
  name: amazee-ai

---
# configmap.yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: amazee-ai-config
  namespace: amazee-ai
data:
  DATABASE_URL: "postgresql://amazee_user:password@postgresql:5432/amazee_ai"
  SECRET_KEY: "your-secret-key"
  ENV_SUFFIX: "prod"

---
# secret.yaml
apiVersion: v1
kind: Secret
metadata:
  name: amazee-ai-secrets
  namespace: amazee-ai
type: Opaque
data:
  POSTGRES_PASSWORD: cGFzc3dvcmQ=  # base64 encoded
  LITELLM_MASTER_KEY: c2stMTIzNA==  # base64 encoded

---
# deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: amazee-ai-backend
  namespace: amazee-ai
spec:
  replicas: 3
  selector:
    matchLabels:
      app: amazee-ai-backend
  template:
    metadata:
      labels:
        app: amazee-ai-backend
    spec:
      containers:
      - name: backend
        image: amazee-ai/backend:latest
        ports:
        - containerPort: 8800
        envFrom:
        - configMapRef:
            name: amazee-ai-config
        - secretRef:
            name: amazee-ai-secrets
        resources:
          requests:
            memory: "512Mi"
            cpu: "250m"
          limits:
            memory: "1Gi"
            cpu: "500m"
        livenessProbe:
          httpGet:
            path: /health
            port: 8800
          initialDelaySeconds: 30
          periodSeconds: 10
        readinessProbe:
          httpGet:
            path: /health
            port: 8800
          initialDelaySeconds: 5
          periodSeconds: 5

---
# service.yaml
apiVersion: v1
kind: Service
metadata:
  name: amazee-ai-backend
  namespace: amazee-ai
spec:
  selector:
    app: amazee-ai-backend
  ports:
  - port: 80
    targetPort: 8800
  type: ClusterIP

---
# ingress.yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: amazee-ai-ingress
  namespace: amazee-ai
  annotations:
    nginx.ingress.kubernetes.io/rewrite-target: /
    cert-manager.io/cluster-issuer: letsencrypt-prod
spec:
  tls:
  - hosts:
    - app.your-domain.com
    secretName: amazee-ai-tls
  rules:
  - host: app.your-domain.com
    http:
      paths:
      - path: /
        pathType: Prefix
        backend:
          service:
            name: amazee-ai-frontend
            port:
              number: 80
      - path: /api
        pathType: Prefix
        backend:
          service:
            name: amazee-ai-backend
            port:
              number: 80
```

#### Horizontal Pod Autoscaler

```yaml
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: amazee-ai-backend-hpa
  namespace: amazee-ai
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: amazee-ai-backend
  minReplicas: 3
  maxReplicas: 10
  metrics:
  - type: Resource
    resource:
      name: cpu
      target:
        type: Utilization
        averageUtilization: 70
  - type: Resource
    resource:
      name: memory
      target:
        type: Utilization
        averageUtilization: 80
```

### Cloud Platform Deployment

For managed production environments, consider deploying directly to cloud platforms.

#### AWS Deployment

##### Using AWS ECS

```yaml
# task-definition.json
{
  "family": "amazee-ai",
  "networkMode": "awsvpc",
  "requiresCompatibilities": ["FARGATE"],
  "cpu": "1024",
  "memory": "2048",
  "executionRoleArn": "arn:aws:iam::account:role/ecsTaskExecutionRole",
  "taskRoleArn": "arn:aws:iam::account:role/amazee-ai-task-role",
  "containerDefinitions": [
    {
      "name": "backend",
      "image": "amazee-ai/backend:latest",
      "portMappings": [
        {
          "containerPort": 8800,
          "protocol": "tcp"
        }
      ],
      "environment": [
        {
          "name": "DATABASE_URL",
          "value": "postgresql://user:pass@rds-endpoint:5432/amazee_ai"
        }
      ],
      "secrets": [
        {
          "name": "SECRET_KEY",
          "valueFrom": "arn:aws:secretsmanager:region:account:secret:amazee-ai-secret-key"
        }
      ],
      "logConfiguration": {
        "logDriver": "awslogs",
        "options": {
          "awslogs-group": "/ecs/amazee-ai",
          "awslogs-region": "us-east-1",
          "awslogs-stream-prefix": "backend"
        }
      }
    }
  ]
}
```

##### Using AWS EKS

```bash
# Create EKS cluster
eksctl create cluster \
  --name amazee-ai \
  --region us-east-1 \
  --nodegroup-name standard-workers \
  --node-type t3.medium \
  --nodes 3 \
  --nodes-min 1 \
  --nodes-max 10 \
  --managed

# Deploy amazee.ai
helm install amazee-ai ./helm \
  --namespace amazee-ai \
  --create-namespace
```

#### Google Cloud Deployment

##### Using Google Cloud Run

```bash
# Build and push images
gcloud builds submit --tag gcr.io/PROJECT_ID/amazee-ai-backend
gcloud builds submit --tag gcr.io/PROJECT_ID/amazee-ai-frontend

# Deploy to Cloud Run
gcloud run deploy amazee-ai-backend \
  --image gcr.io/PROJECT_ID/amazee-ai-backend \
  --platform managed \
  --region us-central1 \
  --allow-unauthenticated \
  --set-env-vars DATABASE_URL=postgresql://user:pass@host:5432/db
```

##### Using Google Kubernetes Engine

```bash
# Create GKE cluster
gcloud container clusters create amazee-ai \
  --zone us-central1-a \
  --num-nodes 3 \
  --machine-type n1-standard-2

# Deploy amazee.ai
helm install amazee-ai ./helm \
  --namespace amazee-ai \
  --create-namespace
```

## Database Deployment

### External PostgreSQL (Production)

For production deployments, always use a managed PostgreSQL service:

#### AWS RDS

```bash
# Create RDS instance
aws rds create-db-instance \
  --db-instance-identifier amazee-ai-db \
  --db-instance-class db.t3.micro \
  --engine postgres \
  --master-username amazee_user \
  --master-user-password secure-password \
  --allocated-storage 20 \
  --storage-type gp2 \
  --backup-retention-period 7 \
  --multi-az \
  --vpc-security-group-ids sg-12345678
```

#### Google Cloud SQL

```bash
# Create Cloud SQL instance
gcloud sql instances create amazee-ai-db \
  --database-version=POSTGRES_16 \
  --tier=db-f1-micro \
  --region=us-central1 \
  --storage-type=SSD \
  --storage-size=10GB \
  --backup-start-time=02:00 \
  --enable-bin-log

# Create database
gcloud sql databases create amazee_ai --instance=amazee-ai-db

# Create user
gcloud sql users create amazee_user \
  --instance=amazee-ai-db \
  --password=secure-password
```

### Database Migrations

```bash
# Run migrations
python scripts/manage_migrations.py upgrade

# Or using Docker
docker run --rm \
  -e DATABASE_URL=postgresql://user:pass@host:5432/db \
  amazee-ai/backend \
  python scripts/manage_migrations.py upgrade
```

## Production Monitoring and Observability

### Prometheus Configuration

```yaml
# prometheus.yml
global:
  scrape_interval: 15s
  evaluation_interval: 15s

rule_files:
  - "alert_rules.yml"

alerting:
  alertmanagers:
    - static_configs:
        - targets:
          - alertmanager:9093

scrape_configs:
  - job_name: 'amazee-ai-backend'
    static_configs:
      - targets: ['backend:8800']
    metrics_path: '/metrics'
    scrape_interval: 10s

  - job_name: 'amazee-ai-frontend'
    static_configs:
      - targets: ['frontend:3000']
    metrics_path: '/metrics'

  - job_name: 'amazee-ai-litellm'
    static_configs:
      - targets: ['litellm:4000']
    metrics_path: '/metrics'
```

### Grafana Dashboards

Create dashboards for:

- **System Overview**: CPU, memory, disk usage
- **Application Metrics**: Request rates, response times, error rates
- **Database Metrics**: Connection count, query performance
- **Business Metrics**: User registrations, key creation, usage

### Alerting Rules

```yaml
# alert_rules.yml
groups:
  - name: amazee-ai
    rules:
      - alert: HighErrorRate
        expr: rate(http_requests_total{status=~"5.."}[5m]) > 0.1
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "High error rate detected"
          description: "Error rate is {{ $value }} errors per second"

      - alert: DatabaseConnectionHigh
        expr: pg_stat_activity_count > 80
        for: 2m
        labels:
          severity: warning
        annotations:
          summary: "High database connection count"
          description: "Database has {{ $value }} active connections"

      - alert: ServiceDown
        expr: up == 0
        for: 1m
        labels:
          severity: critical
        annotations:
          summary: "Service is down"
          description: "Service {{ $labels.instance }} is down"
```

## Backup and Disaster Recovery

### Database Backups

```bash
#!/bin/bash
# backup.sh
DATE=$(date +%Y%m%d_%H%M%S)
BACKUP_DIR="/backups"
DB_HOST="your-db-host"
DB_NAME="amazee_ai"
DB_USER="amazee_user"

# Create backup
pg_dump -h $DB_HOST -U $DB_USER -d $DB_NAME | gzip > $BACKUP_DIR/amazee_ai_$DATE.sql.gz

# Keep only last 7 days of backups
find $BACKUP_DIR -name "amazee_ai_*.sql.gz" -mtime +7 -delete
```

### Automated Backups with Cron

```bash
# Add to crontab
0 2 * * * /path/to/backup.sh
```

### Disaster Recovery Plan

1. **Regular Backups**: Daily database backups
2. **Configuration Backup**: Version control for configuration
3. **Documentation**: Recovery procedures documented
4. **Testing**: Regular disaster recovery drills

## Production Security Best Practices

### Network Security

- Use VPCs and security groups
- Implement network segmentation
- Use private subnets for databases
- Enable VPC flow logs

### Application Security

- Use HTTPS everywhere
- Implement proper CORS policies
- Rate limiting on all endpoints
- Input validation and sanitization

### Secrets Management

```bash
# Using AWS Secrets Manager
aws secretsmanager create-secret \
  --name amazee-ai-secrets \
  --description "Amazee AI application secrets" \
  --secret-string '{"SECRET_KEY":"your-secret","DATABASE_PASSWORD":"db-pass"}'

# Using Kubernetes Secrets
kubectl create secret generic amazee-ai-secrets \
  --from-literal=SECRET_KEY=your-secret \
  --from-literal=DATABASE_PASSWORD=db-pass
```

### SSL/TLS Configuration

```nginx
# Modern SSL configuration
ssl_protocols TLSv1.2 TLSv1.3;
ssl_ciphers ECDHE-RSA-AES256-GCM-SHA512:DHE-RSA-AES256-GCM-SHA512;
ssl_prefer_server_ciphers off;
ssl_session_cache shared:SSL:10m;
ssl_session_timeout 10m;
```

## Production Performance Optimization

### Application Optimization

- Enable connection pooling
- Use CDN for static assets
- Implement caching strategies
- Optimize database queries

### Infrastructure Optimization

- Use appropriate instance types
- Implement auto-scaling
- Use load balancers
- Monitor resource usage

### Database Optimization

```sql
-- Create indexes for better performance
CREATE INDEX idx_users_email ON users(email);
CREATE INDEX idx_private_ai_keys_owner_id ON private_ai_keys(owner_id);
CREATE INDEX idx_audit_logs_user_id ON audit_logs(user_id);

-- Configure connection pooling
ALTER SYSTEM SET max_connections = 200;
ALTER SYSTEM SET shared_buffers = '256MB';
ALTER SYSTEM SET effective_cache_size = '1GB';
```

## Production Scaling Strategies

### Horizontal Scaling

- Multiple application instances
- Load balancer distribution
- Database read replicas
- Cache layers

### Vertical Scaling

- Increase instance sizes
- Optimize application code
- Database tuning
- Resource monitoring

### Auto-scaling Configuration

```yaml
# Kubernetes HPA
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: amazee-ai-backend-hpa
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: amazee-ai-backend
  minReplicas: 3
  maxReplicas: 20
  metrics:
  - type: Resource
    resource:
      name: cpu
      target:
        type: Utilization
        averageUtilization: 70
```

## Production Maintenance and Updates

### Rolling Updates

```bash
# Kubernetes rolling update
kubectl set image deployment/amazee-ai-backend backend=amazee-ai/backend:v2.0.0
```

### Zero-Downtime Deployment

- Use rolling update strategies
- Health checks and readiness probes
- Blue-green deployments
- Canary deployments

### Monitoring During Updates

- Watch error rates
- Monitor response times
- Check resource usage
- Verify functionality

## Production Deployment Recommendations

### For Production Use

1. **Kubernetes**: Use Kubernetes for production deployments requiring high availability, scalability, and enterprise features
2. **Cloud Platforms**: Consider managed Kubernetes services (EKS, GKE, AKS) for reduced operational overhead
3. **Managed Databases**: Always use managed PostgreSQL services (RDS, Cloud SQL, etc.)
4. **Monitoring**: Implement comprehensive monitoring with Prometheus, Grafana, and alerting
5. **Security**: Follow security best practices including network segmentation, secrets management, and SSL/TLS

This deployment guide provides comprehensive coverage for production deployments. For development setup, refer to the [Development Guide](development.md).