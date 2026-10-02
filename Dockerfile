FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY run.py .
COPY tvmonitor ./tvmonitor
ENV TVMONITOR_HOST=0.0.0.0 TVMONITOR_PORT=9494 TVMONITOR_DATA=/data PYTHONUNBUFFERED=1
VOLUME /data
EXPOSE 9494
CMD ["python", "run.py"]
