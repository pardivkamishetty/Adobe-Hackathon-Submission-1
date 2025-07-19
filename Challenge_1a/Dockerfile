FROM --platform=linux/amd64 python:3.10

WORKDIR /app

# Install required dependencies
# Note: warnings, logging, sys, contextlib, io are part of Python standard library
RUN pip install --no-cache-dir pdfplumber jsonschema

# Copy the processing script and schema
COPY process_pdfs.py .
COPY sample_dataset/schema/output_schema.json ./schema/

# Set environment variables
ENV DOCKER_ENV=1
ENV PYTHONUNBUFFERED=1

# Create directories for input and output
RUN mkdir -p /app/input /app/output

# Set proper permissions
RUN chmod +x process_pdfs.py

# Run the script
CMD ["python", "process_pdfs.py"] 