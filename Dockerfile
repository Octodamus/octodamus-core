FROM python:3.11-slim
WORKDIR /app
COPY octo_mcp_server.py octo_personality.py ./
RUN pip install --no-cache-dir fastmcp pydantic
CMD ["python", "octo_mcp_server.py"]
