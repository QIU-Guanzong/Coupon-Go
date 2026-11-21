FROM python:3.12-slim
WORKDIR /app
# P1-D item 2: contract PDFs need a CJK font -- without one, signing
# answers 503 instead of rendering an ASCII-only fallback.
RUN apt-get update && apt-get install -y --no-install-recommends fonts-wqy-zenhei && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV COUPON_GO_ENV=prod
EXPOSE 8000
CMD ["python", "-m", "app.serve"]
