FROM python:3.12-slim

WORKDIR /app

# Chỉ cài đúng 3 thư viện nhẹ phục vụ Web Portal
RUN pip install --no-cache-dir fastapi uvicorn httpx

# Copy máy chủ đám mây và toàn bộ giao diện Rich Chat đã có sẵn
COPY cloud_server.py .
COPY src/body/richchat ./src/body/richchat

ENV PORT=8080
EXPOSE 8080

CMD ["python", "cloud_server.py"]
