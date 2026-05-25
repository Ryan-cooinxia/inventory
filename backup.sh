#!/bin/bash
BACKUP_DIR="/home/ubuntu/inventory/backups"
mkdir -p "$BACKUP_DIR"
cp /home/ubuntu/inventory/data.db "$BACKUP_DIR/data_$(date +\%Y\%m\%d).db"
# 删除7天前的旧备份
find "$BACKUP_DIR" -name "data_*.db" -mtime +7 -delete
