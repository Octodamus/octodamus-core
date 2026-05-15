FROM python:3.11-slim
WORKDIR /app
COPY server.py LICENSE README.md ./
RUN pip install --no-cache-dir fastmcp pydantic
ENTRYPOINT ["python", "server.py"]
