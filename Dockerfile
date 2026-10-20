FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY agentwatch ./agentwatch
COPY frontend ./frontend
EXPOSE 8000
CMD ["uvicorn", "agentwatch.server:app", "--host", "0.0.0.0", "--port", "8000"]
