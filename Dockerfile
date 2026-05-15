FROM python:3.11-slim
WORKDIR /app
COPY server.py ./
RUN pip install --no-cache-dir fastmcp pydantic
ENTRYPOINT ["python", "server.py"]
