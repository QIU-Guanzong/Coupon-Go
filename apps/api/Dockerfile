FROM python:3.12-slim
WORKDIR /app
# Contract PDFs require a TrueType-outlined Chinese font.
RUN apt-get update && apt-get install -y --no-install-recommends fonts-wqy-zenhei && rm -rf /var/lib/apt/lists/*
# Install the pinned set, including the contract PDF dependencies.
COPY requirements.lock .
RUN pip install --no-cache-dir -r requirements.lock
COPY . .
ENV COUPON_GO_ENV=prod
EXPOSE 8000
CMD ["python", "-m", "app.serve"]
