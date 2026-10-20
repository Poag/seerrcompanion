FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 DATA_DIR=/config
WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir .
EXPOSE 8080
VOLUME /config
CMD ["seerrcompanion"]
