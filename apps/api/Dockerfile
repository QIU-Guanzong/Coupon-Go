FROM python:3.12-slim
WORKDIR /app
# Stage-0: install the pinned set, not the loose >= requirements.
COPY requirements.lock .
RUN pip install --no-cache-dir -r requirements.lock
COPY . .
ENV COUPON_GO_ENV=prod
EXPOSE 8000
CMD ["python", "-m", "app.serve"]
