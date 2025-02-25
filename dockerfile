# Use Python 3.12 official image
FROM python:3.12

# Set the working directory inside the container
WORKDIR /app

# Copy all project files to the container
COPY . .

RUN chmod a+x worker.sh

# Install dependencies from requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Expose port 5000 for Flask
EXPOSE 5001
EXPOSE 8001
EXPOSE 4040

# Run the Flask application
#CMD ["python", "test.py"]
RUN python -m spacy download en_core_web_sm

CMD [ "./worker.sh","--host=0.0.0.0" ]