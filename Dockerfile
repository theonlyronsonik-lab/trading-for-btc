# Use a lightweight Python image
FROM python:3.10-slim

# Set the working directory
WORKDIR /app

# Copy requirements and install them (Fixes the missing numpy error)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of your code (main.py, telegram_notifier.py, etc.)
COPY . .

# Run the bot
CMD ["python", "main.py"]
