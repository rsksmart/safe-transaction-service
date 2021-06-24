#! /bin/bash

# It uses docker to generated the DJANGO_SECRET_KEY using the python secrets package.

DJANGO_SECRET_KEY=$(docker run --rm python:3.9.3-slim-buster bash -c "python3 -c 'import secrets; print(secrets.token_hex(50))'")

read -p "ETHEREUM_NODE_URL (e.g., http://172.17.0.1:4444):" ETHEREUM_NODE_URL
read -p "ETHEREUM_TRACING_NODE_URL (e.g., http://172.17.0.1:4444):" ETHEREUM_TRACING_NODE_URL  

# From env.tracing.sample, we set the following variables:
# DJANGO_SECRET_KEY=$DJANGO_SECRET_KEY
# ETHEREUM_NODE_URL=$ETHEREUM_NODE_URL
# ETHEREUM_TRACING_NODE_URL=$ETHEREUM_TRACING_NODE_URL
# ETH_INTERNAL_NO_FILTER=1

cat << EOF > .env
PYTHONPATH=/app/safe_transaction_service
DJANGO_SETTINGS_MODULE=config.settings.production
DJANGO_SECRET_KEY=$DJANGO_SECRET_KEY
C_FORCE_ROOT=true
DEBUG=0
DATABASE_URL=psql://postgres:postgres@db:5432/postgres
ETHEREUM_NODE_URL=$ETHEREUM_NODE_URL
ETHEREUM_TRACING_NODE_URL=$ETHEREUM_TRACING_NODE_URL
ETH_L2_NETWORK=0
REDIS_URL=redis://redis:6379/0
CELERY_BROKER_URL=redis://redis:6379/1
ETH_INTERNAL_NO_FILTER=1
EOF
