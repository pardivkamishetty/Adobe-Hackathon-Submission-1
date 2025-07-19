FROM --platform=linux/amd64 python:3.10

WORKDIR /app

# Install required dependencies
# Note: scikit-learn is required for TF-IDF vectorization and cosine similarity
RUN pip install --no-cache-dir pdfplumber scikit-learn

# Copy the processing script
COPY process_persona.py .

# Set environment variables
ENV DOCKER_ENV=1
ENV PYTHONUNBUFFERED=1

# Create directories for input and output
RUN mkdir -p /app/Challenge_1b

# Set proper permissions
RUN chmod +x process_persona.py

# Run the script
CMD ["python", "process_persona.py"]
