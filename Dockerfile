FROM python:3.10-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy all project files
COPY . .

# Run the bot in unbuffered mode to see logs instantly in Railway
CMD ["python", "-u", "bot_main.py"]
