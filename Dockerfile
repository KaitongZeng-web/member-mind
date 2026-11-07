FROM python:3.13-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY backend backend
COPY config config
COPY frontend frontend
COPY run.py sample_mcp_server.py ./
ENV APP_HOST=0.0.0.0 APP_PORT=8877 APP_DB_PATH=/app/data/workspace.sqlite3
VOLUME ["/app/data"]
EXPOSE 8877
CMD ["python", "run.py"]
