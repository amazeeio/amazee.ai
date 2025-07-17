# Troubleshooting Guide

This guide helps you diagnose and resolve common issues with amazee.ai deployments and usage.

## Quick Diagnosis

### Health Check Commands

```bash
# Check if all services are running
docker-compose ps

# Check service health
curl http://localhost:8800/health
curl http://localhost:3000
curl http://localhost:4000/health/liveliness

# Check logs for errors
docker-compose logs --tail=50
```

### Common Error Patterns

| Symptom | Likely Cause | Solution |
|---------|-------------|----------|
| 502 Bad Gateway | Service not running | Check service status and logs |
| 401 Unauthorized | Authentication issue | Verify credentials and tokens |
| 500 Internal Server Error | Application error | Check application logs |
| Connection refused | Network/port issue | Verify ports and firewall |

## Installation Issues

### Docker Installation Problems

**Problem**: Docker service not starting
```bash
# Check Docker status
sudo systemctl status docker

# Start Docker service
sudo systemctl start docker

# Enable Docker on boot
sudo systemctl enable docker
```

**Problem**: Permission denied for Docker
```bash
# Add user to docker group
sudo usermod -aG docker $USER

# Log out and back in, or run:
newgrp docker
```

**Problem**: Docker Compose not found
```bash
# Install Docker Compose
sudo curl -L "https://github.com/docker/compose/releases/latest/download/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
sudo chmod +x /usr/local/bin/docker-compose
```

### Terraform Issues

**Problem**: Terraform AWS credentials not configured
```bash
# Configure AWS credentials
aws configure

# Or set environment variables
export AWS_ACCESS_KEY_ID=your-access-key
export AWS_SECRET_ACCESS_KEY=your-secret-key
export AWS_DEFAULT_REGION=us-east-1
```

**Problem**: Terraform state locked
```bash
# Force unlock state (use with caution)
terraform force-unlock LOCK_ID

# Or remove lock file manually
rm .terraform.tfstate.lock.info
```

**Problem**: Terraform resources already exist
```bash
# Import existing resources
terraform import aws_iam_role.amazeeai_send_email role-name

# Or destroy and recreate (use with caution)
terraform destroy
terraform apply
```

## Database Issues

### PostgreSQL Connection Problems

**Problem**: Database connection refused
```bash
# Check if PostgreSQL is running
docker-compose ps postgres

# Check PostgreSQL logs
docker-compose logs postgres

# Verify connection string
echo $DATABASE_URL
```

**Problem**: pgvector extension not found
```sql
-- Connect to database and install extension
CREATE EXTENSION IF NOT EXISTS vector;

-- Verify installation
SELECT * FROM pg_extension WHERE extname = 'vector';
```

**Problem**: Database migration errors
```bash
# Check migration status
python scripts/manage_migrations.py current

# Reset migrations (use with caution)
python scripts/manage_migrations.py stamp head
python scripts/manage_migrations.py upgrade
```

### Database Performance Issues

**Problem**: Slow queries
```sql
-- Check for long-running queries
SELECT pid, now() - pg_stat_activity.query_start AS duration, query
FROM pg_stat_activity
WHERE (now() - pg_stat_activity.query_start) > interval '5 minutes';

-- Check table sizes
SELECT schemaname, tablename, attname, n_distinct, correlation
FROM pg_stats
WHERE tablename = 'your_table_name';
```

**Problem**: Connection pool exhausted
```bash
# Check current connections
SELECT count(*) FROM pg_stat_activity;

# Increase max_connections in PostgreSQL config
# Add to postgresql.conf:
max_connections = 200
```

## Authentication Issues

### Login Problems

**Problem**: Invalid credentials error
```bash
# Check user exists
curl -X GET "http://localhost:8800/users/search?email=user@example.com" \
  -H "Authorization: Bearer admin-token"

# Reset password (admin only)
# Use the admin interface or API to reset user password
```

**Problem**: Token expired
```bash
# Re-authenticate to get new token
curl -X POST "http://localhost:8800/auth/login" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "username=user@example.com&password=password" \
  -c cookies.txt
```

**Problem**: CORS errors in browser
```bash
# Check CORS configuration
# Verify CORS_ORIGINS includes your frontend URL
# Check browser console for specific CORS errors
```

### Passwordless Authentication Issues

**Problem**: Email verification not received
```bash
# Check SES configuration
aws ses get-send-quota

# Verify sender email is verified
aws ses list-verified-email-addresses

# Check SES logs
aws logs describe-log-groups --log-group-name-prefix /aws/ses
```

**Problem**: Verification code invalid
```bash
# Check DynamoDB for stored codes
aws dynamodb scan --table-name verification-codes-dev

# Verify code expiration (TTL)
aws dynamodb describe-table --table-name verification-codes-dev
```

## API Issues

### Common API Errors

**Problem**: 400 Bad Request
```bash
# Check request format
curl -X POST "http://localhost:8800/private-ai-keys" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer your-token" \
  -d '{"name": "test", "region_id": 1}' \
  -v
```

**Problem**: 403 Forbidden
```bash
# Check user permissions
curl -X GET "http://localhost:8800/auth/me" \
  -H "Authorization: Bearer your-token"

# Verify user role and team membership
```

**Problem**: 404 Not Found
```bash
# Check if resource exists
curl -X GET "http://localhost:8800/private-ai-keys/1" \
  -H "Authorization: Bearer your-token"

# Verify URL and resource ID
```

### Rate Limiting Issues

**Problem**: 429 Too Many Requests
```bash
# Check rate limit headers
curl -I "http://localhost:8800/private-ai-keys" \
  -H "Authorization: Bearer your-token"

# Wait for rate limit reset or implement exponential backoff
```

## LiteLLM Issues

### LiteLLM Service Problems

**Problem**: LiteLLM service not responding
```bash
# Check LiteLLM status
curl http://localhost:4000/health/liveliness

# Check LiteLLM logs
docker-compose logs litellm

# Verify LiteLLM database connection
docker-compose exec litellm_db psql -U llmproxy -d litellm -c "SELECT 1;"
```

**Problem**: Model not available
```bash
# Check available models
curl -H "Authorization: Bearer sk-1234" \
  http://localhost:4000/models

# Add model to LiteLLM
curl -X POST "http://localhost:4000/model/new" \
  -H "Authorization: Bearer sk-1234" \
  -H "Content-Type: application/json" \
  -d '{
    "model_name": "gpt-4",
    "litellm_params": {
      "model": "gpt-4",
      "api_key": "sk-...",
      "api_base": "https://api.openai.com/v1"
    }
  }'
```

**Problem**: API key authentication failed
```bash
# Verify LiteLLM token
curl -H "Authorization: Bearer your-litellm-token" \
  http://localhost:4000/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model": "gpt-4", "messages": [{"role": "user", "content": "test"}]}'
```

## AWS Service Issues

### DynamoDB Problems

**Problem**: DynamoDB access denied
```bash
# Check IAM role permissions
aws iam get-role --role-name amazeeai-ddb-dev

# Test DynamoDB access
aws dynamodb list-tables --region eu-central-2

# Verify role assumption
aws sts assume-role --role-arn arn:aws:iam::account:role/amazeeai-ddb-dev --role-session-name test
```

**Problem**: DynamoDB table not found
```bash
# List DynamoDB tables
aws dynamodb list-tables --region eu-central-2

# Create missing table
aws dynamodb create-table \
  --table-name verification-codes-dev \
  --attribute-definitions AttributeName=email,AttributeType=S \
  --key-schema AttributeName=email,KeyType=HASH \
  --billing-mode PAY_PER_REQUEST \
  --region eu-central-2
```

### SES Problems

**Problem**: Email sending failed
```bash
# Check SES sending quota
aws ses get-send-quota

# Verify sender email
aws ses list-verified-email-addresses

# Check SES sending statistics
aws ses get-send-statistics
```

**Problem**: Email template not found
```bash
# List email templates
aws ses list-email-templates

# Create missing template
aws ses create-email-template \
  --template-name team-expiring \
  --template-subject "Your trial is expiring" \
  --template-text "Your trial expires in {{days}} days"
```

## Frontend Issues

### Next.js Problems

**Problem**: Frontend not loading
```bash
# Check frontend logs
docker-compose logs frontend

# Verify environment variables
docker-compose exec frontend env | grep NEXT_PUBLIC

# Check build process
docker-compose exec frontend npm run build
```

**Problem**: API calls failing
```bash
# Check browser network tab for errors
# Verify NEXT_PUBLIC_API_URL is correct
# Check CORS configuration
```

**Problem**: Authentication state issues
```bash
# Clear browser storage
# Check for token expiration
# Verify cookie settings
```

## Monitoring Issues

### Prometheus Problems

**Problem**: Metrics not collecting
```bash
# Check Prometheus status
curl http://localhost:9090/-/healthy

# Check targets
curl http://localhost:9090/api/v1/targets

# Verify scrape configuration
docker-compose exec prometheus cat /etc/prometheus/prometheus.yml
```

**Problem**: Grafana not loading dashboards
```bash
# Check Grafana logs
docker-compose logs grafana

# Verify Prometheus data source
# Check dashboard provisioning
```

## Performance Issues

### Slow Response Times

**Problem**: High API response times
```bash
# Check database performance
docker-compose exec postgres psql -U postgres -c "
SELECT query, mean_time, calls
FROM pg_stat_statements
ORDER BY mean_time DESC
LIMIT 10;"

# Check application logs for slow queries
docker-compose logs backend | grep "slow query"
```

**Problem**: High memory usage
```bash
# Check container resource usage
docker stats

# Check memory usage by service
docker-compose exec backend ps aux --sort=-%mem | head -10
```

**Problem**: High CPU usage
```bash
# Check CPU usage by process
docker-compose exec backend top

# Check for infinite loops or heavy computations
docker-compose logs backend | grep -i "cpu\|performance"
```

## Network Issues

### Connectivity Problems

**Problem**: Services can't communicate
```bash
# Check Docker network
docker network ls
docker network inspect amazeeai_default

# Test inter-service communication
docker-compose exec backend ping postgres
docker-compose exec backend ping litellm
```

**Problem**: Port conflicts
```bash
# Check port usage
sudo netstat -tulpn | grep :8800
sudo netstat -tulpn | grep :3000

# Change ports in docker-compose.yml if needed
```

## Security Issues

### SSL/TLS Problems

**Problem**: SSL certificate errors
```bash
# Check certificate validity
openssl x509 -in cert.pem -text -noout

# Verify certificate chain
openssl verify cert.pem

# Check certificate expiration
openssl x509 -in cert.pem -noout -dates
```

**Problem**: CORS policy violations
```bash
# Check CORS configuration in backend
# Verify allowed origins include your domain
# Check browser console for specific CORS errors
```

## Recovery Procedures

### Database Recovery

**Problem**: Database corruption
```bash
# Stop services
docker-compose down

# Backup current data
docker-compose exec postgres pg_dump -U postgres postgres_service > backup.sql

# Restore from backup
docker-compose exec -T postgres psql -U postgres postgres_service < backup.sql

# Restart services
docker-compose up -d
```

**Problem**: Data loss
```bash
# Check for recent backups
ls -la backups/

# Restore from latest backup
pg_restore -h localhost -U postgres -d postgres_service backup_file.dump
```

### Service Recovery

**Problem**: Service won't start
```bash
# Check service dependencies
docker-compose config

# Start services in order
docker-compose up -d postgres
docker-compose up -d backend
docker-compose up -d frontend

# Check for dependency issues
docker-compose logs
```

## Debugging Techniques

### Log Analysis

```bash
# Follow logs in real-time
docker-compose logs -f

# Filter logs by service
docker-compose logs -f backend | grep ERROR

# Search for specific patterns
docker-compose logs | grep -i "exception\|error\|failed"
```

### Debug Mode

```bash
# Enable debug logging
export LOG_LEVEL=DEBUG

# Restart services with debug
docker-compose down
docker-compose up -d

# Check debug logs
docker-compose logs backend | grep DEBUG
```

### Network Debugging

```bash
# Test network connectivity
docker-compose exec backend curl -v http://postgres:5432
docker-compose exec backend curl -v http://litellm:4000/health

# Check DNS resolution
docker-compose exec backend nslookup postgres
```

## Getting Help

### Information to Collect

When reporting issues, include:

1. **Environment Details**:
   - Operating system and version
   - Docker version
   - amazee.ai version
   - Deployment method (Docker Compose, Kubernetes, etc.)

2. **Error Messages**:
   - Complete error logs
   - Stack traces
   - HTTP status codes

3. **Configuration**:
   - Environment variables (without sensitive data)
   - Configuration files
   - Network setup

4. **Steps to Reproduce**:
   - Exact commands run
   - Sequence of actions
   - Expected vs actual behavior

### Support Channels

1. **Documentation**: Check this guide and other documentation
2. **Logs**: Review service logs for error messages
3. **Community**: Check GitHub issues and discussions
4. **Professional Support**: Contact your system administrator

### Common Solutions

| Issue | Quick Fix |
|-------|-----------|
| Service not starting | Check logs, verify dependencies |
| Database connection failed | Verify DATABASE_URL, check PostgreSQL status |
| Authentication errors | Check credentials, verify token expiration |
| API errors | Check request format, verify permissions |
| Performance issues | Monitor resources, check for bottlenecks |

This troubleshooting guide should help you resolve most common issues. If you continue to experience problems, collect the information listed above and seek additional support.