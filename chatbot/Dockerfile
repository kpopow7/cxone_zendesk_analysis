FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements-chatbot.txt .
RUN pip install --no-cache-dir -r requirements-chatbot.txt

COPY src/ /app/src/
COPY chatbot/ /app/chatbot/
COPY config/ /app/config/

ENV PYTHONPATH=/app/src

EXPOSE 7860

CMD ["python", "chatbot/app.py"]
