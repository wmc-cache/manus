#!/bin/bash
cd /home/ubuntu/manus-mvp/backend
export PYTHONPATH=/home/ubuntu/manus-mvp/backend
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
